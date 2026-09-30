"""Retrain the deployed adapter and compare every equity point with frozen research."""
import argparse
from pathlib import Path

import numpy as np

from research_combinations import ROOT, get_histories, load
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.portfolio import simulate
from ai_stock_bot.v4.research import write_json
from ai_stock_bot.v4.schema import Plan


def main(args):
    research = load(ROOT/"docs/reports/execution-risk-20260930/results.json")
    key = "hgb20_regularized-2x-monthly-v30"
    periods = research["finalists"][key]
    original = periods["continuous"]["candidate"]["plan"]
    plan = Plan.model_validate({**original, "method": "ml", "model_recipe": "hgb20_regularized"})
    store = Store(args.output/"isolated-store")
    histories = get_histories()
    results = {}
    for period in ("continuous", "later", "partial"):
        reference = periods[period]["base"]
        result = simulate(store, plan, histories, reference["start"], reference["end"])
        np.testing.assert_allclose([r["equity"] for r in result["curve"]],
                                   [r["equity"] for r in reference["curve"]], rtol=1e-11, atol=1e-7)
        for field in ("trade_count", "fees", "interest_paid", "max_drawdown"):
            np.testing.assert_allclose(result[field], reference[field], rtol=1e-11, atol=1e-7)
        assert all(t["signal_date"] < t["date"] for t in result["trades"])
        assert all(not r.get("label_end") or r["label_end"] < r["cutoff"] for r in result["training"])
        write_json(args.output/f"{period}.json", result)
        results[period] = {"equity_points_equal": len(result["curve"]),
            "training_months": len(result["training"]), "trades": result["trade_count"]}
        print(f"PARITY {period}: {results[period]}", flush=True)
    write_json(args.output/"verification.json", {"plan": plan.model_dump(), "reference": key, "results": results})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
