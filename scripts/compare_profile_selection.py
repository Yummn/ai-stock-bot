"""Reproduce the trend-vs-recent comparison without changing the enabled plan."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from ai_stock_bot.v2.backtest import run_backtest
from ai_stock_bot.v2.market import Market
from ai_stock_bot.v2.schema import AppConfig, JobRequest
from ai_stock_bot.v2.store import Store


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    source = Store(Path("data/v3-hybrid-preview"))
    output = Path("data/v3/research/profile-selection-2026-09-28")
    compute = Store(output / "compute")
    config = AppConfig.model_validate(source.settings()["config"])
    config.engine = "ml"
    config.model = "random_forest"
    config.profile = "balanced"
    config.symbols = ["510300.SH"]
    config.training_symbols = ["510500.SH", "512890.SH", "518880.SH"]
    market = Market(source)
    histories = {symbol: market.read(symbol, end="2025-12-31") for symbol in config.symbols + config.training_symbols}
    assert all(len(frame) > 1000 for frame in histories.values())
    rows = []
    for year in (2022, 2025):
        for mode in ("trend", "recent"):
            current = config.model_copy(deep=True)
            current.auto_profile = True
            current.profile_selection = mode
            start = time.monotonic()
            result = run_backtest(compute, JobRequest(config=current, year=year),
                                  progress=lambda percent, message: None, histories=histories)
            row = {key: result[key] for key in ("year", "start", "end", "return_pct", "benchmark_return",
                                                  "excess_return", "max_drawdown", "trade_count", "fees", "fallback_count",
                                                  "profile_trades", "data_hash")}
            row.update(mode=mode, seconds=round(time.monotonic() - start, 2),
                       decisions=len(result["decisions"]),
                       insufficient=sum("不足" in (d.get("profile_selection_reason") or "") for d in result["decisions"]),
                       selected_counts={profile: sum(d["profile"] == profile for d in result["decisions"])
                                        for profile in ("aggressive", "balanced", "conservative")})
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    receipt = {"source": str(source.root), "config": config.model_dump(), "results": rows,
               "note": "Only cached OHLC data; one ETF and three training ETFs. No enabled settings or account changes."}
    output.mkdir(parents=True, exist_ok=True)
    (output / "comparison.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {output / 'comparison.json'}", flush=True)


if __name__ == "__main__":
    main()
