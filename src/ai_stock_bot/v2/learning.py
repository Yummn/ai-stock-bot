from __future__ import annotations

import copy
import hashlib

import numpy as np

from .backtest import load_histories
from .isolated import run_backtest
from .market import Market
from .evaluation import annual_summary, read_gzip, write_gzip, strategy_key
from .schema import AppConfig, JobRequest, MODEL_FIELDS, PROFILES
from .store import dumps


def score_year(row):
    return row["return_pct"] + .5 * row["excess_return"] + .5 * row["max_drawdown"]


def candidate_score(valid, profile, weights):
    target = {"aggressive": "bull", "balanced": "sideways", "conservative": "bear"}.get(profile)
    groups = {"specialized": [y for y in valid if y["scene"] == target],
              "fallback": [y for y in valid if y["scene"] != target], "composite": valid}
    if profile == "custom":
        return float(np.mean([score_year(y) for y in valid])) if valid else 0.
    applicable = {key: weight for key, weight in weights.items() if groups[key]}
    total = sum(applicable.values())
    return sum(weight * float(np.mean([score_year(y) for y in groups[key]])) for key, weight in applicable.items()) / total if total else 0.


def summarize(trials):
    best = {}
    for item in trials:
        if not item["complete"]:
            continue
        if item["profile"] not in best or item["score"] > best[item["profile"]]["score"]:
            best[item["profile"]] = item
    return {"best": best, "trials": trials, "total": len(trials), "rounds": max((t["round"] for t in trials), default=0)}


def mutate(config, profile, rng, scale):
    candidate = copy.deepcopy(config)
    candidate.profile = profile
    candidate.auto_profile = False
    candidate.profile_selection = "fixed"
    p = candidate.preset()
    p.buy_threshold = float(np.clip(p.buy_threshold + rng.uniform(-scale, scale), .01, .99))
    p.sell_threshold = float(np.clip(p.sell_threshold + rng.uniform(-scale, scale), .0, p.buy_threshold - .01))
    fields = [f for f in MODEL_FIELDS[candidate.model] if f["kind"] == "number"]
    for field in rng.choice(fields, size=min(2, len(fields)), replace=False):
        key = field["key"]
        old = p.params[key]
        value = old * np.exp(rng.uniform(-scale * 4, scale * 4)) if old else scale
        if field["step"] >= 1:
            value = int(round(value))
        if field["min"] is not None:
            value = max(field["min"], value)
        if field["max"] is not None:
            value = min(field["max"], value)
        if key == "kernel_size":
            value = int(value) | 1
        p.params[key] = value
    return AppConfig.model_validate(candidate.model_dump())


def run_learning(store, job_id, request, progress):
    from datetime import date

    config = request.config.model_copy(deep=True)
    config.engine = "ml"
    config.auto_profile = False
    config.profile_selection = "fixed"
    years = sorted(set(request.years))
    maximum_window = max(p.training_years for p in config.models[config.model].values())
    for preset in config.models[config.model].values():
        preset.training_years = maximum_window
    histories, warnings = load_histories(Market(store), config, date(min(years + request.check_years), 1, 1), date(max(years + request.check_years), 12, 31), lambda msg: progress(4, msg))
    config = request.config.model_copy(deep=True)
    config.engine = "ml"
    config.auto_profile = False
    config.profile_selection = "fixed"
    weights = {k: v / sum(request.weights.values()) for k, v in request.weights.items()}
    round_number = 1
    while True:
        previous_best = store.learning_summary(job_id, before_round=round_number)["best"]
        for profile_index, profile in enumerate(request.profiles):
            base = AppConfig.model_validate(previous_best[profile]["config"]) if profile in previous_best else config.model_copy(deep=True)
            base.profile = profile
            for index in range(request.trials):
                rng = np.random.default_rng(config.seed + round_number * 101 + index * 17 + list(PROFILES).index(profile))
                phase = "参照" if index == 0 else "粗搜" if index % 3 == 1 else "细搜" if index % 3 == 2 else "随机扰动"
                candidate = base.model_copy(deep=True) if index == 0 else mutate(base, profile, rng, .12 if phase == "粗搜" else .035 if phase == "细搜" else .08)
                candidate.profile, candidate.auto_profile = profile, False
                candidate.profile_selection = "fixed"
                fp = hashlib.sha256(dumps({"round": round_number, "index": index, "profile": profile, "config": candidate.model_dump(), "years": years, "weights": weights}).encode()).hexdigest()
                if store.has_trial(job_id, fp):
                    continue
                per_year = []
                for year_index, year in enumerate(years):
                    message = f"第 {round_number} 轮 · {PROFILES[profile]} {phase} · 验证 {year} 年"
                    progress(10 + 80 * ((profile_index * request.trials + index) * len(years) + year_index) / (len(request.profiles) * request.trials * len(years)), message)
                    try:
                        backtest = run_backtest(store, JobRequest(kind="backtest", year=year, config=candidate), histories=histories, progress=lambda _, msg: progress(None, message + " / " + msg))
                        per_year.append({k: backtest[k] for k in ("year", "scene", "return_pct", "excess_return", "benchmark_return", "max_drawdown", "trade_count", "start", "end", "data_hash")})
                    except Exception as exc:
                        per_year.append({"year": year, "error": str(exc)[:500]})
                complete = all("return_pct" in y for y in per_year)
                valid = [y for y in per_year if "return_pct" in y]
                target = {"aggressive": "bull", "balanced": "sideways", "conservative": "bear"}.get(profile)
                specialized = [y for y in valid if y["scene"] == target]
                fallback = [y for y in valid if y["scene"] != target]
                score = candidate_score(valid, profile, weights)
                item = {"fingerprint": fp, "round": round_number, "profile": profile, "phase": phase, "label": f"第{round_number}轮 {PROFILES[profile]} {phase} #{index + 1}", "score": score if complete else None, "complete": complete, "valid_years": len(valid), "expected_years": len(years), "specialized_years": len(specialized), "mean_return": float(np.mean([y["return_pct"] for y in valid])) if valid else 0., "mean_excess": float(np.mean([y["excess_return"] for y in valid])) if valid else 0., "worst_return": min((y["return_pct"] for y in valid), default=0.), "per_year": per_year, "config": candidate.model_dump(), "warnings": warnings}
                store.save_trial(job_id, fp, item)
                store.update_job(job_id, result=store.learning_summary(job_id))
        summary = store.learning_summary(job_id)
        summary["protocol"] = {"search_years": years, "check_years": request.check_years}
        for profile, item in summary["best"].items():
            checked = []
            candidate = AppConfig.model_validate(item["config"])
            for year in request.check_years:
                progress(92, f"第 {round_number} 轮 · {PROFILES[profile]} · 验收 {year} 年")
                path = store.root / "learning_checks" / job_id / f"{strategy_key(candidate)}-{year}.json.gz"
                try:
                    if path.exists():
                        row = read_gzip(path)
                    else:
                        row = annual_summary(run_backtest(store, JobRequest(year=year, config=candidate), histories=histories))
                        write_gzip(path, row)
                    checked.append(row)
                except Exception as exc:
                    checked.append({"year": year, "error": str(exc)[:500]})
            item["check_years"] = checked
        store.update_job(job_id, result=summary)
        if not request.continuous:
            break
        round_number += 1
    return summary
