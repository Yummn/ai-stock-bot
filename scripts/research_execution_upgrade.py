"""Predeclared paired experiment for corrected and cost-aware execution."""
import argparse
from datetime import datetime, timezone
import hashlib
import shutil
from pathlib import Path

from research_combinations import ROOT, SNAPSHOT, base_plan, dispatch, load
from ai_stock_bot.v4.research import write_json
from ai_stock_bot.v4.schema import ENGINE_VERSION


MODELS = ("multi", "hgb20_regularized", "extra20", "xgb20", "cnn20")


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    source = ROOT / "docs/reports/combinations-20260930/models"
    panels = {m: hashlib.sha256((source/m/"panel.json").read_bytes()).hexdigest()
              for m in MODELS if m != "multi"}
    cases = {}
    for model in MODELS:
        for policy in ("corrected", "cost_aware"):
            for cadence in ("weekly", "monthly"):
                plan = base_plan().model_copy(update={"execution_policy": policy,
                    "rebalance": cadence, "horizon": 20})
                key = f"{model}-{policy}-{cadence}"
                cases[key] = {"family": "multi" if model == "multi" else "ml",
                    **({"model": model, "policy": "trend"} if model != "multi" else {}),
                    "plan": plan.model_dump()}
    if args.higher_risk:
        cases = {}
        for model in ("multi", "hgb20_regularized"):
            for multiple in (2, 3):
                for cadence, volatility in (("daily", .20), ("weekly", .20), ("monthly", .20), ("monthly", .30)):
                    key = f"{model}-{multiple}x-{cadence}-v{int(volatility*100)}"
                    plan = base_plan().model_copy(update={"execution_policy": "cost_aware",
                        "rebalance": cadence, "horizon": 20, "target_volatility": volatility,
                        "research_multiple": multiple})
                    cases[key] = {"family": "multi" if model == "multi" else "ml",
                        **({"model": model, "policy": "trend"} if model != "multi" else {}),
                        "plan": plan.model_dump()}
    implementation = hashlib.sha256(b"".join((ROOT/"src/ai_stock_bot/v4"/f).read_bytes()
        for f in ("cost_allocation.py", "portfolio.py", "allocation.py", "research.py", "schema.py"))).hexdigest()
    protocol = {"version": "execution-risk-1" if args.higher_risk else "execution-upgrade-1", "engine": ENGINE_VERSION,
        "identity": hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest(), "implementation": implementation,
        "candidate_count": len(cases), "cases": cases, "panels": panels,
        "development": ["2020-01-01", "2022-12-31"],
        "later": ["2023-01-01", "2025-12-31"], "partial": ["2026-01-01", "2026-09-28"],
        "continuous": ["2020-01-01", "2026-09-28"],
        "selection": "Per model and leverage" if args.higher_risk else "Per model and execution policy: highest development stressed CAGR minus half absolute drawdown; cadence locked before later periods",
        **({"score": "Highest development stressed CAGR minus half absolute drawdown; lock before later periods"} if args.higher_risk else {}),
        "leverage": "Fixed 2x/3x with 20/30 percent risk target" if args.higher_risk else "For the best development cost-aware candidate also replay 1x and 3x, no later retuning",
        "stress": "Double commission/slippage and 12 percent annual financing; same predictions",
        "limitations": ["Previously seen history, not a fresh holdout", "Daily next-open approximation",
            "Return estimates are uncertain, not guarantees", "No live trades or modifications to frozen observation"],
        "sources": ["https://www.cvxportfolio.com/en/stable/costs.html",
                    "https://web.stanford.edu/~boyd/papers/cvx_portfolio.html"]}
    path = args.output/"protocol.json"
    if path.exists() and load(path) != protocol:
        raise ValueError("Protocol changed; create a new output directory")
    write_json(path, protocol)
    if args.freeze_only:
        print(f"LOCKED {len(cases)} candidates: {path}")
        return
    for model in panels:
        destination = args.output/"models"/model/"panel.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(source/model/"panel.json", destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != panels[model]:
            raise ValueError("Forecast panel changed")
    result_path = args.output/"results.json"
    results = load(result_path) if result_path.exists() else {"development": {}, "finalists": {}}
    directory = args.output/"cases"
    directory.mkdir(exist_ok=True)

    def run(key, candidate, period):
        pair = {"candidate": candidate}
        for stress in (False, True):
            name = f"{period}-{key}-{'stress' if stress else 'base'}"
            request = directory/f"{name}.json"
            value = {"candidate": candidate, "period": protocol[period], "stress": stress}
            if request.exists() and load(request) != value:
                raise ValueError("Case changed")
            write_json(request, value)
            output = request.with_suffix(".result.json")
            if not output.exists() and not dispatch(args, ["--case", str(request)], name):
                raise RuntimeError(f"Unfinished case: {name}; resume same inputs after diagnosis")
            pair["stress" if stress else "base"] = load(output)
        print(f"{period} {key}: CAGR={pair['base']['liquidation_cagr']:.4f} "
              f"DD={pair['base']['max_drawdown']:.4f} stress={pair['stress']['liquidation_cagr']:.4f}", flush=True)
        return pair

    for key, candidate in cases.items():
        if key not in results["development"]:
            results["development"][key] = run(key, candidate, "development")
            write_json(result_path, results)
    score = lambda k: (results["development"][k]["stress"]["liquidation_cagr"]
                      - .5*abs(results["development"][k]["stress"]["max_drawdown"]))
    if args.higher_risk:
        chosen = {f"{m}-{multiple}x": max((k for k in cases if k.startswith(f"{m}-{multiple}x-")), key=score)
                  for m in ("multi", "hgb20_regularized") for multiple in (2, 3)}
    else:
        chosen = {f"{m}-{p}": max((k for k in cases if k.startswith(f"{m}-{p}-")), key=score)
                  for m in MODELS for p in ("corrected", "cost_aware")}
    if results.get("locked") and results["locked"] != chosen:
        raise ValueError("Locked candidates changed")
    results["locked"] = chosen
    write_json(result_path, results)
    finalists = {k: cases[k] for k in chosen.values()}
    if not args.higher_risk:
        best = max((k for k in chosen.values() if "-cost_aware-" in k), key=score)
        for multiple in (1, 3):
            finalists[f"{best}-{multiple}x"] = {**cases[best],
                "plan": {**cases[best]["plan"], "research_multiple": multiple}}
    finalists["hold-1x-corrected"] = {"family": "hold", "plan": {
        **base_plan().model_dump(), "execution_policy": "corrected", "research_multiple": 1}}
    for key, candidate in finalists.items():
        results["finalists"].setdefault(key, {})
        for period in ("later", "partial", "continuous"):
            if period not in results["finalists"][key]:
                results["finalists"][key][period] = run(key, candidate, period)
                write_json(result_path, results)
    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    results["applied"] = False
    write_json(result_path, results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze-only", action="store_true")
    parser.add_argument("--higher-risk", action="store_true")
    args = parser.parse_args()
    args.output = args.output.resolve()
    main(args)
