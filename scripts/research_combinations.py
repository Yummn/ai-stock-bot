"""Bounded, resumable offline comparison; never writes to a user account database."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs/reports/daily-4-7-frozen/market.json.gz"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def base_plan():
    from ai_stock_bot.v4.schema import Plan, GROUPS
    return Plan(name="Bounded research", max_exposure=.9, max_weight=.45, holdings=4,
        allocation={g: .25 for g in GROUPS}, rebalance="monthly", drift_band=.08,
        trend_signal="multi_horizon", exposure_policy="volatility_target", target_volatility=.12,
        drawdown_pause=.15, cooldown_sessions=15, research_multiple=2)


def candidates():
    from ai_stock_bot.v4.research import model_specs
    base = base_plan()
    cases = {}
    for family in ("legacy", "multi", "breakout", "pullback", "allocation"):
        for frequency in ("daily", "weekly", "monthly"):
            for multiple in (1, 2, 3):
                name = f"{family}-{frequency}-{multiple}x-v12"
                plan = base.model_copy(update={"rebalance": frequency, "research_multiple": multiple,
                    "trend_signal": "legacy" if family == "legacy" else "multi_horizon"})
                cases[name] = {"family": family, "plan": plan.model_dump()}
    for frequency in ("weekly", "monthly"):
        for multiple in (1, 2, 3):
            name = f"multi-{frequency}-{multiple}x-v20"
            cases[name] = {"family": "multi", "plan": base.model_copy(update={
                "rebalance": frequency, "research_multiple": multiple, "target_volatility": .20}).model_dump()}
    for model in model_specs():
        for policy in ("trend", "model"):
            name = f"{model}-{policy}"
            cases[name] = {"family": "ml", "model": model, "policy": policy, "plan": base.model_dump()}
    # A paired ablation isolates the mismatch between 5-day labels and monthly holding.
    for frequency in ("daily", "weekly"):
        cases[f"hgb5_old-model-{frequency}"] = {"family": "ml", "model": "hgb5_old", "policy": "model",
            "plan": base.model_copy(update={"rebalance": frequency}).model_dump()}
    return cases


def get_histories():
    import pandas as pd
    with gzip.open(SNAPSHOT, "rt", encoding="utf-8") as stream:
        return {s: pd.read_json(StringIO(f), orient="split") for s, f in json.load(stream).items()}


def worker(args):
    from ai_stock_bot.v2.store import Store
    from ai_stock_bot.v4.research import build_panel, model_specs, replay, write_json
    from ai_stock_bot.v4.schema import Plan
    histories = get_histories()
    protocol = load(args.output / "protocol.json")
    if args.model:
        build_panel(histories, model_specs()[args.model], args.output / "models" / args.model,
            protocol["identity"])
        return
    request = load(args.case)
    plan = Plan(**request["candidate"]["plan"])
    candidate = request["candidate"]
    panel = load(args.output / "models" / candidate["model"] / "panel.json") if candidate.get("model") else None
    if request.get("stress"):
        # Double fees/slippage AND financing from 8% to 12%; forecasts stay identical.
        plan = plan.model_copy(update={"commission_rate": plan.commission_rate * 2,
            "min_commission": plan.min_commission * 2, "slippage_bps": plan.slippage_bps * 2,
            "research_annual_rate": .12})
    store = Store(args.output / "isolated-store")
    if candidate["family"] in {"hold", "old_ml"}:
        from ai_stock_bot.v4.portfolio import simulate
        from research_daily_upgrade import compact
        method = "hold" if candidate["family"] == "hold" else "ml"
        original = Plan(**candidate["plan"])
        result = simulate(store, plan, histories, *request["period"], method=method, forecast_plan=original)
        value = compact(result, plan)
        value["training_receipts"] = result["training"]
    else:
        value = replay(store, plan, histories, *request["period"], family=candidate["family"],
            panel=panel, policy=candidate.get("policy", "model"))
    write_json(Path(args.case).with_suffix(".result.json"), value)


def dispatch(args, command, name, runtime=None):
    from ai_stock_bot.v4.research import write_json
    logs = args.output / "logs"
    logs.mkdir(exist_ok=True)
    attempts = []
    for attempt in (1, 2):
        started = time.time()
        log = logs / f"{name}-{attempt}.txt"
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), OMP_NUM_THREADS="2",
            OPENBLAS_NUM_THREADS="2", MKL_NUM_THREADS="2", PYTHONUTF8="1")
        with log.open("w", encoding="utf-8") as stream:
            result = subprocess.run([str(runtime or sys.executable), "-X", "faulthandler", str(Path(__file__).resolve()),
                "--output", str(args.output), "--worker", *command], cwd=ROOT, env=env,
                stdout=stream, stderr=subprocess.STDOUT)
        attempts.append({"attempt": attempt, "exit_code": result.returncode, "seconds": time.time() - started})
        write_json(logs / f"{name}.json", attempts)
        if result.returncode == 0:
            return True
        print(f"WORKER FAILED {name} attempt={attempt} exit={result.returncode}; same-input retry only", flush=True)
    return False


def main(args):
    from ai_stock_bot.v4.data import audit
    from ai_stock_bot.v4.research import model_specs, write_json, RESEARCH_VERSION
    from ai_stock_bot.v4.schema import ASSETS, ENGINE_VERSION
    args.output.mkdir(parents=True, exist_ok=True)
    histories = get_histories()
    checked = audit(histories, list(ASSETS), "2020-01-01", "2025-12-31")
    write_json(args.output / "audit.json", checked)
    if not checked["ok"]:
        raise ValueError(checked["blockers"])
    identity = hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()
    implementation = hashlib.sha256(b"".join((ROOT / "src/ai_stock_bot/v4" / f).read_bytes()
        for f in ("research.py", "portfolio.py", "allocation.py", "actions.py", "cnn.py"))).hexdigest()
    all_cases = candidates()
    protocol = {"version": RESEARCH_VERSION, "engine": ENGINE_VERSION, "identity": identity,
        "implementation": implementation, "candidate_count": len(all_cases), "cases": all_cases,
        "models": {k: asdict(v) for k, v in model_specs().items()},
        "development": ["2020-01-01", "2022-12-31"], "later": ["2023-01-01", "2025-12-31"],
        "partial": ["2026-01-01", "2026-09-28"], "continuous": ["2020-01-01", "2026-09-28"],
        "selection": "Within each rule family or model: development stressed CAGR minus half absolute drawdown; lock before later period",
        "stress": "double commissions and slippage, borrowing 12% instead of 8%; same forecasts",
        "gate": {"later_and_continuous_cagr_min": .20, "max_drawdown": .20,
            "all_completed_years_positive": True, "partial_period_positive": True,
            "stress_profitable": True, "beats_unlevered_hold": True},
        "limitations": ["Previously inspected years, not pristine holdout", "Fixed present-day six-ETF universe",
            "Daily fills are approximations; market impact and future liquidity not guaranteed",
            "Volatility target is a forecast, not a loss cap", "No guarantee of future profit"],
        "sources": ["https://qlib.readthedocs.io/en/latest/component/strategy.html",
            "https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html",
            "https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html",
            "https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.ExtraTreesRegressor.html",
            "https://xgboost.readthedocs.io/en/stable/parameter.html"]}
    path = args.output / "protocol.json"
    if path.exists() and load(path) != protocol:
        raise ValueError("Protocol changed; use a new output folder")
    write_json(path, protocol)
    results_path = args.output / "results.json"
    results = load(results_path) if results_path.exists() else {"development": {}, "finalists": {}, "failures": []}
    cases_dir = args.output / "cases"
    cases_dir.mkdir(exist_ok=True)

    def run(label, candidate, period_name):
        pair = {}
        for stress in (False, True):
            key = f"{period_name}-{label}-{'stress' if stress else 'base'}"
            request = {"candidate": candidate, "period": protocol[period_name], "stress": stress}
            request_path = cases_dir / f"{key}.json"
            if request_path.exists() and load(request_path) != request:
                raise ValueError(f"Changed case {key}")
            write_json(request_path, request)
            result_path = request_path.with_suffix(".result.json")
            if not result_path.exists() and not dispatch(args, ["--case", str(request_path)], key):
                results["failures"].append(key)
                write_json(results_path, results)
                return None
            pair["stress" if stress else "base"] = load(result_path)
        print(json.dumps({"period": period_name, "case": label,
            "cagr": pair["base"]["liquidation_cagr"], "dd": pair["base"]["max_drawdown"],
            "stress_cagr": pair["stress"]["liquidation_cagr"], "trades": pair["base"]["trade_count"]}), flush=True)
        return {"candidate": candidate, **pair}

    # Rule screening first; models are isolated one family at a time to bound memory.
    for label, candidate in all_cases.items():
        if label in results["development"]:
            continue
        model = candidate.get("model")
        if model and not (args.output / "models" / model / "panel.json").exists():
            runtime = ROOT / ".venv/Scripts/python.exe" if model == "cnn20" else None
            if not dispatch(args, ["--model", model], f"model-{model}", runtime):
                results["failures"].append(f"model-{model}")
                write_json(results_path, results)
                continue
        row = run(label, candidate, "development")
        if row:
            results["development"][label] = row
        write_json(results_path, results)
    buckets = {}
    for label, row in results["development"].items():
        candidate = row["candidate"]
        family = candidate.get("model", candidate["family"])
        score = row["stress"]["liquidation_cagr"] - .5 * abs(row["stress"]["max_drawdown"])
        if family not in buckets or score > buckets[family][0]:
            buckets[family] = (score, label)
    locked = {family: value[1] for family, value in buckets.items()}
    if results.get("locked") and results["locked"] != locked:
        raise ValueError("Previously locked selection changed")
    results["locked"] = locked
    write_json(results_path, results)
    print("LOCKED " + json.dumps(locked), flush=True)
    finalists = {label: all_cases[label] for label in locked.values()}
    # Leverage is compared symmetrically for the winning rule and winning ML on development data.
    for family_set in ({"legacy", "multi", "breakout", "pullback", "allocation"}, set(model_specs())):
        eligible = [v for k, v in buckets.items() if k in family_set]
        if eligible:
            _, winner = max(eligible)
            for multiple in (1, 2, 3):
                candidate = all_cases[winner]
                finalists[f"locked-{winner}-{multiple}x"] = {**candidate,
                    "plan": {**candidate["plan"], "research_multiple": multiple}}
    base = base_plan().model_dump()
    finalists["hold-1x"] = {"family": "hold", "plan": {**base, "research_multiple": 1}}
    finalists["existing-ml-2x"] = {"family": "old_ml", "plan": base}
    finalists["frozen-forward-2x"] = {"family": "multi", "plan": base}
    for label, candidate in finalists.items():
        results["finalists"].setdefault(label, {})
        for period in ("later", "partial", "continuous"):
            if period not in results["finalists"][label]:
                row = run(label, candidate, period)
                if row:
                    results["finalists"][label][period] = row
                write_json(results_path, results)
    reference = results["finalists"].get("hold-1x", {})
    gates = {}
    for label, periods in results["finalists"].items():
        complete = all(p in periods and p in reference for p in ("later", "partial", "continuous"))
        if not complete:
            gates[label] = {"complete": False, "passed": False}
            continue
        checks = {"complete": True,
            "return_target": all(periods[p]["base"]["liquidation_cagr"] >= .20 for p in ("later", "continuous")),
            "drawdown": all(periods[p]["base"]["max_drawdown"] >= -.20 for p in periods),
            "stress_profitable": all(periods[p]["stress"]["liquidation_return"] > 0 for p in periods),
            "positive_completed_years": all(r["return_pct"] > 0 for r in periods["continuous"]["base"]["per_year"] if r["year"] < 2026),
            "positive_partial": periods["partial"]["base"]["liquidation_return"] > 0,
            "beats_hold": all(periods[p]["base"]["liquidation_return"] > reference[p]["base"]["liquidation_return"] for p in ("later", "continuous")),
            "solvent": all(not periods[p]["base"]["insolvent"] and not periods[p]["stress"]["insolvent"] for p in periods)}
        gates[label] = {**checks, "passed": all(checks.values())}
    results["gates"] = gates
    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    results["goal_proven"] = False
    results["historical_gate_passed"] = [k for k, g in gates.items() if g["passed"]]
    write_json(results_path, results)
    print("COMPLETE " + json.dumps(results["historical_gate_passed"]), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--case", type=Path)
    args = parser.parse_args()
    args.output = args.output.resolve()
    worker(args) if args.worker else main(args)
