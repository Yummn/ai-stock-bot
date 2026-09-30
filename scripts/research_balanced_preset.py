"""Predeclared balanced-preset research, isolated from all user accounts."""
import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from research_resilient_execution import ROOT, SOURCE, SNAPSHOT, load, write, worker


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protocol():
    from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS, Plan
    preset = {k: v for k, v in FIXED_PROFILE_PRESETS["balanced"].items() if k != "label"}
    base = Plan(**preset, profile="custom", preset_version=1,
                validation_start_year=2020, validation_end_year=2025).model_dump()
    cases = {"original": base}
    for signal in ("legacy", "trend_weighted", "confirmed"):
        for vol in (.10, .12, .15):
            cases[f"four-{signal}-v{round(vol*100)}"] = {**base, "holdings": 4,
                "signal_policy": signal, "target_volatility": vol,
                "drawdown_pause": .10, "drift_band": .05}
    for review in ("weekly", "daily"):
        cases[f"four-confirmed-{review}"] = {**cases["four-confirmed-v12"], "risk_review": review}
    cases["four-confirmed-diversified"] = {**cases["four-confirmed-v12"], "max_weight": .30,
        "allocation": {"中国股票": .20, "海外股票": .20, "黄金": .25, "国债": .35}}
    cases["four-confirmed-exposure80"] = {**cases["four-confirmed-v12"], "max_exposure": .80}
    for signal in ("legacy", "confirmed"):
        cases[f"trend-{signal}"] = {**cases[f"four-{signal}-v12"], "method": "rotation"}
    return {"version": "balanced-1", "created_at": datetime.now(timezone.utc).isoformat(),
        "market_sha256": sha(SNAPSHOT), "forecast_sha256": sha(SOURCE), "cases": cases,
        "periods": {"development": ["2020-01-01", "2022-12-31"],
            "later": ["2023-01-01", "2025-12-31"], "recent": ["2026-01-01", "2026-09-28"],
            "full": ["2020-01-01", "2026-09-28"],
            **{str(y): [f"{y}-01-01", f"{y}-12-31"] for y in range(2020, 2026)}},
        "selection": "Development data only. Prefer cases with net CAGR >=8%, DD <=10%, worst completed year >=-5%. Maximize stressed CAGR minus absolute stressed DD plus min(0, worst stressed calendar year). If none qualify, keep highest score as a diagnostic, never promote without all final gates.",
        "gates": {"full_and_later_net_cagr_min": .08, "normal_drawdown_max": .10,
            "completed_calendar_and_independent_year_loss_max": .05, "recent_loss_max": .03,
            "double_cost_full_and_later_profitable": True, "no_borrowing": True},
        "stress": "Double commission, minimum commission and slippage. Financing is disabled (1x).",
        "limitations": ["All years were previously explored: later data is not a pristine holdout.",
            "Fixed six current ETFs; universe selection and public price data remain limitations.",
            "Historical gates cannot prove future profits or real executable fills."],
        "references": ["https://www.cvxportfolio.com/en/stable/costs.html",
            "https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html"]}


