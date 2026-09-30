"""Locked candidate: continuous account, independent years, and financing stress."""
import gzip
import hashlib
import json
import tempfile
from io import StringIO
from pathlib import Path

import pandas as pd

from research_daily_upgrade import compact, save
from research_daily_stability import rolling_diagnostics
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.portfolio import simulate
from ai_stock_bot.v4.schema import ENGINE_VERSION, Plan


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / "docs/reports/daily-4-7-frozen/market.json.gz"
    previous = json.loads((root / "docs/reports/daily-4-7-stability/results.json").read_text(encoding="utf-8"))
    plan = Plan.model_validate(previous["later"]["2"]["plan"])
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        histories = {s: pd.read_json(StringIO(f), orient="split") for s, f in json.load(stream).items()}
    end = str(min(f.index[-1] for f in histories.values()).date())
    output = root / "docs/reports/daily-4-7-qualification"
    output.mkdir(exist_ok=True)
    protocol = {"engine": ENGINE_VERSION, "plan": plan.model_dump(),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "candidate_locked_before_checks": True,
                "reason": "2x candidate: higher stressed development return and less drawdown than 3x candidate",
                "limitations": "All years previously seen; no untouched out-of-sample claim, no automatic profit qualification",
                "stress": {"transaction_cost_multiplier": 2, "financing_rate": .12}}
    save(output / "protocol.json", protocol)
    results = {"continuous": {}, "independent_years": {}}
    with tempfile.TemporaryDirectory(prefix="stocklab-qualify-") as tmp:
        store = Store(Path(tmp))
        for label, candidate in (("candidate_2x", plan), ("same_strategy_1x", plan.model_copy(update={"research_multiple": 1})),
                                 ("same_allocation_hold_1x", plan.model_copy(update={"method": "hold", "research_multiple": 1}))):
            for stress in (False, True):
                current = candidate.model_copy(update={"commission_rate": candidate.commission_rate * 2,
                    "min_commission": candidate.min_commission * 2, "slippage_bps": candidate.slippage_bps * 2,
                    "research_annual_rate": .12}) if stress else candidate
                raw = simulate(store, current, histories, "2020-01-01", end, forecast_plan=candidate)
                result = compact(raw, current)
                result["diagnostics"] = rolling_diagnostics(raw, current.starting_cash)
                results["continuous"][label + ("_stress" if stress else "")] = result
                print(json.dumps({"case": label, "stress": stress, "return": result["liquidation_return"],
                                  "drawdown": result["max_drawdown"], "cagr": result["liquidation_cagr"]}), flush=True)
        for year in range(2020, 2027):
            finish = min(f"{year}-12-31", end)
            result = simulate(store, plan, histories, f"{year}-01-01", finish)
            results["independent_years"][str(year)] = compact(result, plan)
            print(json.dumps({"year": year, "return": result["return_pct"]}), flush=True)
    results["qualified_as_stable_profit"] = False
    results["forward_observation_required"] = True
    save(output / "results.json", results)
    print(str(output), flush=True)


if __name__ == "__main__":
    main()
