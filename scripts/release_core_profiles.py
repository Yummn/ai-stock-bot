"""Independent audit, production replay and non-destructive preset publication."""
import argparse
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from audit_resilient_execution import block_difference
from research_balanced_preset import sha
from research_combinations import get_histories
from research_core_profiles import checks
from research_resilient_execution import ROOT, SOURCE, SNAPSHOT, load, write
from ai_stock_bot.v2.evaluation import write_gzip
from ai_stock_bot.v2.schema import JobRequest
from ai_stock_bot.v2.store import Store, dumps
from ai_stock_bot.v4.data import audit as audit_market
from ai_stock_bot.v4.portfolio import simulate
from ai_stock_bot.v4.preset_evidence import KEY
from ai_stock_bot.v4.schema import ENGINE_VERSION, FIXED_PROFILE_PRESETS, Plan, fingerprint
from ai_stock_bot.v4.service import comparison


def audit(directory):
    spec, results = load(directory/"protocol.json"), load(directory/"results.json")
    assert results.get("completed_at") and results["separation"]
    assert sha(SNAPSHOT) == spec["market_sha256"] and sha(SOURCE) == spec["forecast_sha256"]
    for path, digest in spec["source_sha256"].items():
        assert sha(directory/"source"/path) == digest
    files = list((directory/"cases").glob("*.result.json"))
    assert len(files) == len(spec["cases"])*2+len(results["finalists"])*12
    decisions = 0
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
        assert row["interest_paid"] == 0 and not row["insolvent"] and request["plan"]["research_multiple"] == 1
        assert all(t["signal_date"] < t["date"] for t in row["trades"])
        assert all(not r.get("label_end") or r["label_end"] < r["cutoff"] for r in row["training"])
        previous = values[0]
        for y in row["per_year"]:
            last = [r["equity"] for r in row["curve"] if int(r["date"][:4]) == y["year"]][-1]
            np.testing.assert_allclose(y["return_pct"], last/previous-1, atol=1e-10)
            previous = last
        for d in row["decisions"]:
            budget = d.get("risk_budget", {})
            if budget.get("policy") not in {"defensive_v1", "balanced_v1"}:
                continue
            assert budget["status"] != "failed", file.name
            if budget["status"] == "optimized":
                assert budget["target_gross"] <= request["plan"]["max_exposure"]+1e-6
                assert budget["estimated_volatility"] <= budget["target_volatility"]+1e-5
                assert budget["risky_exposure"] <= budget["risky_limit"]+1e-6
                decisions += 1
    uncertainty = {}
    for profile, key in results["locked"].items():
        verdict = checks(results["finalists"][key], results["independent_years"][key], spec["gates"][profile])
        assert verdict == results["gates"][profile]["checks"] and all(verdict.values())
        baseline = load(directory/"cases"/f"full-old-{profile}-base.result.json")
        candidate = load(directory/"cases"/f"full-{key}-base.result.json")
        uncertainty[profile] = [block_difference(candidate, baseline, n) for n in (20, 60)]
    prior = load(ROOT/"docs/reports/balanced-20260930/cases/full-four-legacy-v15-base.result.json")
    reproduced = load(directory/"cases/full-old-balanced-base.result.json")
    np.testing.assert_allclose([r["equity"] for r in prior["curve"]], [r["equity"] for r in reproduced["curve"]], atol=1e-7)
    value = {"passed": True, "replays": len(files), "core_decisions_checked": decisions,
        "baseline_reproduced": True, "gates": results["gates"], "uncertainty": uncertainty,
        "note": "Intervals are exploratory, not adjusted for model selection; no forward-profit proof."}
    write(directory/"audit.json", value)
    print(f"AUDIT {len(files)} replays; {decisions} valid core decisions", flush=True)
    return spec, results


