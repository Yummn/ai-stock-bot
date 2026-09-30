"""Audit the locked candidate, verify production parity, prepare read-only evidence."""
import argparse
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from audit_resilient_execution import block_difference
from research_balanced_preset import gate, sha
from research_combinations import get_histories
from research_resilient_execution import ROOT, SOURCE, SNAPSHOT, load, write
from ai_stock_bot.v2.evaluation import write_gzip
from ai_stock_bot.v2.schema import JobRequest
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.data import audit as audit_market
from ai_stock_bot.v4.portfolio import simulate
from ai_stock_bot.v4.schema import ENGINE_VERSION, Plan, fingerprint
from ai_stock_bot.v4.service import comparison


def audit(directory):
    spec, results = load(directory/"protocol.json"), load(directory/"results.json")
    assert results.get("completed_at")
    assert sha(SNAPSHOT) == spec["market_sha256"] and sha(SOURCE) == spec["forecast_sha256"]
    for path, expected in spec["source_sha256"].items():
        assert sha(directory/"source"/path) == expected
    files = list((directory/"cases").glob("*.result.json"))
    assert len(files) == len(spec["cases"])*2 + len(results["finalists"])*12
    for file in files:
        row = load(file)
        request = load(file.with_name(file.name.replace(".result.json", ".json")))
        values = np.r_[request["plan"]["starting_cash"], [r["equity"] for r in row["curve"]]]
        assert request["period"][0] <= row["start"] <= row["end"] <= request["period"][1]
        years = ((pd.Timestamp(row["end"])-pd.Timestamp(row["start"])).days+1)/365.25
        net = (values[-1]-row["exit_cost"])/values[0]
        np.testing.assert_allclose([row["liquidation_return"], row["liquidation_cagr"], row["max_drawdown"]],
            [net-1, net**(1/years)-1, (values/np.maximum.accumulate(values)-1).min()], atol=1e-10)
        np.testing.assert_allclose(values[0]+sum(row["price_pnl"].values())+row["dividends_paid"]
            +row["receivables"]-row["fees"]-row["interest_paid"], row["end_equity"], atol=1e-6)
        assert not row["insolvent"] and row["interest_paid"] == 0 and request["plan"]["research_multiple"] == 1
        assert all(t["signal_date"] < t["date"] for t in row["trades"])
        assert all(not r.get("label_end") or r["label_end"] < r["cutoff"] for r in row["training"])
        previous = values[0]
        for year in row["per_year"]:
            points = [r["equity"] for r in row["curve"] if int(r["date"][:4]) == year["year"]]
            np.testing.assert_allclose(year["return_pct"], points[-1]/previous-1, atol=1e-10)
            previous = points[-1]
    for key, periods in results["finalists"].items():
        checks = gate(periods, results["independent_years"][key])
        assert checks == results["gates"][key]["checks"]
        assert all(checks.values()) == results["gates"][key]["historical_passed"]
    selected = results["locked"]
    assert results["gates"][selected]["historical_passed"], "Locked candidate failed; do not publish"
    base = load(directory/"cases/full-original-base.result.json")
    candidate = load(directory/"cases"/f"full-{selected}-base.result.json")
    receipt = {"passed": True, "replays": len(files), "candidate": selected,
        "gates": results["gates"], "uncertainty": [block_difference(candidate, base, n) for n in (20, 60)],
        "note": "Exploratory paired block intervals, not selection-adjusted; no forward-profit proof."}
    write(directory/"audit.json", receipt)
    print(f"AUDIT passed: {len(files)} costed, causal, reconciled replays", flush=True)
    return spec, results


def prepare(directory):
    spec, results = audit(directory)
    key = results["locked"]
    plan = Plan.model_validate(spec["cases"][key])
    histories = get_histories()
    store = Store(directory/"production-store")
    prior_cache = ROOT/"docs/reports/production-recipe-20260930/isolated-store/portfolio_models"
    if prior_cache.exists() and not (store.root/"portfolio_models").exists():
        shutil.copytree(prior_cache, store.root/"portfolio_models")
    parity = {}
    for period in ("full", "later", "recent"):
        reference = load(directory/"cases"/f"{period}-{key}-base.result.json")
        output = store.root.parent/f"production-{period}.json"
        if output.exists():
            value = load(output)
        else:
            value = simulate(store, plan, histories, *spec["periods"][period],
                             progress=lambda p, m: print(f"{period}: {m}", flush=True) if "训练" in m else None)
            write(output, value)
        assert [r["date"] for r in value["curve"]] == [r["date"] for r in reference["curve"]]
        np.testing.assert_allclose([r["equity"] for r in value["curve"]],
                                  [r["equity"] for r in reference["curve"]], rtol=1e-11, atol=1e-7)
        for field in ("trade_count", "fees", "interest_paid", "max_drawdown"):
            np.testing.assert_allclose(value[field], reference[field], rtol=1e-11, atol=1e-7)
        assert all(t["signal_date"] < t["date"] for t in value["trades"])
        assert all(not r.get("label_end") or r["label_end"] < r["cutoff"] for r in value["training"])
        parity[period] = {"points_equal": len(value["curve"]), "trades": value["trade_count"],
                          "model_months": len(value["training"])}
        print(f"PRODUCTION PARITY {period}: {parity[period]}", flush=True)
    write(directory/"production-parity.json", {"plan_key": fingerprint(plan), "passed": True, "periods": parity})
    request = JobRequest(kind="portfolio_test", investment=plan, years=list(range(2020, 2026)), compare_models=False)
    index = directory/"prepared-job.json"
    if index.exists():
        job = load(index)
        assert job["plan_key"] == fingerprint(plan)
        job_id = job["id"]
    else:
        job_id = store.create_job(request.model_dump())["id"]
        write(index, {"id": job_id, "plan_key": fingerprint(plan)})
    snapshot = store.root/"portfolio_tests"/job_id/"market.json.gz"
    if not snapshot.exists():
        checked = audit_market(histories, plan.symbols, "2020-01-01", "2025-12-31")
        assert checked["ok"], checked["blockers"]
        write_gzip(snapshot, {"engine": ENGINE_VERSION, "plan_key": fingerprint(plan), "audit": checked,
            "frames": {s: f.to_json(orient="split", date_format="iso", double_precision=15) for s, f in histories.items()}})
    output = comparison(store, job_id, request, lambda p, m: None)
    output["provenance"] = {"study": directory.name, "previously_seen_history": True,
                            "not_forward_profit": True, "market_sha256": spec["market_sha256"]}
    store.update_job(job_id, status="done", progress=100, message="均衡预设冻结行情验证完成", result=output)
    write(directory/"verified-plan.json", {"plan": plan.model_dump(), "plan_key": fingerprint(plan),
        "job_id": job_id, "complete": output["complete"]})
    print("Prepared matching complete validation in isolated store; user settings unchanged", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    prepare(args.directory.resolve())
