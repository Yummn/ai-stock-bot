"""Publish verified preset evidence without changing any active plan or account."""
import argparse
import shutil
from pathlib import Path

from research_resilient_execution import load, write
from ai_stock_bot.v2.store import Store, dumps
from ai_stock_bot.v4.preset_evidence import KEY
from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS, Plan, fingerprint


def publish(directory, data_dir):
    spec, results = load(directory/"protocol.json"), load(directory/"results.json")
    audit = load(directory/"audit.json")
    verified, parity = load(directory/"verified-plan.json"), load(directory/"production-parity.json")
    winner = results["locked"]
    preset = {k: v for k, v in FIXED_PROFILE_PRESETS["balanced"].items() if k != "label"}
    plan = Plan(**preset, profile="balanced", preset_version=1)
    assert audit["passed"] and parity["passed"] and verified["complete"]
    assert audit["gates"][winner]["historical_passed"]
    assert fingerprint(plan) == verified["plan_key"] == parity["plan_key"]
    assert fingerprint(Plan.model_validate(spec["cases"][winner])) == fingerprint(plan)
    def metrics(key):
        rows = results["finalists"][key]
        return {"cagr": rows["full"]["base"]["liquidation_cagr"],
            "drawdown": rows["full"]["base"]["max_drawdown"],
            "later_cagr": rows["later"]["base"]["liquidation_cagr"],
            "recent_return": rows["recent"]["base"]["liquidation_return"],
            "stress_cagr": rows["full"]["stress"]["liquidation_cagr"],
            "worst_year": min(r["return_pct"] for r in rows["full"]["base"]["per_year"] if r["year"] < 2026),
            "independent_years": {y: pair["base"]["liquidation_return"] for y, pair in results["independent_years"][key].items()}}
    record = {"study": directory.name, "candidate": winner, "plan_key": fingerprint(plan),
        "starting_cash": plan.starting_cash, "start": results["finalists"][winner]["full"]["base"]["start"],
        "end": results["finalists"][winner]["full"]["base"]["end"],
        "historical_passed": True, "forward_proven": False,
        "candidate_count": len(spec["cases"]), "replays": audit["replays"],
        "before": metrics("original"), "after": metrics(winner), "gates": spec["gates"],
        "checks": results["gates"][winner]["checks"], "uncertainty": audit["uncertainty"],
        "market_sha256": spec["market_sha256"], "completed_at": results["completed_at"]}
    source, target = Store(directory/"production-store"), Store(data_dir)
    job_id = verified["job_id"]
    with source.connect() as origin:
        row = dict(origin.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    assert row["status"] == "done"
    destination = target.root/"portfolio_tests"/job_id
    if not destination.exists():
        shutil.copytree(source.root/"portfolio_tests"/job_id, destination)
    with target.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        protected = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM meta WHERE key<>?", (KEY,))}
        existing = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if existing:
            assert dict(existing) == row
        else:
            columns = tuple(row)
            c.execute(f"INSERT INTO jobs({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(row.values()))
        previous = c.execute("SELECT value FROM meta WHERE key=?", (KEY,)).fetchone()
        import json
        receipts = json.loads(previous[0]) if previous else {}
        receipts["balanced"] = record
        c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (KEY, dumps(receipts)))
        assert {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM meta WHERE key<>?", (KEY,))} == protected
    write(directory/"publication.json", {"record": record, "job_id": job_id,
        "saved_settings_unchanged": True, "accounts_unchanged": True, "live_orders": False})
    print("Published balanced preset evidence and matching validation; active plan and all accounts unchanged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    publish(args.directory.resolve(), args.data_dir.resolve())