def prepare(directory):
    spec, results = audit(directory)
    histories = get_histories()
    store = Store(directory/"production-store")
    cache = ROOT/"docs/reports/production-recipe-20260930/isolated-store/portfolio_models"
    if not (store.root/"portfolio_models").exists():
        shutil.copytree(cache, store.root/"portfolio_models")
    verified, parity = {}, {}
    for profile, key in results["locked"].items():
        plan = Plan.model_validate(spec["cases"][key])
        parity[profile] = {}
        for period in ("full", "later", "recent"):
            value = simulate(store, plan, histories, *spec["periods"][period])
            ref = load(directory/"cases"/f"{period}-{key}-base.result.json")
            assert [r["date"] for r in value["curve"]] == [r["date"] for r in ref["curve"]]
            np.testing.assert_allclose([r["equity"] for r in value["curve"]], [r["equity"] for r in ref["curve"]], rtol=1e-11, atol=1e-7)
            for field in ("trade_count", "fees", "interest_paid", "max_drawdown"):
                np.testing.assert_allclose(value[field], ref[field], rtol=1e-11, atol=1e-7)
            write(directory/f"production-{profile}-{period}.json", value)
            parity[profile][period] = len(value["curve"])
            print(f"PARITY {profile} {period}: {len(value['curve'])} points", flush=True)
        request = JobRequest(kind="portfolio_test", investment=plan, years=list(range(2020, 2026)), compare_models=False)
        index = directory/f"{profile}-job.json"
        if index.exists():
            job_id = load(index)["id"]
            assert load(index)["plan_key"] == fingerprint(plan)
        else:
            job_id = store.create_job(request.model_dump())["id"]
            write(index, {"id": job_id, "plan_key": fingerprint(plan)})
        snapshot = store.root/"portfolio_tests"/job_id/"market.json.gz"
        checked = audit_market(histories, plan.symbols, "2020-01-01", "2025-12-31")
        assert checked["ok"], checked["blockers"]
        if not snapshot.exists():
            write_gzip(snapshot, {"engine": ENGINE_VERSION, "plan_key": fingerprint(plan), "audit": checked,
                "frames": {s: f.to_json(orient="split", date_format="iso", double_precision=15) for s, f in histories.items()}})
        output = comparison(store, job_id, request, lambda p, m: None)
        output["provenance"] = {"study": directory.name, "previously_seen_history": True, "not_forward_profit": True,
                                "market_sha256": spec["market_sha256"]}
        store.update_job(job_id, status="done", progress=100, message="核心组合冻结行情验证完成", result=output)
        verified[profile] = {"plan": plan.model_dump(), "plan_key": fingerprint(plan), "job_id": job_id, "complete": output["complete"]}
    aggressive = load(ROOT/"docs/reports/execution-release-20260930/verified-plan.json")
    plan = Plan.model_validate(aggressive["plan"])
    assert fingerprint(plan) == aggressive["plan_key"]
    unchanged = simulate(store, plan, histories, *spec["periods"]["full"])
    reference = load(ROOT/"docs/reports/production-recipe-20260930/continuous.json")
    np.testing.assert_allclose([r["equity"] for r in unchanged["curve"]], [r["equity"] for r in reference["curve"]], atol=1e-7)
    write(directory/"production-parity.json", {"passed": True, "profiles": parity, "aggressive_unchanged": True})
    write(directory/"verified-plans.json", verified)
    print("Both production plans and aggressive regression verified; no user accounts changed", flush=True)


def publish(directory, data_dir):
    spec, results = load(directory/"protocol.json"), load(directory/"results.json")
    audited, parity, verified = load(directory/"audit.json"), load(directory/"production-parity.json"), load(directory/"verified-plans.json")
    assert audited["passed"] and parity["passed"] and parity["aggressive_unchanged"] and results["separation"]
    source, target = Store(directory/"production-store"), Store(data_dir)
    records, rows = {}, []
    def metrics(key):
        r = results["finalists"][key]
        return {"cagr": r["full"]["base"]["liquidation_cagr"], "drawdown": r["full"]["base"]["max_drawdown"],
            "later_cagr": r["later"]["base"]["liquidation_cagr"], "recent_return": r["recent"]["base"]["liquidation_return"],
            "stress_cagr": r["full"]["stress"]["liquidation_cagr"], "volatility": r["full"]["base"]["volatility"],
            "mean_exposure": r["full"]["base"]["mean_exposure"],
            "worst_year": min(y["return_pct"] for y in r["full"]["base"]["per_year"] if y["year"] < 2026),
            "independent_years": {y: v["base"]["liquidation_return"] for y, v in results["independent_years"][key].items()}}
    for profile, key in results["locked"].items():
        plan = Plan(**{k: v for k, v in FIXED_PROFILE_PRESETS[profile].items() if k != "label"}, profile=profile, preset_version=1)
        assert fingerprint(plan) == verified[profile]["plan_key"] and verified[profile]["complete"]
        assert audited["gates"][profile]["historical_passed"]
        records[profile] = {"study": directory.name, "candidate": key, "plan_key": fingerprint(plan),
            "starting_cash": plan.starting_cash, "start": results["finalists"][key]["full"]["base"]["start"],
            "end": results["finalists"][key]["full"]["base"]["end"], "historical_passed": True, "forward_proven": False,
            "candidate_count": len(spec["cases"]), "replays": audited["replays"], "before": metrics(f"old-{profile}"),
            "after": metrics(key), "gates": spec["gates"][profile], "checks": results["gates"][profile]["checks"],
            "uncertainty": audited["uncertainty"][profile], "completed_at": results["completed_at"], "market_sha256": spec["market_sha256"]}
        job_id = verified[profile]["job_id"]
        with source.connect() as c:
            row = dict(c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        assert row["status"] == "done"
        rows.append(row)
        destination = target.root/"portfolio_tests"/job_id
        if not destination.exists():
            shutil.copytree(source.root/"portfolio_tests"/job_id, destination)
    with target.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        protected = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM meta WHERE key<>?", (KEY,))}
        for row in rows:
            existing = c.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
            if existing:
                assert dict(existing) == row
            else:
                c.execute(f"INSERT INTO jobs({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
        old = c.execute("SELECT value FROM meta WHERE key=?", (KEY,)).fetchone()
        write(directory/"previous-preset-evidence.json", json.loads(old[0]) if old else {})
        receipts = json.loads(old[0]) if old else {}
        receipts.update(records)
        c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (KEY, dumps(receipts)))
        assert protected == {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM meta WHERE key<>?", (KEY,))}
    write(directory/"publication.json", {"profiles": records, "saved_settings_unchanged": True,
        "accounts_unchanged": True, "live_orders": False})
    print("Published new defaults' evidence; active settings, observations and accounts unchanged", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--publish", type=Path)
    args = parser.parse_args()
    if args.publish:
        publish(args.directory.resolve(), args.publish.resolve())
    else:
        prepare(args.directory.resolve())
