"""Supplemental high-risk ablation, locked before inspecting its later-period returns."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from research_combinations import ROOT, SNAPSHOT, base_plan, dispatch, load
from ai_stock_bot.v4.research import write_json
from ai_stock_bot.v4.schema import ASSETS, ENGINE_VERSION


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    base = base_plan().model_copy(update={"max_exposure": 1., "max_weight": .6,
        "allocation": {ASSETS[s]["group"]: w for s, w in (
            ("510300.SH", .4), ("518880.SH", .2), ("511260.SH", .1), ("513100.SH", .3))},
        "drawdown_pause": .25, "target_volatility": .30})
    # GROUPS is China, gold, bonds, overseas. No future period chooses this allocation.
    cases = {}
    for policy in ("fixed", "volatility_target"):
        for frequency in ("weekly", "monthly"):
            for multiple in (1, 2, 3):
                key = f"risk-{policy}-{frequency}-{multiple}x"
                cases[key] = {"family": "multi", "plan": base.model_copy(update={
                    "exposure_policy": policy, "rebalance": frequency, "research_multiple": multiple}).model_dump()}
    protocol = {"version": "risk-budget-1", "engine": ENGINE_VERSION,
        "identity": hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest(), "candidate_count": len(cases),
        "cases": cases, "models": {}, "development": ["2020-01-01", "2022-12-31"],
        "later": ["2023-01-01", "2025-12-31"], "partial": ["2026-01-01", "2026-09-28"],
        "continuous": ["2020-01-01", "2026-09-28"],
        "selection": "Development stressed CAGR - half absolute drawdown; one winner per leverage, then no retuning",
        "stress": "double fees/slippage and 12 percent interest", "sources": [],
        "gate": {"cagr": .20, "drawdown": .20},
        "limitations": ["Supplemental hypotheses motivated by the first development screen",
            "Previously seen history; not independent holdout", "Broader risk budget can increase loss, not guarantee profit"]}
    path = args.output / "protocol.json"
    if path.exists() and load(path) != protocol:
        raise ValueError("Protocol changed")
    write_json(path, protocol)
    if args.freeze_only:
        print(f"LOCKED {len(cases)} supplemental candidates: {path}")
        return
    result_path = args.output / "results.json"
    results = load(result_path) if result_path.exists() else {"development": {}, "finalists": {}, "failures": []}
    directory = args.output / "cases"
    directory.mkdir(exist_ok=True)

    def run(key, candidate, period):
        pair = {"candidate": candidate}
        for stress in (False, True):
            name = f"{period}-{key}-{'stress' if stress else 'base'}"
            request = directory / f"{name}.json"
            write_json(request, {"candidate": candidate, "period": protocol[period], "stress": stress})
            output = request.with_suffix(".result.json")
            if not output.exists() and not dispatch(args, ["--case", str(request)], name):
                results["failures"].append(name)
                return None
            pair["stress" if stress else "base"] = load(output)
        print(json.dumps({"case": key, "period": period, "cagr": pair["base"]["liquidation_cagr"],
            "dd": pair["base"]["max_drawdown"], "stress": pair["stress"]["liquidation_cagr"]}), flush=True)
        return pair

    for key, candidate in cases.items():
        if key not in results["development"]:
            row = run(key, candidate, "development")
            if row:
                results["development"][key] = row
            write_json(result_path, results)
    chosen = {}
    for multiple in (1, 2, 3):
        available = {k: v for k, v in results["development"].items() if v["candidate"]["plan"]["research_multiple"] == multiple}
        chosen[str(multiple)] = max(available, key=lambda k: available[k]["stress"]["liquidation_cagr"] - .5 * abs(available[k]["stress"]["max_drawdown"]))
    results["locked"] = chosen
    write_json(result_path, results)
    for key in chosen.values():
        results["finalists"].setdefault(key, {})
        for period in ("later", "partial", "continuous"):
            if period not in results["finalists"][key]:
                row = run(key, cases[key], period)
                if row:
                    results["finalists"][key][period] = row
                write_json(result_path, results)
    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_json(result_path, results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    args.output = args.output.resolve()
    main(args)
