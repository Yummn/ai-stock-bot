"""Offline, isolated comparison of label definitions on frozen historical data."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(variable, "2")

from ai_stock_bot.v2.backtest import run_backtest
from ai_stock_bot.v2.evaluation import annual_summary, write_gzip
from ai_stock_bot.v2.market import Market, digest
from ai_stock_bot.v2.schema import AppConfig, JobRequest, MODEL_NAMES
from ai_stock_bot.v2.store import Store
from iterate_stocklab import objective, write_json

ROOT = Path("data/v3/research/decision-upgrade-2026-09-28")


def candidates(config, model):
    choices = {}
    for name, delta in (("baseline", None), ("net", 0), ("net_lower", -.03), ("net_higher", .03)):
        c = config.model_copy(deep=True)
        c.engine, c.model, c.auto_profile = "ml", model, False
        if delta is not None:
            p = c.preset()
            p.label_basis = "next_open_net"
            p.buy_threshold = round(max(.01, min(1., p.buy_threshold + delta)), 6)
            p.sell_threshold = round(max(0., min(p.buy_threshold - .01, p.sell_threshold + delta)), 6)
        choices[name] = c
    return choices


def worker(model, year, names, stress):
    settings = json.loads((ROOT / "before.json").read_text(encoding="utf-8"))
    config = AppConfig.model_validate(settings["config"])
    market = Market(Store(ROOT / "frozen"))
    histories = {s: market.read(s) for s in dict.fromkeys(config.symbols + config.training_symbols)}
    store = Store(ROOT / "compute")
    rows = {}
    for name in names:
        cfg = candidates(config, model)[name]
        if stress:
            cfg.seed = 43
            cfg.risk.commission_rate *= 2
            cfg.risk.slippage_bps *= 2
        result = run_backtest(store, JobRequest(year=year, config=cfg), histories=histories)
        if result["fallback_count"] or not result["start"].startswith(str(year)) or not result["end"].startswith(str(year)):
            raise RuntimeError("Invalid year coverage or model fallback")
        result["mean_exposure"] = sum(1 - p["cash"] / p["equity"] for p in result["curve"]) / len(result["curve"])
        write_gzip(ROOT / "runs" / f"{model}-{year}-{name}-{'stress' if stress else 'main'}.json.gz", result)
        rows[name] = {**annual_summary(result), "mean_exposure": result["mean_exposure"], "config": cfg.model_dump()}
    if len({r["data_hash"] for r in rows.values()}) != 1:
        raise RuntimeError("Comparison data mismatch")
    write_json(ROOT / f"{model}-{year}-{'stress' if stress else 'main'}.json", rows)


def run_worker(model, year, names, stress=False):
    path = ROOT / f"{model}-{year}-{'stress' if stress else 'main'}.json"
    if not path.exists():
        command = [sys.executable, __file__, "--worker", model, "--year", str(year), "--names", *names]
        if stress:
            command.append("--stress")
        for attempt in range(2):
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode == 0:
                break
            with (ROOT / "failures.log").open("a", encoding="utf-8") as log:
                log.write(f"{model}/{year}/{attempt}: {result.returncode}\n{result.stderr}\n")
        else:
            raise RuntimeError(f"Worker failed: {model}/{year}; see failures.log")
    print(f"completed {model} {year} {'stress' if stress else 'main'}", flush=True)
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    live = Store(Path("data/v3"))
    if not (ROOT / "before.json").exists():
        before = live.settings()
        write_json(ROOT / "before.json", before)
        with live.connect() as source, closing(sqlite3.connect(ROOT / "before.sqlite3")) as dest:
            source.backup(dest)
        config = AppConfig.model_validate(before["config"])
        source = Market(Store(Path("data/v3/research/weak-2026-09-28/frozen")))
        target = Market(Store(ROOT / "frozen"))
        manifest = []
        for s in dict.fromkeys(config.symbols + config.training_symbols):
            data = source.read(s)
            if len(data) < 300 or data.index[-1].year != 2025:
                raise RuntimeError(f"Missing historical data: {s}")
            target.write(s, data, "frozen upgrade comparison")
            manifest.append(dict(symbol=s, hash=digest(data), bars=len(data), start=str(data.index[0].date()), end=str(data.index[-1].date())))
        write_json(ROOT / "manifest.json", manifest)
    config = AppConfig.model_validate(json.loads((ROOT / "before.json").read_text(encoding="utf-8"))["config"])
    summary = {}
    for model in MODEL_NAMES:
        rows = [run_worker(model, year, ["baseline", "net", "net_lower", "net_higher"]) for year in (2021, 2022, 2023, 2024)]
        scores = {name: objective([r[name] for r in rows], config.profile) for name in rows[0]}
        winner = max(scores, key=scores.get)
        check = run_worker(model, 2025, list(dict.fromkeys(["baseline", winner])))
        gain = scores[winner] - scores["baseline"]
        worst = lambda name: min(r[name]["return_pct"] for r in rows)
        accepted = winner != "baseline" and gain > .003 and worst(winner) >= worst("baseline") - .01
        accepted &= check[winner]["return_pct"] >= check["baseline"]["return_pct"] - .01 and check[winner]["max_drawdown"] >= check["baseline"]["max_drawdown"] - .02
        stress = []
        if accepted:
            stress = [run_worker(model, year, ["baseline", winner], True) for year in (2022, 2024)]
            accepted &= all(r[winner]["return_pct"] >= r["baseline"]["return_pct"] - .01 and r[winner]["max_drawdown"] >= r["baseline"]["max_drawdown"] - .02 for r in stress)
        summary[model] = dict(profile=config.profile, scores=scores, winner=winner, accepted=bool(accepted), search=rows, check=check, stress=stress)
        write_json(ROOT / "summary.json", summary)
        print(f"{model}: winner={winner}, accepted={accepted}", flush=True)


def ai_smoke():
    from ai_stock_bot.v2.execution import Account
    from ai_stock_bot.v2.features import features
    from ai_stock_bot.v2.strategies import ai_decisions
    config = AppConfig.model_validate(json.loads((ROOT / "before.json").read_text(encoding="utf-8"))["config"])
    config.engine, config.auto_profile = "ai", False
    market = Market(Store(ROOT / "frozen"))
    snapshots = []
    for symbol in config.symbols[:2]:
        data = market.read(symbol, end="2025-12-31")
        snapshots.append((symbol, float(data.close.iloc[-1]), features(data).iloc[-1]))
    account = Account(config.risk)
    result = ai_decisions(config, snapshots, account.decision_context("2025-12-31"), "2025-12-31")
    write_json(ROOT / "ai-smoke.json", result)
    print({s: {k: r[k] for k in ("source", "side", "fallback")} for s, r in result.items()})


if __name__ == "__main__":
    import faulthandler
    faulthandler.enable()
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker")
    parser.add_argument("--year", type=int)
    parser.add_argument("--names", nargs="+")
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--ai-smoke", action="store_true")
    args = parser.parse_args()
    if args.ai_smoke:
        ai_smoke()
    elif args.worker:
        worker(args.worker, args.year, args.names, args.stress)
    else:
        main()
