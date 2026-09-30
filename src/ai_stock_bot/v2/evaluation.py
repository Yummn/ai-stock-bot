from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
from io import StringIO
from pathlib import Path

import pandas as pd

from .backtest import load_histories
from .isolated import run_backtest
from .market import Market
from .schema import AppConfig, JobRequest
from .profile_selection import selection_mode
from .store import dumps


def strategy_key(config: AppConfig) -> str:
    config = AppConfig.model_validate(config.model_dump())
    # Only effective parameters identify a strategy; unrelated model edits do not.
    mode = selection_mode(config)
    profiles = ["aggressive", "balanced", "conservative"] if mode != "fixed" else [config.profile]
    data = {"engine": config.engine, "model": config.model if config.engine == "ml" else None,
            "profile": mode if mode != "fixed" else config.profile,
            "presets": {p: config.preset(p).model_dump() for p in profiles},
            "symbols": config.symbols, "risk": config.risk.model_dump(), "seed": config.seed}
    if config.engine == "ml":
        data.update(training_symbols=config.training_symbols, annual_labels=config.annual_labels)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def scorecard(rows, expected=None):
    valid = [r for r in rows if "return_pct" in r]
    if not valid:
        return {"count": 0, "complete": False, "verdict": "尚无完整评估"}
    avg = lambda key: sum(r[key] for r in valid) / len(valid)
    losses = [r["year"] for r in valid if r["return_pct"] < 0]
    wins = sum(r["excess_return"] > 0 for r in valid)
    complete = len(valid) == (expected if expected is not None else len(rows))
    verdict = "多数年份优于持有" if wins > len(valid) / 2 else "收益优势仍不稳定"
    if not complete:
        verdict = "评估尚未完成"
    return {"count": len(valid), "complete": complete, "mean_return": avg("return_pct"),
            "mean_excess": avg("excess_return"), "worst_return": min(r["return_pct"] for r in valid),
            "max_drawdown": min(r["max_drawdown"] for r in valid), "beat_hold": wins,
            "loss_years": losses, "verdict": verdict}


def annual_summary(result):
    keys = ("year", "scene", "return_pct", "excess_return", "benchmark_return", "max_drawdown",
            "benchmark_drawdown", "trade_count", "start", "end", "data_hash", "fallback_count", "fees", "bars", "warnings")
    return {k: result[k] for k in keys if k in result}


def write_gzip(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as output:
        output.write(dumps(value))
    temporary.replace(path)


def read_gzip(path):
    with gzip.open(path, "rt", encoding="utf-8") as source:
        return json.load(source)


def run_evaluation(store, job_id, request, progress):
    root = store.root / "evaluations" / job_id
    snapshot = root / "market.json.gz"
    if snapshot.exists():
        frozen = read_gzip(snapshot)
        histories = {s: pd.read_json(StringIO(value), orient="split") for s, value in frozen["frames"].items()}
        for frame in histories.values():
            frame.index = pd.to_datetime(frame.index)
        warnings = frozen["warnings"]
    else:
        histories, warnings = load_histories(Market(store), request.config, dt.date(min(request.years), 1, 1),
                                             dt.date(max(request.years), 12, 31), lambda msg: progress(3, msg))
        write_gzip(snapshot, {"warnings": warnings, "frames": {s: f.to_json(orient="split", date_format="iso", double_precision=15) for s, f in histories.items()}})
        # Start and resume both consume exactly the serialized snapshot.
        frozen = read_gzip(snapshot)
        histories = {s: pd.read_json(StringIO(value), orient="split") for s, value in frozen["frames"].items()}
        for frame in histories.values():
            frame.index = pd.to_datetime(frame.index)
    rows = []
    for index, year in enumerate(request.years):
        path = root / f"{year}.json.gz"
        progress(5 + 90 * index / len(request.years), f"评估 {year} 年 · {index + 1}/{len(request.years)}")
        if path.exists():
            result = read_gzip(path)
        else:
            result = run_backtest(store, JobRequest(year=year, config=request.config), histories=histories,
                                  progress=lambda p, m: progress(5 + 90 * (index + p / 100) / len(request.years), f"{year} 年 · {m}"))
            write_gzip(path, result)
        rows.append(annual_summary(result))
        output = {"per_year": rows, "scorecard": scorecard(rows, len(request.years)), "warnings": warnings,
                  "strategy_key": strategy_key(request.config), "expected_years": request.years,
                  "protocol": "各年重新从相同初始资产开始；训练截止于该年之前；平均收益为年度算术平均，不是连续复利收益。"}
        store.update_job(job_id, result=output)
    return output


def find_evidence(store, config):
    wanted = strategy_key(config)
    with store.connect() as c:
        rows = c.execute("SELECT id,kind,created,request,json_extract(result,'$.best') AS best,json_extract(result,'$.per_year') AS per_year FROM jobs WHERE status='done' AND kind IN ('learn','evaluate') ORDER BY created DESC LIMIT 200").fetchall()
    for row in rows:
        if row["kind"] == "evaluate":
            source = AppConfig.model_validate(json.loads(row["request"])["config"])
            if strategy_key(source) != wanted:
                continue
            years = json.loads(row["per_year"] or "[]")
            origin = "独立运行的跨年评估"
        else:
            choices = json.loads(row["best"] or "{}")
            matched = next((v for v in choices.values() if v.get("complete") and strategy_key(AppConfig.model_validate(v["config"])) == wanted), None)
            if not matched:
                continue
            years = matched["per_year"] + [{**y, "check": True} for y in matched.get("check_years", [])]
            origin = "调参历史，可能参与过参数选择"
        return {"matched": True, "job_id": row["id"], "kind": row["kind"], "created": row["created"], "origin": origin,
                "per_year": years, "scorecard": scorecard(years), "strategy_key": wanted}
    return {"matched": False, "strategy_key": wanted, "per_year": [], "scorecard": scorecard([])}
