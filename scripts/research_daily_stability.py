"""Second bounded experiment: turnover and diversification, not retuning later years."""
import argparse
import gzip
import hashlib
import json
import tempfile
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

from research_daily_upgrade import save, compact
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.portfolio import simulate
from ai_stock_bot.v4.schema import ENGINE_VERSION, Plan, with_profile


def rolling_diagnostics(result, starting):
    equity = pd.Series([r["equity"] for r in result["curve"]],
                       index=pd.to_datetime([r["date"] for r in result["curve"]]))
    rolling = equity.pct_change(252).dropna()
    returns = equity.pct_change().dropna().to_numpy()
    rng = np.random.default_rng(417)
    annuals = []
    # Dependent blocks, not independent daily shuffling. Exploratory uncertainty only.
    for _ in range(500):
        starts = rng.integers(0, max(1, len(returns) - 19), size=int(np.ceil(len(returns) / 20)))
        path = np.concatenate([returns[s:s + 20] for s in starts])[:len(returns)]
        annuals.append(float(np.prod(1 + path) ** (252 / len(path)) - 1))
    return {"rolling_252_min": float(rolling.min()) if len(rolling) else None,
            "rolling_252_positive_fraction": float((rolling > 0).mean()) if len(rolling) else None,
            "block_bootstrap_cagr_90pct": list(np.quantile(annuals, [.05, .95])),
            "fees_fraction": result["fees"] / starting,
            "interest_fraction": result["interest_paid"] / starting}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.source / "market.json.gz", "rt", encoding="utf-8") as f:
        histories = {s: pd.read_json(StringIO(v), orient="split") for s, v in json.load(f).items()}
    base = with_profile(Plan(trend_signal="multi_horizon", exposure_policy="volatility_target"), "aggressive")
    plans = {}
    for multiple in (1, 2, 3):
        for cadence in ("weekly", "monthly"):
            for diversified in (False, True):
                name = f"{multiple}x-{cadence}-{'four_groups' if diversified else 'three_groups'}"
                changes = {"research_multiple": multiple, "rebalance": cadence, "drift_band": .08}
                if diversified:
                    changes.update(holdings=4, allocation={g: .25 for g in base.allocation})
                plans[name] = Plan.model_validate({**base.model_dump(), **changes})
    protocol = {"engine": ENGINE_VERSION, "source_snapshot": str(args.source / "market.json.gz"),
                "source_hash": hashlib.sha256((args.source / "market.json.gz").read_bytes()).hexdigest(),
                "candidates": {k: p.model_dump() for k, p in plans.items()},
                "development": ["2020-01-01", "2022-12-31"], "later": ["2023-01-01", "2025-12-31"],
                "selection": "Each leverage ceiling: development stressed CAGR minus half drawdown",
                "limits": "Later data already observed. Diagnostic replication, not independent holdout. No automatic promotion."}
    if (output / "protocol.json").exists() and json.loads((output / "protocol.json").read_text(encoding="utf-8")) != protocol:
        raise ValueError("Protocol changed")
    save(output / "protocol.json", protocol)
    path = output / "results.json"
    results = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"development": {}, "later": {}, "2026_partial": {}}
    with tempfile.TemporaryDirectory(prefix="stocklab-stability-") as tmp:
        store = Store(Path(tmp))

        def run(name, plan, stage, start, end):
            if name in results[stage]:
                return
            row = {"plan": plan.model_dump()}
            for cost, stress in (("base", False), ("stress", True)):
                current = plan.model_copy(update={"commission_rate": plan.commission_rate * 2,
                    "min_commission": plan.min_commission * 2, "slippage_bps": plan.slippage_bps * 2,
                    "research_annual_rate": .12}) if stress else plan
                result = simulate(store, current, histories, start, end, forecast_plan=plan)
                row[cost] = compact(result, current)
                row[cost]["diagnostics"] = rolling_diagnostics(result, plan.starting_cash)
            results[stage][name] = row
            save(path, results)
            print(json.dumps({"stage": stage, "name": name, "return": row["base"]["liquidation_return"],
                              "drawdown": row["base"]["max_drawdown"], "stress": row["stress"]["liquidation_return"]}), flush=True)

        for name, plan in plans.items():
            run(name, plan, "development", *protocol["development"])
        selected = {}
        for multiple in (1, 2, 3):
            names = [k for k in plans if k.startswith(f"{multiple}x-")]
            selected[str(multiple)] = max(names, key=lambda k: results["development"][k]["stress"]["liquidation_cagr"]
                - .5 * abs(results["development"][k]["stress"]["max_drawdown"]))
        results["selected"] = selected
        save(path, results)
        print("LOCKED " + json.dumps(selected), flush=True)
        controls = {str(m): plans[selected[str(m)]] for m in (1, 2, 3)}
        controls["old_3x"] = with_profile(Plan(research_multiple=3), "aggressive")
        controls["hold_1x"] = base.model_copy(update={"method": "hold"})
        controls["ml_1x"] = plans[selected["1"]].model_copy(update={"method": "ml"})
        for name, plan in controls.items():
            run(name, plan, "later", *protocol["later"])
        end = str(min(f.index[-1] for f in histories.values()).date())
        for name, plan in controls.items():
            run(name, plan, "2026_partial", "2026-01-01", end)
    save(path, results)
    print(str(output.resolve()), flush=True)


if __name__ == "__main__":
    main()
