"""Compare both automatic profile selectors with the saved ML presets.

Run one model/year per process to release native ML memory between jobs. This
script only reads the active configuration and cached bars; all outputs go to
the research directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

from ai_stock_bot.v2.backtest import run_backtest
from ai_stock_bot.v2.market import Market, digest
from ai_stock_bot.v2.schema import AppConfig, JobRequest, MODEL_NAMES
from ai_stock_bot.v2.store import Store


SOURCE = Path("data/v3-hybrid-preview")
OUTPUT = Path("data/v3/research/profile-selection-all-models-2026-09-28")
TRADED = ("510300.SH", "518880.SH")
TRAINING = ("510500.SH", "512890.SH", "511260.SH")


def save(row: dict) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / f"{row['model']}-{row['year']}-{row['mode']}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", choices=MODEL_NAMES)
    parser.add_argument("year", type=int, choices=(2022, 2023, 2024, 2025))
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    source = Store(SOURCE)
    saved = AppConfig.model_validate(source.settings()["config"])
    config = saved.model_copy(deep=True)
    config.engine = "ml"
    config.model = args.model
    config.profile = "balanced"
    config.symbols = list(TRADED)
    config.training_symbols = list(TRAINING)
    market = Market(source)
    histories = {symbol: market.read(symbol, end=f"{args.year}-12-31")
                 for symbol in (*TRADED, *TRAINING)}
    for symbol in TRADED:
        year_bars = histories[symbol].loc[f"{args.year}-01-01":f"{args.year}-12-31"]
        if len(year_bars) < 200:
            raise ValueError(f"{symbol} has only {len(year_bars)} bars in {args.year}")
    inputs = {symbol: digest(frame) for symbol, frame in histories.items()}
    presets = {key: config.models[args.model][key].model_dump()
               for key in ("aggressive", "balanced", "conservative")}
    config_hash = hashlib.sha256(json.dumps({"presets": presets, "risk": config.risk.model_dump(),
                                               "seed": config.seed, "symbols": TRADED,
                                               "training_symbols": TRAINING}, sort_keys=True).encode()).hexdigest()[:20]
    compute = Store(OUTPUT / "compute")

    for mode in ("trend", "recent"):
        start = time.monotonic()
        current = config.model_copy(deep=True)
        current.profile_selection = mode
        current.auto_profile = True
        print(f"START {args.model} {args.year} {mode}", flush=True)
        try:
            result = run_backtest(compute, JobRequest(config=current, year=args.year),
                                  histories=histories)
            decisions = result["decisions"]
            selected = Counter(item["profile"] for item in decisions)
            reasons = Counter(item.get("profile_selection_reason") or "走势判断" for item in decisions)
            row = {
                "status": "complete", "model": args.model, "year": args.year,
                "mode": mode, "seconds": round(time.monotonic() - start, 2),
                "config_hash": config_hash, "input_hashes": inputs,
                "data_hash": result["data_hash"], "symbols": list(TRADED),
                "training_symbols": list(TRAINING), "presets": presets,
                "risk": config.risk.model_dump(), "seed": config.seed,
                "start": result["start"], "end": result["end"],
                "bars": result["bars"], "return_pct": result["return_pct"],
                "benchmark_return": result["benchmark_return"],
                "excess_return": result["excess_return"],
                "max_drawdown": result["max_drawdown"],
                "trade_count": result["trade_count"], "fees": result["fees"],
                "fallback_count": result["fallback_count"],
                "profile_trades": result["profile_trades"],
                "selection_counts": dict(selected),
                "selection_reasons": dict(reasons),
                "training": result["training"], "warnings": result["warnings"],
            }
        except Exception as exc:
            row = {"status": "error", "model": args.model, "year": args.year,
                   "mode": mode, "seconds": round(time.monotonic() - start, 2),
                   "config_hash": config_hash, "input_hashes": inputs,
                   "error": f"{type(exc).__name__}: {exc}"}
        save(row)
        print(json.dumps({key: value for key, value in row.items() if key in
                          {"status", "model", "year", "mode", "seconds", "return_pct",
                           "benchmark_return", "max_drawdown", "trade_count", "error"}},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
