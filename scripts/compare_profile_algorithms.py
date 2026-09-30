"""Nested, bounded algorithm/parameter comparison under each profile's risk budget."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys

from research_balanced_preset import sha
from research_resilient_execution import ROOT, SOURCE, SNAPSHOT, load, write, worker

FAMILIES = {
    "trend": ["trend"], "hgb": ["hgb20_regularized", "hgb20", "hgb20_5y"],
    "ridge": ["ridge20", "ridge20_light"], "extra": ["extra20", "extra20_smooth"],
    "xgb": ["xgb20", "xgb20_smooth"], "cnn": ["cnn20", "cnn20_long"],
}
LIMITS = {
    "conservative": {"cagr": .04, "drawdown": .06, "year_loss": .03, "recent_loss": .03, "penalty": 1.},
    "balanced": {"cagr": .10, "drawdown": .12, "year_loss": .08, "recent_loss": .05, "penalty": .25},
    "aggressive": {"cagr": .20, "drawdown": .20, "year_loss": .15, "recent_loss": 0., "penalty": .15},
}


def specification():
    from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS, Plan
    from ai_stock_bot.v4.model_recipes import model_specs
    cases = {}
    for profile, fixed in FIXED_PROFILE_PRESETS.items():
        base = Plan(**{k: v for k, v in fixed.items() if k != "label"}, profile="custom", preset_version=1,
                    validation_start_year=2020, validation_end_year=2025).model_dump()
        for recipe in (v for family in FAMILIES.values() for v in family):
            plan = Plan.model_validate({**base, "method": "rotation" if recipe == "trend" else "ml",
                "model_recipe": "legacy" if recipe == "trend" else recipe})
            cases[f"{profile}--{recipe}"] = plan.model_dump()
    return {"version": "profile-algorithms-1", "created_at": datetime.now(timezone.utc).isoformat(),
        "market_sha256": sha(SNAPSHOT), "forecast_sha256": sha(SOURCE), "cases": cases,
        "models": {k: asdict(v) for k, v in model_specs().items() if any(k in names for names in FAMILIES.values())},
        "families": FAMILIES, "limits": LIMITS,
        "periods": {"development": ["2020-01-01", "2022-12-31"], "selection": ["2023-01-01", "2024-12-31"],
            "confirmation": ["2025-01-01", "2025-12-31"], "recent": ["2026-01-01", "2026-09-28"],
            "full": ["2020-01-01", "2026-09-28"], "later": ["2023-01-01", "2025-12-31"],
            **{str(y): [f"{y}-01-01", f"{y}-12-31"] for y in range(2020, 2026)}},
        "selection": "Each profile selects one parameter recipe per algorithm family on 2020-2022 only. Compare these representatives plus original HGB on 2023-2024. Rank risk eligibility then average development/selection stressed utility. Utility = net CAGR - profile DD penalty*abs(DD) + 0.5*worst negative calendar year. Lock one algorithm per profile before 2025/2026/full verification. Ties prefer original HGB.",
        "promotion": "Retain original unless full net CAGR improves >=0.3pp and full/later CAGR meet profile target, DD/year limits pass, 2025 and 2026 net returns no worse than original by >1pp, full doubled-cost CAGR no worse and later/confirmation doubled-cost profits positive. Aggressive also requires all completed continuous years and recent return positive. All normal independent years checked. No increasing risk budgets or leverage.",
        "limitations": ["All history previously researched, including confirmation years; not pristine holdouts.",
            "Fixed six-ETF universe, daily data, realistic simulation assumptions not live fills.",
            "One additional parameter recipe per non-HGB model tests parameter sensitivity, not an exhaustive optimum.",
            "Trend bypasses forecasts entirely. All ML recipes share label horizon, validation gate and allocation engine.",
            "Historical gate passing is not proof of future profits."],
        "references": ["https://scikit-learn.org/stable/modules/cross_validation.html"]}


def utility(pair, limits):
    a, b = pair["base"], pair["stress"]
    eligible = a["max_drawdown"] >= -limits["drawdown"] and min(r["return_pct"] for r in a["per_year"]) >= -limits["year_loss"]
    score = b["liquidation_cagr"]-limits["penalty"]*abs(b["max_drawdown"])+.5*min(0., min(r["return_pct"] for r in b["per_year"]))
    return eligible, score


def main(directory, model_python):
    directory.mkdir(parents=True, exist_ok=True)
    protocol = directory/"protocol.json"
    if not protocol.exists():
        spec = specification()
        sources = [Path(__file__).resolve(), ROOT/"scripts/research_resilient_execution.py",
            *[ROOT/"src/ai_stock_bot/v4"/name for name in ("schema.py", "portfolio.py", "core_allocation.py",
              "model_recipes.py", "research.py", "research_forecast.py", "allocation.py", "cost_allocation.py", "cnn.py")],
            ROOT/"src/ai_stock_bot/v2/execution.py"]
        spec["source_sha256"] = {}
        for src in sources:
            relative = src.relative_to(ROOT)
            spec["source_sha256"][relative.as_posix()] = sha(src)
            dest = directory/"source"/relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        write(protocol, spec)
    spec = load(protocol)
    for file, digest in spec["source_sha256"].items():
        assert sha(ROOT/file) == digest, f"Changed research dependency: {file}"
    env = {**os.environ, "PYTHONPATH": str(ROOT/"src"), "PYTHONUTF8": "1",
           "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"}
    for recipe in spec["models"]:
        target = directory/"models"/recipe/"panel.json"
        if target.exists():
            assert load(target)["spec"] == spec["models"][recipe]
            continue
        cached = SOURCE.parent.parent/recipe
        if (cached/"panel.json").exists() and load(cached/"panel.json")["spec"] == spec["models"][recipe]:
            shutil.copytree(cached, target.parent)
            print(f"REUSE {recipe}", flush=True)
        else:
            subprocess.run([str(model_python), "-u", "-X", "faulthandler", __file__, "--output", str(directory),
                            "--fit", recipe], cwd=ROOT, env=env, check=True)
    write(directory/"forecast_manifest.json", {k: sha(directory/"models"/k/"panel.json") for k in spec["models"]})
    output = directory/"results.json"
    result = load(output) if output.exists() else {"development": {}, "selection": {}, "finalists": {}, "independent_years": {}}

    def run(key, period, stress=True):
        pair = {}
        for doubled in ((False, True) if stress else (False,)):
            suffix = "stress" if doubled else "base"
            path = directory/"cases"/f"{period}-{key}-{suffix}.json"
            recipe = key.split("--")[1]
            request = {"plan": spec["cases"][key], "period": spec["periods"][period], "stress": doubled,
                "method": "rotation" if recipe == "trend" else "ml"}
            if recipe != "trend":
                request["forecast_model"] = recipe
            if path.exists():
                assert load(path) == request
            else:
                write(path, request)
            file = path.with_suffix(".result.json")
            if not file.exists():
                logs = directory/"logs"
                logs.mkdir(exist_ok=True)
                with (logs/f"{path.stem}.log").open("w", encoding="utf-8") as stream:
                    subprocess.run([sys.executable, "-X", "faulthandler", __file__, "--output", str(directory),
                                    "--worker", str(path)], cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
            value = load(file)
            pair[suffix] = {k: v for k, v in value.items() if k not in {"curve", "trades", "decisions", "training", "rejections"}}
        print(f"{period} {key}: CAGR {pair['base']['liquidation_cagr']:.3%}; DD {pair['base']['max_drawdown']:.3%}", flush=True)
        return pair

    for key in spec["cases"]:
        if key not in result["development"]:
            result["development"][key] = run(key, "development")
            write(output, result)
    best = {}
    for profile, limits in LIMITS.items():
        best[profile] = {family: max([f"{profile}--{name}" for name in names], key=lambda k: utility(result["development"][k], limits))
                         for family, names in FAMILIES.items()}
    if result.get("family_winners"):
        assert result["family_winners"] == best
    result["family_winners"] = best
    write(output, result)
    for profile, groups in best.items():
        for key in dict.fromkeys([*groups.values(), f"{profile}--hgb20_regularized"]):
            if key not in result["selection"]:
                result["selection"][key] = run(key, "selection")
                write(output, result)
    locked = {}
    for profile, limits in LIMITS.items():
        baseline = f"{profile}--hgb20_regularized"
        def score(key):
            d, v = utility(result["development"][key], limits), utility(result["selection"][key], limits)
            return d[0] and v[0], .5*(d[1]+v[1]), key == baseline
        locked[profile] = max(dict.fromkeys([*best[profile].values(), baseline]), key=score)
    if result.get("locked"):
        assert result["locked"] == locked
    result.update(locked=locked, locked_at=result.get("locked_at", datetime.now(timezone.utc).isoformat()))
    write(output, result)
    print(f"LOCKED {locked}", flush=True)
    for key in dict.fromkeys([*locked.values(), *[f"{p}--hgb20_regularized" for p in LIMITS]]):
        periods = result["finalists"].setdefault(key, {})
        for period in ("confirmation", "recent", "full", "later"):
            if period not in periods:
                periods[period] = run(key, period)
                write(output, result)
        annual = result["independent_years"].setdefault(key, {})
        for year in map(str, range(2020, 2026)):
            if year not in annual:
                annual[year] = run(key, year, stress=False)
                write(output, result)
    result["decisions"] = {}
    for profile, key in locked.items():
        limits = LIMITS[profile]
        original = f"{profile}--hgb20_regularized"
        new, old = result["finalists"][key], result["finalists"][original]
        annual = result["independent_years"][key]
        gates = {
            "return_target": all(new[p]["base"]["liquidation_cagr"] >= limits["cagr"] for p in ("full", "later")),
            "drawdown": all(v["base"]["max_drawdown"] >= -limits["drawdown"] for v in [*new.values(), *annual.values()]),
            "year_loss": all(y["return_pct"] >= -limits["year_loss"] for y in new["full"]["base"]["per_year"] if y["year"] < 2026),
            "independent_years": all(v["base"]["liquidation_return"] >= -limits["year_loss"] for v in annual.values()),
            "recent": new["recent"]["base"]["liquidation_return"] >= -limits["recent_loss"],
            "growth_improvement": new["full"]["base"]["liquidation_cagr"] >= old["full"]["base"]["liquidation_cagr"]+.003,
            "confirmation": new["confirmation"]["base"]["liquidation_return"] >= old["confirmation"]["base"]["liquidation_return"]-.01,
            "recent_vs_original": new["recent"]["base"]["liquidation_return"] >= old["recent"]["base"]["liquidation_return"]-.01,
            "stress": new["full"]["stress"]["liquidation_cagr"] >= old["full"]["stress"]["liquidation_cagr"] and all(new[p]["stress"]["liquidation_return"] > 0 for p in ("later", "confirmation")),
        }
        if profile == "aggressive":
            gates["positive_years"] = all(y["return_pct"] > 0 for y in new["full"]["base"]["per_year"] if y["year"] < 2026)
        promote = all(gates.values())
        result["decisions"][profile] = {"candidate": key, "recommended": key if promote else original,
            "checks": gates, "promoted": promote, "forward_proven": False}
    result.update(completed_at=datetime.now(timezone.utc).isoformat(), applied=False)
    write(output, result)
    print(f"DECISIONS {result['decisions']}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-python", type=Path, default=ROOT/".venv/Scripts/python.exe")
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--fit")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker.resolve(), args.output.resolve())
    elif args.fit:
        from ai_stock_bot.v4.research import build_panel
        from ai_stock_bot.v4.model_recipes import ModelSpec
        from research_combinations import get_histories
        spec = load(args.output/"protocol.json")
        build_panel(get_histories(), ModelSpec(**spec["models"][args.fit]), args.output/"models"/args.fit, spec["market_sha256"])
    else:
        main(args.output.resolve(), args.model_python.resolve())
