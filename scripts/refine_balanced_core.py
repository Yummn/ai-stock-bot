"""Bounded balanced-preset refinement, frozen before any candidate replay."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys

from research_balanced_preset import sha
from research_core_profiles import checks
from research_resilient_execution import ROOT, SOURCE, SNAPSHOT, load, write, worker


def protocol(turnover=False):
    from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS, Plan
    baseline = Plan(**{k: v for k, v in FIXED_PROFILE_PRESETS["balanced"].items() if k != "label"},
                    profile="custom", preset_version=1).model_dump()
    changes = {
        "anchor-lighter": {"core_anchor": .3},
        "anchor-stronger": {"core_anchor": .9},
        "risk-penalty-lighter": {"risk_aversion": 3.},
        "risk-penalty-stronger": {"risk_aversion": 9.},
        "weekly-rebalance": {"rebalance": "weekly"},
        "weekly-exit-review": {"risk_review": "weekly"},
        "trend-consensus": {"signal_policy": "consensus"},
        "fast-confirmation": {"signal_policy": "fast_confirmed"},
        "smaller-drift": {"drift_band": .02},
    }
    if turnover:
        changes = {f"drift-{int(value*100)}": {"drift_band": value} for value in (.06, .08, .10)}
    prior = ROOT/"docs/reports/core-profiles-20260930"
    spec = load(prior/"protocol.json")
    return {
        "version": "balanced-core-turnover-v1" if turnover else "balanced-core-refinement-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "market_sha256": sha(SNAPSHOT), "forecast_sha256": sha(SOURCE),
        "cases": {"old-balanced": baseline, **{k: {**baseline, **v} for k, v in changes.items()}},
        "families": {k: "balanced" for k in changes}, "periods": spec["periods"],
        "gates": {"balanced": spec["gates"]["balanced"]},
        "baseline_reference": "docs/reports/core-profiles-20260930/cases/full-balanced-monthly-risk6-base.result.json",
        "selection": "Development 2020-2022 only. Rank stressed CAGR minus 0.25 absolute drawdown plus 0.5 worst negative calendar year; normal DD <=12%, calendar year loss <=8%. Include baseline in ranking. Lock exactly one winner before later/full/recent/independent-year checks, with no switching after results.",
        "promotion": "All original balanced gates pass. Full net CAGR improves by >=0.5 percentage point; later net CAGR not below baseline; double-cost full CAGR improves; recent independent return no worse than baseline by >1 percentage point. Risk budgets, caps and leverage unchanged. Otherwise keep existing default.",
        "limitations": ["All periods already researched, so no untouched holdout or proof of forward profits.",
            "No changing asset universe, data, forecast recipe, leverage or volatility budget.",
            "One-factor candidates are diagnostic tests, not an exhaustive optimum."],
        "references": ["https://www.cvxportfolio.com/en/stable/manual.html"],
    }


def promotion_checks(new, old, historical_passed):
    """Never promote on a tiny in-sample gain or omit an adverse later period."""
    return {
        "historical_gates": historical_passed,
        "full_growth": new["full"]["base"]["liquidation_cagr"] >= old["full"]["base"]["liquidation_cagr"]+.005,
        "later_growth": new["later"]["base"]["liquidation_cagr"] >= old["later"]["base"]["liquidation_cagr"],
        "stressed_growth": new["full"]["stress"]["liquidation_cagr"] > old["full"]["stress"]["liquidation_cagr"],
        "recent_loss": new["recent"]["base"]["liquidation_return"] >= old["recent"]["base"]["liquidation_return"]-.01,
    }


def main(directory, turnover=False):
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory/"protocol.json").exists():
        spec = protocol(turnover)
        if turnover:
            prior = ROOT/"docs/reports/balanced-refinement-20260930/results.json"
            spec["previous_research_sha256"] = sha(prior)
            spec["motivation"] = "First-round weekly cadence and smaller drift lost net growth. Test fewer small position adjustments only. This is an adaptive second research round, not independent validation."
        files = [Path(__file__).resolve(), ROOT/"scripts/research_resilient_execution.py",
                 *sorted((ROOT/"src/ai_stock_bot/v4").glob("*.py"))]
        spec["source_sha256"] = {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p) for p in files}
        for p in files:
            dest = directory/"source"/p.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
        write(directory/"protocol.json", spec)
    spec = load(directory/"protocol.json")
    for file, digest in spec["source_sha256"].items():
        assert sha(ROOT/file) == digest, f"Changed source: {file}"
    path = directory/"results.json"
    result = load(path) if path.exists() else {"development": {}, "finalists": {}, "independent_years": {}}

    def run(key, period, both=True):
        pair = {}
        for stress in ((False, True) if both else (False,)):
            name = f"{period}-{key}-{'stress' if stress else 'base'}"
            case = directory/"cases"/f"{name}.json"
            request = {"plan": spec["cases"][key], "period": spec["periods"][period], "stress": stress}
            if case.exists():
                assert load(case) == request
            else:
                write(case, request)
            target = case.with_suffix(".result.json")
            if not target.exists():
                (directory/"logs").mkdir(exist_ok=True)
                with (directory/"logs"/f"{name}.log").open("w", encoding="utf-8") as stream:
                    task = subprocess.run([sys.executable, "-X", "faulthandler", __file__, "--output", str(directory), "--worker", str(case)],
                        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT/"src"), "PYTHONUTF8": "1",
                            "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"},
                        stdout=stream, stderr=subprocess.STDOUT)
                if task.returncode:
                    raise RuntimeError(f"Replay failed: {name}; see log")
            raw = load(target)
            pair["stress" if stress else "base"] = {k: v for k, v in raw.items()
                if k not in {"curve", "trades", "decisions", "training", "rejections"}}
        print(f"{period} {key}: CAGR {pair['base']['liquidation_cagr']:.3%}; DD {pair['base']['max_drawdown']:.3%}", flush=True)
        return pair

    for key in spec["cases"]:
        if key not in result["development"]:
            result["development"][key] = run(key, "development")
            write(path, result)

    def score(key):
        base, stressed = result["development"][key]["base"], result["development"][key]["stress"]
        eligible = base["max_drawdown"] >= -.12 and min(r["return_pct"] for r in base["per_year"]) >= -.08
        value = stressed["liquidation_cagr"]-.25*abs(stressed["max_drawdown"])+.5*min(0, min(r["return_pct"] for r in stressed["per_year"]))
        return eligible, value
    winner = max(spec["cases"], key=score)
    locked = {"balanced": winner}
    if result.get("locked"):
        assert result["locked"] == locked
    result.update(locked=locked, locked_at=result.get("locked_at", datetime.now(timezone.utc).isoformat()),
                  scores={k: score(k) for k in spec["cases"]})
    write(path, result)
    print(f"LOCKED {winner}", flush=True)
    for key in dict.fromkeys(["old-balanced", winner]):
        periods = result["finalists"].setdefault(key, {})
        for period in ("later", "recent", "full"):
            if period not in periods:
                periods[period] = run(key, period)
                write(path, result)
        years = result["independent_years"].setdefault(key, {})
        for year in map(str, range(2020, 2026)):
            if year not in years:
                years[year] = run(key, year, both=False)
                write(path, result)
    verdict = checks(result["finalists"][winner], result["independent_years"][winner], spec["gates"]["balanced"])
    new, old = result["finalists"][winner], result["finalists"]["old-balanced"]
    promotion = promotion_checks(new, old, all(verdict.values()))
    result["gates"] = {"balanced": {"checks": verdict, "historical_passed": all(verdict.values()), "forward_proven": False}}
    conservative = load(ROOT/"docs/reports/core-profiles-20260930/publication.json")["profiles"]["conservative"]["after"]
    full = new["full"]["base"]
    result["separation"] = full["volatility"] >= conservative["volatility"]*1.25 and full["mean_exposure"] >= conservative["mean_exposure"]+.10
    result.update(promotion=promotion, promoted=all(promotion.values()) and result["separation"],
                  completed_at=datetime.now(timezone.utc).isoformat(), applied=False)
    write(path, result)
    print(f"PROMOTION {result['promoted']} {promotion}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--turnover", action="store_true")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker.resolve(), args.output.resolve())
    else:
        main(args.output.resolve(), args.turnover)
