"""Measurement correction: initialize every holding, without a rebalance drift filter.

Original artifacts and frozen forward books remain untouched. Candidate results are
unchanged; only the flawed buy-and-hold reference and its comparison gates change.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from research_combinations import base_plan, dispatch, load
from ai_stock_bot.v4.research import write_json


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    source = load(args.source / "protocol.json")
    plan = base_plan().model_copy(update={"research_multiple": 1, "drift_band": 0.})
    protocol = {"identity": source["identity"], "engine": source["engine"],
        "correction": "Buy-and-hold initial purchases must not be skipped by the strategy's 8% rebalance band",
        "candidate": {"family": "hold", "plan": plan.model_dump()},
        **{p: source[p] for p in ("later", "partial", "continuous")}}
    path = args.output / "protocol.json"
    if path.exists() and load(path) != protocol:
        raise ValueError("Benchmark correction changed")
    write_json(path, protocol)
    results = {"periods": {}, "failures": []}
    directory = args.output / "cases"
    directory.mkdir(exist_ok=True)
    for period in ("later", "partial", "continuous"):
        results["periods"][period] = {"candidate": protocol["candidate"]}
        for stress in (False, True):
            name = f"{period}-fully-invested-hold-{'stress' if stress else 'base'}"
            request = directory / f"{name}.json"
            write_json(request, {"candidate": protocol["candidate"], "period": protocol[period], "stress": stress})
            result = request.with_suffix(".result.json")
            if not result.exists() and not dispatch(args, ["--case", str(request)], name):
                raise RuntimeError(f"Benchmark correction failed: {name}")
            value = load(result)
            if value["trade_count"] != 6:
                raise ValueError(f"Benchmark did not initialize all six assets: {value['trade_count']}")
            results["periods"][period]["stress" if stress else "base"] = value
        print(period, results["periods"][period]["base"]["liquidation_cagr"], flush=True)
    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_json(args.output / "results.json", results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    main(args)
