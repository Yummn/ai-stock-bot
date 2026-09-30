"""Frozen, bounded, chronological research. Does not change any saved account or plan."""
import argparse
import gzip
import hashlib
import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd

from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.data import audit
from ai_stock_bot.v4.portfolio import simulate
from ai_stock_bot.v4.schema import ASSETS, ENGINE_VERSION, Plan, with_profile


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def candidates():
    base = with_profile(Plan(research_interval="daily", method="rotation", auto_update=False), "aggressive")
    result = {}
    for signal in ("legacy", "multi_horizon"):
        for target in (0., .12, .18):
            for multiple in (1, 2, 3):
                label = f"{signal}-{'fixed' if not target else int(target*100)}-{multiple}x-weekly"
                result[label] = base.model_copy(update={"trend_signal": signal, "research_multiple": multiple,
                    "exposure_policy": "volatility_target" if target else "fixed",
                    "target_volatility": target or .12})
    for multiple in (1, 2, 3):
        result[f"multi_horizon-12-{multiple}x-daily"] = base.model_copy(update={
            "trend_signal": "multi_horizon", "research_multiple": multiple,
            "exposure_policy": "volatility_target", "target_volatility": .12, "rebalance": "daily"})
    return result


def compact(result, plan):
    exit_cost = sum(max(plan.min_commission, p["quantity"] * p["price"] * plan.commission_rate)
                    + p["quantity"] * p["price"] * plan.slippage_bps / 10000
                    for p in result["positions"].values())
    years = ((pd.Timestamp(result["end"]) - pd.Timestamp(result["start"])).days + 1) / 365.25
    liquidated = result["end_equity"] - exit_cost
    return {key: value for key, value in result.items() if key not in {"decisions", "rejections", "trades", "training"}} | {
        "estimated_exit_cost": exit_cost, "liquidation_return": liquidated / plan.starting_cash - 1,
        "liquidation_cagr": (liquidated / plan.starting_cash) ** (1 / years) - 1 if liquidated > 0 else -1.,
        "rejection_count": len(result["rejections"]), "model_fits": len(result["training"]),
        "positive_years": sum(row["return_pct"] > 0 for row in result["per_year"])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    snapshot = output / "market.json.gz"
    if not snapshot.exists():
        connection = sqlite3.connect(f"file:{(root / 'data/v4-intraday-preview/stocklab.sqlite3').as_posix()}?mode=ro", uri=True)
        histories = {}
        for symbol in ASSETS:
            frame = pd.read_sql_query("SELECT * FROM investment_bars WHERE symbol=? ORDER BY date", connection, params=[symbol])
            frame.index = pd.to_datetime(frame.pop("date"))
            frame = frame.drop(columns="symbol")
            row = connection.execute("SELECT value FROM meta WHERE key=?", (f"portfolio_dividends:{symbol}",)).fetchone()
            if not row:
                raise ValueError(f"No verified dividend cache: {symbol}")
            frame["dividend"], frame["pay_date"] = 0., ""
            for event in json.loads(row[0])["events"]:
                day = pd.Timestamp(event["date"])
                if day in frame.index:
                    frame.loc[day, ["dividend", "pay_date"]] = [event["cash"], event["pay_date"]]
            histories[symbol] = frame
        connection.close()
        with gzip.open(snapshot, "wt", encoding="utf-8") as stream:
            json.dump({s: f.to_json(orient="split", date_format="iso", double_precision=15) for s, f in histories.items()}, stream)
    with gzip.open(snapshot, "rt", encoding="utf-8") as stream:
        histories = {s: pd.read_json(StringIO(f), orient="split") for s, f in json.load(stream).items()}
    checked = audit(histories, list(ASSETS), "2020-01-01", "2025-12-31")
    save(output / "audit.json", checked)
    if not checked["ok"]:
        raise ValueError(checked["blockers"])
    plans = candidates()
    implementation = hashlib.sha256(b"".join((root / "src/ai_stock_bot/v4" / f).read_bytes()
        for f in ("allocation.py", "portfolio.py", "schema.py", "actions.py"))).hexdigest()
    protocol = {"engine": ENGINE_VERSION, "implementation": implementation,
        "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        "development": ["2020-01-01", "2022-12-31"], "evaluation": ["2023-01-01", "2025-12-31"],
        "candidates": {k: p.model_dump() for k, p in plans.items()},
        "selection": "development stressed CAGR - 0.5*abs(stressed max drawdown); no evaluation-based retuning",
        "limitations": ["Previously viewed history, not pristine holdout", "Fixed present-day ETF universe",
                        "Estimated exit costs, not guaranteed liquidity", "No proof of future profit"],
        "aspirational_gate": {"evaluation_cagr_min": .20, "drawdown_max": .25,
                              "all_evaluation_years_positive": True, "double_cost_positive": True}}
    protocol_path = output / "protocol.json"
    if protocol_path.exists() and json.loads(protocol_path.read_text(encoding="utf-8")) != protocol:
        raise ValueError("Protocol changed. Use a new output folder, never mix experiments.")
    save(protocol_path, protocol)
    results_path = output / "results.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else {
        "development": {}, "evaluation": {}, "post_period": {}}
    with tempfile.TemporaryDirectory(prefix="stocklab-daily-") as temporary:
        store = Store(Path(temporary))

        def run(label, plan, period, start, end):
            if label in results[period]:
                return results[period][label]
            row = {"plan": plan.model_dump()}
            for stress in (False, True):
                current = plan.model_copy(update={"commission_rate": plan.commission_rate * 2,
                    "min_commission": plan.min_commission * 2, "slippage_bps": plan.slippage_bps * 2}) if stress else plan
                result = simulate(store, current, histories, start, end, forecast_plan=plan)
                row["stress" if stress else "base"] = compact(result, current)
            results[period][label] = row
            save(results_path, results)
            print(json.dumps({"period": period, "label": label, "return": row["base"]["liquidation_return"],
                "cagr": row["base"]["liquidation_cagr"], "drawdown": row["base"]["max_drawdown"],
                "stress": row["stress"]["liquidation_return"], "trades": row["base"]["trade_count"]}), flush=True)
            return row

        for label, plan in plans.items():
            run(label, plan, "development", *protocol["development"])
        chosen = max(results["development"], key=lambda k: results["development"][k]["stress"]["liquidation_cagr"]
                     - .5 * abs(results["development"][k]["stress"]["max_drawdown"]))
        selected = plans[chosen]
        results["selected"] = chosen
        save(results_path, results)
        print("LOCKED SELECTION: " + chosen, flush=True)
        controls = {"selected": selected,
                    "old_same_multiple": plans[f"legacy-fixed-{selected.research_multiple}x-weekly"],
                    "old_1x": plans["legacy-fixed-1x-weekly"],
                    "hold_1x": selected.model_copy(update={"method": "hold", "research_multiple": 1}),
                    "hold_same_multiple": selected.model_copy(update={"method": "hold"}),
                    "ml_same_risk": selected.model_copy(update={"method": "ml"})}
        for label, plan in controls.items():
            run(label, plan, "evaluation", *protocol["evaluation"])
        last = min(f.index[-1] for f in histories.values()).date().isoformat()
        for label in ("selected", "old_same_multiple", "hold_1x"):
            run(label, controls[label], "post_period", "2026-01-01", last)
    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    r = results["evaluation"]["selected"]
    results["aspirational_gate_passed"] = (r["base"]["liquidation_cagr"] >= .20
        and r["base"]["max_drawdown"] >= -.25 and r["base"]["positive_years"] == 3
        and r["stress"]["liquidation_return"] > 0 and not r["base"]["insolvent"])
    save(results_path, results)
    print(str(output.resolve()), flush=True)


if __name__ == "__main__":
    main()
