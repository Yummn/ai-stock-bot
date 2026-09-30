"""Bounded, preregistered research for defensive and balanced core portfolios."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys

from research_balanced_preset import sha
from research_resilient_execution import ROOT, SOURCE, SNAPSHOT, load, write, worker


def protocol():
    from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS, Plan
    cases, families = {}, {}
    for profile in ("conservative", "balanced"):
        base = Plan(**{k: v for k, v in FIXED_PROFILE_PRESETS[profile].items() if k != "label"},
                    profile="custom", preset_version=1, validation_start_year=2020, validation_end_year=2025).model_dump()
        cases[f"old-{profile}"] = base
        defensive = profile == "conservative"
        common = {**base, "allocation_policy": "defensive_v1" if defensive else "balanced_v1",
            "holdings": 4, "max_exposure": .7 if defensive else .98, "max_weight": .35 if defensive else .4,
            "drawdown_pause": .06 if defensive else .12, "drift_band": .04,
            "allocation": {"中国股票": .15, "海外股票": .15, "黄金": .15, "国债": .55} if defensive
                else {"中国股票": .30, "海外股票": .30, "黄金": .20, "国债": .20}}
        for vol in ((.04, .05, .06) if defensive else (.08, .10, .12)):
            for anchor in (.2, .6):
                key = f"{profile}-v{round(vol*100)}-a{round(anchor*10)}"
                cases[key] = {**common, "target_volatility": vol, "core_anchor": anchor, "risk_aversion": 3.}
                families[key] = profile
        for cadence in ("weekly", "monthly"):
            key = f"{profile}-{cadence}-risk6"
            cases[key] = {**common, "target_volatility": .05 if defensive else .10,
                          "core_anchor": .6, "risk_aversion": 6., "rebalance": cadence}
            families[key] = profile
    return {"version": "core-profiles-v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "market_sha256": sha(SNAPSHOT), "forecast_sha256": sha(SOURCE), "cases": cases, "families": families,
        "periods": {"development": ["2020-01-01", "2022-12-31"], "later": ["2023-01-01", "2025-12-31"],
            "recent": ["2026-01-01", "2026-09-28"], "full": ["2020-01-01", "2026-09-28"],
            **{str(y): [f"{y}-01-01", f"{y}-12-31"] for y in range(2020, 2026)}},
        "gates": {"conservative": {"cagr": .04, "drawdown": .06, "year_loss": .03, "recent_loss": .03},
                  "balanced": {"cagr": .10, "drawdown": .12, "year_loss": .08, "recent_loss": .05}},
        "selection": "For each policy separately, rank development only. Prefer DD/year-loss eligibility then stressed CAGR minus DD penalty (1 defensive, .25 balanced) plus half worst negative year. Lock one per policy before later checks.",
        "shared_gates": "Full and later net CAGR reach target, normal DD and year loss meet limits, independent years meet loss limit, recent losses bounded, full and later doubled-cost profits positive, no debt. Balanced full realized volatility >=1.25x defensive, exposure >=defensive+10pp; no required minimum absolute risk.",
        "limitations": ["All historical years previously explored; later data is not pristine out of sample.",
            "Fixed six ETF universe; no newly invented short-duration assets or interest on cash.",
            "Ten-year bond ETF retains duration risk and remains in covariance constraints.",
            "Historical gates cannot demonstrate stable future profits."],
        "references": ["https://www.cvxportfolio.com/en/stable/manual.html"]}


def checks(periods, years, limits):
    complete = set(years) == set(map(str, range(2020, 2026))) and set(periods) == {"full", "later", "recent"}
    return {"coverage": complete,
        "net_growth": all(periods[p]["base"]["liquidation_cagr"] >= limits["cagr"] for p in ("full", "later")),
        "drawdown": all(pair["base"]["max_drawdown"] >= -limits["drawdown"] for pair in [*periods.values(), *years.values()]),
        "calendar_losses": all(r["return_pct"] >= -limits["year_loss"] for r in periods["full"]["base"]["per_year"] if r["year"] < 2026),
        "independent_losses": all(pair["base"]["liquidation_return"] >= -limits["year_loss"] for pair in years.values()),
        "recent": periods["recent"]["base"]["liquidation_return"] >= -limits["recent_loss"],
        "double_cost": all(periods[p]["stress"]["liquidation_return"] > 0 for p in ("full", "later")),
        "no_borrowing": all(v["interest_paid"] == 0 and not v["insolvent"] for pair in [*periods.values(), *years.values()] for v in pair.values())}


def main(directory):
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory/"protocol.json").exists():
        spec = protocol()
        files = [Path(__file__).resolve(), ROOT/"scripts/research_resilient_execution.py", *sorted((ROOT/"src/ai_stock_bot/v4").glob("*.py"))]
        spec["source_sha256"] = {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p) for p in files}
        for p in files:
            destination = directory/"source"/p.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, destination)
        write(directory/"protocol.json", spec)
    spec = load(directory/"protocol.json")
    for path, digest in spec["source_sha256"].items():
        assert sha(ROOT/path) == digest, f"Changed source: {path}"
    result_path = directory/"results.json"
    result = load(result_path) if result_path.exists() else {"development": {}, "finalists": {}, "independent_years": {}}

    def run(key, period, both=True):
        pair = {}
        for stress in ((False, True) if both else (False,)):
            name = f"{period}-{key}-{'stress' if stress else 'base'}"
            path = directory/"cases"/f"{name}.json"
            request = {"plan": spec["cases"][key], "period": spec["periods"][period], "stress": stress}
            if path.exists():
                assert load(path) == request
            else:
                write(path, request)
            target = path.with_suffix(".result.json")
            if not target.exists():
                (directory/"logs").mkdir(exist_ok=True)
                with (directory/"logs"/f"{name}.log").open("w", encoding="utf-8") as stream:
                    task = subprocess.run([sys.executable, "-X", "faulthandler", __file__, "--output", str(directory), "--worker", str(path)],
                        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT/"src"), "PYTHONUTF8": "1", "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"}, stdout=stream, stderr=subprocess.STDOUT)
                if task.returncode:
                    raise RuntimeError(f"Replay failed: {name}; see log")
            raw = load(target)
            pair["stress" if stress else "base"] = {k: v for k, v in raw.items() if k not in {"curve", "trades", "decisions", "training", "rejections"}}
        print(f"{period} {key}: net {pair['base']['liquidation_cagr']:.2%}; DD {pair['base']['max_drawdown']:.2%}; exposure {pair['base']['mean_exposure']:.2%}", flush=True)
        return pair

    for key in spec["cases"]:
        if key not in result["development"]:
            result["development"][key] = run(key, "development")
            write(result_path, result)
    def score(key):
        profile = spec["families"][key]
        limits = spec["gates"][profile]
        base, stress = result["development"][key]["base"], result["development"][key]["stress"]
        eligible = base["max_drawdown"] >= -limits["drawdown"] and min(r["return_pct"] for r in base["per_year"]) >= -limits["year_loss"]
        value = stress["liquidation_cagr"]-(1 if profile == "conservative" else .25)*abs(stress["max_drawdown"])+.5*min(0, min(r["return_pct"] for r in stress["per_year"]))
        return eligible, value
    locked = {p: max((k for k, v in spec["families"].items() if v == p), key=score) for p in spec["gates"]}
    if result.get("locked"):
        assert result["locked"] == locked
    result.update(locked=locked, locked_at=result.get("locked_at", datetime.now(timezone.utc).isoformat()), scores={k: score(k) for k in spec["families"]})
    write(result_path, result)
    print(f"LOCKED {locked}", flush=True)
    for key in ["old-conservative", "old-balanced", *locked.values()]:
        periods = result["finalists"].setdefault(key, {})
        for period in ("later", "recent", "full"):
            if period not in periods:
                periods[period] = run(key, period)
                write(result_path, result)
        years = result["independent_years"].setdefault(key, {})
        for year in map(str, range(2020, 2026)):
            if year not in years:
                years[year] = run(key, year, both=False)
                write(result_path, result)
    result["gates"] = {}
    for profile, key in locked.items():
        verdict = checks(result["finalists"][key], result["independent_years"][key], spec["gates"][profile])
        result["gates"][profile] = {"checks": verdict, "historical_passed": all(verdict.values()), "forward_proven": False}
    a = result["finalists"][locked["conservative"]]["full"]["base"]
    b = result["finalists"][locked["balanced"]]["full"]["base"]
    result["separation"] = b["volatility"] >= a["volatility"]*1.25 and b["mean_exposure"] >= a["mean_exposure"]+.10
    result.update(completed_at=datetime.now(timezone.utc).isoformat(), applied=False)
    write(result_path, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker.resolve(), args.output.resolve())
    else:
        main(args.output.resolve())
