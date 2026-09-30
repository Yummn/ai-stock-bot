"""Compare risk templates on one frozen market snapshot without changing saved plans."""

import argparse
import json
from io import StringIO
from pathlib import Path

import pandas as pd

from ai_stock_bot.v2.evaluation import read_gzip
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.portfolio import simulate
from ai_stock_bot.v4.schema import Plan, with_profile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--methods", nargs="+", default=["rotation"],
                        choices=["rotation", "ml", "ml_class", "cnn", "cnn_class"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    frozen = read_gzip(args.snapshot)
    frames = {symbol: pd.read_json(StringIO(raw), orient="split")
              for symbol, raw in frozen["frames"].items()}
    for frame in frames.values():
        frame.index = pd.to_datetime(frame.index)
    store = Store(root / "data" / "v4")
    start, end = "2021-01-01", "2025-12-31"
    rows = []
    for profile in ("conservative", "balanced", "aggressive"):
        for method in args.methods:
            plan = with_profile(Plan(method=method), profile)
            result = simulate(store, plan, frames, start, end, method)
            stressed = plan.model_copy(update={
                "commission_rate": plan.commission_rate * 2,
                "min_commission": plan.min_commission * 2,
                "slippage_bps": plan.slippage_bps * 2,
            })
            stress = simulate(store, stressed, frames, start, end, method, forecast_plan=plan)
            hold = simulate(store, plan, frames, start, end, "hold")
            rows.append({"profile": profile, "method": method,
                         "return_pct": result["return_pct"],
                         "annualized_return": result["annualized_return"],
                         "max_drawdown": result["max_drawdown"],
                         "mean_exposure": result["mean_exposure"],
                         "trade_count": result["trade_count"],
                         "stress_return": stress["return_pct"],
                         "hold_return": hold["return_pct"],
                         "per_year": result["per_year"]})
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    print(f"Compared {len(rows)} plans on {start}..{end}; snapshot={args.snapshot}")


if __name__ == "__main__":
    main()
