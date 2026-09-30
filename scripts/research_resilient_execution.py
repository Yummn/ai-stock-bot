"""Frozen, bounded risk-management experiment using the production state machine."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/reports/combinations-20260930/models/hgb20_regularized/panel.json"
SNAPSHOT = ROOT / "docs/reports/daily-4-7-frozen/market.json.gz"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


class FrozenForecasts:
    classification = False
    horizon = 20

    def __init__(self, panel):
        self.horizon = panel.get("spec", {}).get("horizon", 20)
        self.predictions = {(r["date"], r["symbol"]): r["value"] for r in panel["rows"]}
        self.by_month = {r["cutoff"][:7]: r for r in panel["reports"]}
        self.reports = []
        self.seen = set()

    def at(self, date):
        day = str(date.date())
        report = self.by_month[day[:7]]
        if report.get("label_end"):
            assert report["label_end"] < report["cutoff"] <= day
        if day[:7] not in self.seen:
            self.seen.add(day[:7])
            self.reports.append(report)
        return {s: value for (d, s), value in self.predictions.items() if d == day}, report


class EnsembleForecasts:
    classification = False
    horizon = 20

    def __init__(self, names):
        self.models = {name: FrozenForecasts(load(SOURCE.parent.parent/name/"panel.json")) for name in names}
        self.reports, self.seen = [], set()

    def at(self, date):
        import numpy as np
        eligible, receipts = [], {}
        for name, model in self.models.items():
            values, report = model.at(date)
            receipts[name] = {k: v for k, v in report.items() if k != "predictions"}
            if report["status"] == "model":
                eligible.append(values)
        predictions = {}
        if len(eligible) >= 2:
            for symbol in set.intersection(*(set(values) for values in eligible)):
                predictions[symbol] = float(np.median([values[symbol] for values in eligible]))
        day = str(date.date())
        report = {"cutoff": day[:7]+"-01", "status": "model" if predictions else "baseline",
            "label_end": max((r["label_end"] for r in receipts.values() if r.get("label_end")), default=None),
            "reason": "median of at least two time-validated models" if predictions else "fewer than two accepted models",
            "components": receipts}
        if day[:7] not in self.seen:
            self.seen.add(day[:7])
            self.reports.append(report)
        return predictions, report


def worker(case_path, output):
    import numpy as np
    import pandas as pd
    from research_combinations import get_histories
    from ai_stock_bot.v2.store import Store
    from ai_stock_bot.v4.portfolio import Portfolio, statistics
    from ai_stock_bot.v4.schema import Plan
    protocol = load(output / "protocol.json")
    assert hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest() == protocol["market_sha256"]
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == protocol["forecast_sha256"]
    for name, expected in protocol.get("ensemble_sha256", {}).items():
        assert hashlib.sha256((SOURCE.parent.parent/name/"panel.json").read_bytes()).hexdigest() == expected
    case = load(case_path)
    plan = Plan.model_validate(case["plan"])
    if case["stress"]:
        plan = plan.model_copy(update={"commission_rate": plan.commission_rate*2,
            "min_commission": plan.min_commission*2, "slippage_bps": plan.slippage_bps*2,
            "research_annual_rate": .12})
    histories = get_histories()
    engine = Portfolio(Store(output/"isolated-store"), plan, histories, method="rotation")
    engine.method = case.get("method", "ml")
    if engine.method == "rotation":
        engine.forecasts = None
    elif case.get("forecast_model"):
        file = output/"models"/case["forecast_model"]/"panel.json"
        expected = load(output/"forecast_manifest.json")[case["forecast_model"]]
        assert hashlib.sha256(file.read_bytes()).hexdigest() == expected
        engine.forecasts = FrozenForecasts(load(file))
    else:
        engine.forecasts = EnsembleForecasts(case["ensemble"]) if case.get("ensemble") else FrozenForecasts(load(SOURCE))
    dates = sorted(set.union(*(set(f.index) for f in histories.values())))
    start, end = map(pd.Timestamp, case["period"])
    previous = max(d for d in dates if d < start)
    dates = [d for d in dates if start <= d <= end]
    engine.state["pending"] = engine.make_plan(previous)
    price_pnl = {s: 0. for s in plan.symbols}
    risk_trades = 0
    for date in dates:
        before = {s: p["quantity"]*p["price"] for s, p in engine.account.positions.items()}
        count = len(engine.account.trades)
        pending = engine.state["pending"]
        engine.step(date)
        trades = engine.account.trades[count:]
        if pending and pending.get("reduce_only"):
            assert all(t["side"] == "sell" for t in trades)
            risk_trades += len(trades)
        for s in plan.symbols:
            position = engine.account.positions.get(s, {})
            change = position.get("quantity", 0)*position.get("price", 0.) - before.get(s, 0.)
            purchases = sum(t["quantity"]*t["price"]*(1 if t["side"] == "buy" else -1)
                            for t in trades if t["symbol"] == s)
            price_pnl[s] += change-purchases
    result = statistics(engine.state, plan.starting_cash)
    receivable = sum(d["amount"] for d in engine.state["receivables"])
    reconstructed = (plan.starting_cash + sum(price_pnl.values()) + engine.state["dividends_paid"]
                     + receivable - result["fees"] - result["interest_paid"])
    np.testing.assert_allclose(reconstructed, result["end_equity"], atol=1e-6, rtol=1e-10)
    exit_cost = sum(max(plan.min_commission, p["quantity"]*p["price"]*plan.commission_rate)
        + p["quantity"]*p["price"]*plan.slippage_bps/10000 for p in engine.account.positions.values())
    final = result["end_equity"]-exit_cost
    years = ((dates[-1]-dates[0]).days+1)/365.25
    decisions = engine.state["decisions"]
    curve = engine.state["curve"]
    rolling = pd.Series([r["equity"] for r in curve]).pct_change(252).dropna()
    write(case_path.with_suffix(".result.json"), {**result,
        "liquidation_cagr": (final/plan.starting_cash)**(1/years)-1 if final > 0 else -1.,
        "liquidation_return": final/plan.starting_cash-1, "exit_cost": exit_cost,
        "start": str(dates[0].date()), "end": str(dates[-1].date()),
        "rolling_year_min": float(rolling.min()) if len(rolling) else None,
        "price_pnl": price_pnl, "pnl_reconciled": True, "receivables": receivable,
        "model_decisions": sum(r["status"] == "model" for r in decisions),
        "risk_review_decisions": sum(r["status"] == "risk_review" for r in decisions),
        "risk_review_trades": risk_trades, "rejections": engine.state["rejections"],
        "curve": curve, "trades": engine.account.trades, "decisions": decisions,
        "training": engine.forecasts.reports if engine.forecasts else []})


def main(output, second_round=False, normalized=False):
    source_plan = load(ROOT/"docs/reports/execution-release-20260930/verified-plan.json")["plan"]
    cases = {f"{signal}-{review}": {**source_plan, "signal_policy": signal, "risk_review": review}
             for signal in ("legacy", "trend_weighted", "confirmed")
             for review in ("rebalance", "weekly", "daily")}
    baseline = "legacy-rebalance"
    ensembles = {}
    families = ("legacy", "trend_weighted", "confirmed")
    if second_round:
        models = {"single": None, "trees": ["hgb20_regularized", "extra20", "xgb20"],
                  "diverse": ["hgb20_regularized", "extra20", "xgb20", "ridge20", "cnn20"]}
        families = tuple(models)
        baseline = "single-legacy"
        cases = {f"{model}-{signal}": {**source_plan, "signal_policy": signal, "risk_review": "rebalance"}
                 for model in models for signal in ("legacy", "confirmed", "consensus", "fast_confirmed")}
        ensembles = {key: models[key.split('-')[0]] for key in cases}
    model_specs = {}
    if normalized:
        from dataclasses import asdict, replace
        from ai_stock_bot.v4.research import RiskNormalizedSpec, model_specs as regular_specs
        recipe = regular_specs()["hgb20_regularized"]
        model_specs = {f"risk{years}y": RiskNormalizedSpec(**{**asdict(recipe), "years": years})
                       for years in (1, 3, 5)}
        model_specs["regular5y"] = replace(recipe, years=5)
        baseline = "single-legacy"
        cases = {baseline: source_plan, **{f"model-{name}": source_plan for name in model_specs}}
        families = ("model",)
    protocol = {"version": "resilient-execution-1", "cases": cases,
        "market_sha256": hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest(),
        "forecast_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "implementation_sha256": hashlib.sha256(b"".join((ROOT/"src/ai_stock_bot/v4"/f).read_bytes()
            for f in ("portfolio.py", "cost_allocation.py", "allocation.py", "schema.py")) + Path(__file__).read_bytes()).hexdigest(),
        "periods": {"development": ["2020-01-01", "2022-12-31"],
            "later": ["2023-01-01", "2025-12-31"], "partial": ["2026-01-01", "2026-09-28"],
            "continuous": ["2020-01-01", "2026-09-28"]},
        "selection": "Within each signal family select highest development stressed CAGR minus half absolute drawdown; lock before later data.",
        "promotion_gate": {"later_and_continuous_net_cagr": .20, "max_drawdown": .20,
            "all_completed_years_positive": True, "partial_positive": True,
            "all_periods_stress_profitable": True, "no_increase_in_leverage_or_risk_budget": True},
        "limitations": ["Previously observed history, not a pristine holdout",
            "Passing historical gate cannot establish future profitability", "Same fixed six ETFs",
            "Public daily bars do not establish real order fills or broker financing availability"],
        "references": ["https://www.cvxportfolio.com/en/stable/risks.html",
            "https://sdm.lbl.gov/oapapers/ssrn-id2507040-bailey.pdf"]}
    if second_round:
        protocol.update(version="resilient-execution-2", ensemble_members=ensembles,
            ensemble_sha256={name: hashlib.sha256((SOURCE.parent.parent/name/"panel.json").read_bytes()).hexdigest()
                             for name in ("hgb20_regularized", "extra20", "xgb20", "ridge20", "cnn20")},
            selection="Within each forecast family select highest development stressed CAGR minus half absolute drawdown; lock before later data.",
            motivation="2022 HGB rejected all 12 months; test universal fallback trend confirmation and causal median ensemble. Second-round hypotheses use first-round findings; not a new holdout.")
    if normalized:
        protocol.update(version="resilient-normalization-3", models={k: asdict(v) for k, v in model_specs.items()},
            selection="One model chosen by development stressed CAGR minus half absolute drawdown; then frozen for later periods. Original baseline also replayed.",
            model_implementation_sha256=hashlib.sha256((ROOT/"src/ai_stock_bot/v4/research.py").read_bytes()).hexdigest(),
            motivation="Scale labels by feature-date volatility, not future volatility; require better validation MSE than zero and train mean. Previously observed history remains contaminated.")
    output.mkdir(parents=True, exist_ok=True)
    protocol_path = output/"protocol.json"
    if protocol_path.exists() and load(protocol_path) != protocol:
        raise ValueError("Protocol changed; use a new output directory")
    write(protocol_path, protocol)
    if normalized:
        from research_combinations import get_histories
        from ai_stock_bot.v4.research import build_panel
        histories = get_histories()
        for name, spec in model_specs.items():
            build_panel(histories, spec, output/"models"/name, protocol["market_sha256"])
        write(output/"forecast_manifest.json", {name: hashlib.sha256((output/"models"/name/"panel.json").read_bytes()).hexdigest()
                                               for name in model_specs})
    results_path = output/"results.json"
    results = load(results_path) if results_path.exists() else {"development": {}, "finalists": {}}

    def run(key, period):
        pair = {}
        for stress in (False, True):
            name = f"{period}-{key}-{'stress' if stress else 'base'}"
            path = output/"cases"/f"{name}.json"
            request = {"plan": cases[key], "period": protocol["periods"][period], "stress": stress}
            if ensembles.get(key):
                request["ensemble"] = ensembles[key]
            if normalized and key.startswith("model-"):
                request["forecast_model"] = key.removeprefix("model-")
            if path.exists() and load(path) != request:
                raise ValueError("Case changed")
            write(path, request)
            result_path = path.with_suffix(".result.json")
            if not result_path.exists():
                logs = output/"logs"
                logs.mkdir(exist_ok=True)
                for attempt in (1, 2):
                    started = time.time()
                    with (logs/f"{name}-{attempt}.log").open("w", encoding="utf-8") as stream:
                        process = subprocess.run([sys.executable, "-X", "faulthandler", str(Path(__file__).resolve()),
                            "--output", str(output), "--worker", str(path)], cwd=ROOT,
                            env={**os.environ, "PYTHONPATH": str(ROOT/"src"), "PYTHONUTF8": "1",
                                 "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"},
                            stdout=stream, stderr=subprocess.STDOUT)
                    write(logs/f"{name}-{attempt}.json", {"exit_code": process.returncode, "seconds": time.time()-started})
                    if process.returncode == 0:
                        break
                    print(f"FAIL {name}: {process.returncode}; same-input retry {attempt}", flush=True)
                if process.returncode:
                    raise RuntimeError(f"Replay failed: {name}")
            value = load(result_path)
            pair["stress" if stress else "base"] = {k: v for k, v in value.items()
                if k not in {"curve", "trades", "decisions", "training", "rejections"}}
        print(f"{period} {key}: net={pair['base']['liquidation_cagr']:.4%} "
              f"DD={pair['base']['max_drawdown']:.4%} stress={pair['stress']['liquidation_cagr']:.4%}", flush=True)
        return pair

    for key in cases:
        if key not in results["development"]:
            results["development"][key] = run(key, "development")
            write(results_path, results)
    def score(key):
        r = results["development"][key]["stress"]
        return r["liquidation_cagr"]-.5*abs(r["max_drawdown"])
    locked = {family: max((k for k in cases if k.startswith(family+"-")), key=score)
              for family in families}
    if results.get("locked") and results["locked"] != locked:
        raise ValueError("Locked selection changed")
    results["locked"] = locked
    write(results_path, results)
    for key in dict.fromkeys([baseline, *locked.values()]):
        results["finalists"].setdefault(key, {})
        for period in ("later", "partial", "continuous"):
            if period not in results["finalists"][key]:
                results["finalists"][key][period] = run(key, period)
                write(results_path, results)
    results["gates"] = {}
    for key, periods in results["finalists"].items():
        checks = {
            "return_target": all(periods[p]["base"]["liquidation_cagr"] >= .20 for p in ("later", "continuous")),
            "drawdown_limit": all(periods[p][s]["max_drawdown"] >= -.20 for p in periods for s in ("base", "stress")),
            "completed_years_positive": all(r["return_pct"] > 0 for r in periods["continuous"]["base"]["per_year"] if r["year"] < 2026),
            "recent_positive": periods["partial"]["base"]["liquidation_return"] > 0,
            "stress_profitable": all(periods[p]["stress"]["liquidation_return"] > 0 for p in periods),
        }
        results["gates"][key] = {"checks": checks, "historical_passed": all(checks.values()), "forward_proven": False}
    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    results["applied"] = False
    write(results_path, results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--second-round", action="store_true")
    parser.add_argument("--normalized", action="store_true")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker.resolve(), args.output.resolve())
    else:
        main(args.output.resolve(), args.second_round, args.normalized)