def gate(periods, yearly):
    return {
        "net_cagr": all(periods[p]["base"]["liquidation_cagr"] >= .08 for p in ("full", "later")),
        "normal_drawdown": all(pair["base"]["max_drawdown"] >= -.10 for pair in [*periods.values(), *yearly.values()]),
        "completed_calendar_years": all(y["return_pct"] >= -.05 for y in periods["full"]["base"]["per_year"] if y["year"] < 2026),
        "independent_years": all(pair["base"]["liquidation_return"] >= -.05 for pair in yearly.values()),
        "recent_loss": periods["recent"]["base"]["liquidation_return"] >= -.03,
        "double_cost_positive": all(periods[p]["stress"]["liquidation_return"] > 0 for p in ("full", "later")),
        "no_interest": all(pair[s]["interest_paid"] == 0 for pair in [*periods.values(), *yearly.values()] for s in pair),
    }


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    if not (output/"protocol.json").exists():
        spec = protocol()
        sources = [Path(__file__).resolve(), ROOT/"scripts/research_resilient_execution.py",
                   *sorted((ROOT/"src/ai_stock_bot/v4").glob("*.py"))]
        spec["source_sha256"] = {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p) for p in sources}
        for path in sources:
            dest = output/"source"/path.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
        write(output/"protocol.json", spec)
    spec = load(output/"protocol.json")
    assert sha(SNAPSHOT) == spec["market_sha256"] and sha(SOURCE) == spec["forecast_sha256"]
    # Source edits require a new experiment, not silently resumed old results.
    for relative, expected in spec["source_sha256"].items():
        assert sha(ROOT/relative) == expected, f"Implementation changed: {relative}"
    results_path = output/"results.json"
    results = load(results_path) if results_path.exists() else {"development": {}, "finalists": {}}

    def run(key, period, stress=True):
        pair = {}
        for costly in ((False, True) if stress else (False,)):
            name = f"{period}-{key}-{'stress' if costly else 'base'}"
            path = output/"cases"/f"{name}.json"
            request = {"plan": spec["cases"][key], "period": spec["periods"][period],
                       "stress": costly, "method": spec["cases"][key]["method"]}
            if path.exists():
                assert load(path) == request
            else:
                write(path, request)
            target = path.with_suffix(".result.json")
            if not target.exists():
                (output/"logs").mkdir(exist_ok=True)
                for attempt in (1, 2):
                    start = time.monotonic()
                    with (output/"logs"/f"{name}-{attempt}.log").open("w", encoding="utf-8") as stream:
                        process = subprocess.run([sys.executable, "-X", "faulthandler", __file__,
                            "--output", str(output), "--worker", str(path)], cwd=ROOT,
                            env={**os.environ, "PYTHONPATH": str(ROOT/"src"), "PYTHONUTF8": "1",
                                 "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"},
                            stdout=stream, stderr=subprocess.STDOUT)
                    write(output/"logs"/f"{name}-{attempt}.json", {"exit_code": process.returncode,
                        "seconds": time.monotonic()-start})
                    if process.returncode == 0:
                        break
                if process.returncode:
                    raise RuntimeError(f"Failed: {name}; see logs")
            raw = load(target)
            pair["stress" if costly else "base"] = {k: v for k, v in raw.items()
                if k not in {"curve", "trades", "decisions", "training", "rejections"}}
        print(f"{period} {key}: net={pair['base']['liquidation_cagr']:.3%} "
              f"DD={pair['base']['max_drawdown']:.3%}", flush=True)
        return pair

    for key in spec["cases"]:
        if key not in results["development"]:
            results["development"][key] = run(key, "development")
            write(results_path, results)
    def score(key):
        pair = results["development"][key]
        normal, stress = pair["base"], pair["stress"]
        qualifies = (normal["liquidation_cagr"] >= .08 and normal["max_drawdown"] >= -.10
                     and min(r["return_pct"] for r in normal["per_year"]) >= -.05)
        value = stress["liquidation_cagr"]-abs(stress["max_drawdown"])+min(0, min(r["return_pct"] for r in stress["per_year"]))
        return qualifies, value
    winner = max(spec["cases"], key=score)
    if results.get("locked"):
        assert results["locked"] == winner
    results.update(locked=winner, locked_at=results.get("locked_at", datetime.now(timezone.utc).isoformat()),
                   development_scores={k: score(k) for k in spec["cases"]})
    write(results_path, results)
    print(f"LOCKED {winner}", flush=True)
    for key in dict.fromkeys(["original", winner]):
        periods = results["finalists"].setdefault(key, {})
        for period in ("later", "recent", "full"):
            if period not in periods:
                periods[period] = run(key, period)
                write(results_path, results)
    results.setdefault("independent_years", {})
    for key in dict.fromkeys(["original", winner]):
        years = results["independent_years"].setdefault(key, {})
        for year in range(2020, 2026):
            if str(year) not in years:
                years[str(year)] = run(key, str(year), stress=False)
                write(results_path, results)
    results["gates"] = {}
    for key, periods in results["finalists"].items():
        checks = gate(periods, results["independent_years"][key])
        results["gates"][key] = {"checks": checks, "historical_passed": all(checks.values()), "forward_proven": False}
    results.update(completed_at=datetime.now(timezone.utc).isoformat(), applied=False)
    write(results_path, results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker.resolve(), args.output.resolve())
    else:
        main(args.output.resolve())
