"""Validate an executable paper plan, then explicitly install it with rollback data."""
import argparse
import json
import shutil
import time
from pathlib import Path

from research_combinations import ROOT, get_histories, load
from ai_stock_bot.v2.evaluation import write_gzip
from ai_stock_bot.v2.schema import JobRequest
from ai_stock_bot.v2.store import Store, dumps
from ai_stock_bot.v4.data import audit
from ai_stock_bot.v4.research import write_json
from ai_stock_bot.v4.schema import ENGINE_VERSION, Plan, fingerprint
from ai_stock_bot.v4.service import comparison


def prepare(directory):
    parity = load(ROOT/"docs/reports/production-recipe-20260930/verification.json")
    plan = Plan.model_validate({**parity["plan"], "name": "成本约束梯度树 · 2x模拟",
        "validation_start_year": 2020, "validation_end_year": 2025, "auto_update": False})
    request = JobRequest(kind="portfolio_test", investment=plan, years=list(range(2020, 2026)), compare_models=False)
    store = Store(directory/"isolated-store")
    index = directory/"job.json"
    if index.exists():
        job = load(index)
        if job["plan_key"] != fingerprint(plan):
            raise ValueError("Prepared plan changed")
        job_id = job["id"]
    else:
        job_id = store.create_job(request.model_dump())["id"]
        write_json(index, {"id": job_id, "plan_key": fingerprint(plan)})
    snapshot = store.root/"portfolio_tests"/job_id/"market.json.gz"
    if not snapshot.exists():
        histories = get_histories()
        checked = audit(histories, plan.symbols, "2020-01-01", "2025-12-31")
        if not checked["ok"]:
            raise ValueError(checked["blockers"])
        write_gzip(snapshot, {"engine": ENGINE_VERSION, "plan_key": fingerprint(plan), "audit": checked,
            "frames": {s: f.to_json(orient="split", date_format="iso", double_precision=15) for s, f in histories.items()}})
    output = comparison(store, job_id, request, lambda p, m: print(f"{p:.0f}% {m}", flush=True))
    output["provenance"] = {"source": "docs/reports/daily-4-7-frozen/market.json.gz",
        "previously_seen_history": True, "not_forward_profit": True}
    store.update_job(job_id, status="done", progress=100, message="冻结行情完整验证完成", result=output)
    write_json(directory/"verified-plan.json", {"plan": plan.model_dump(), "job_id": job_id,
        "plan_key": fingerprint(plan), "result": output})
    return store, job_id, plan, output


def install(directory, data_dir):
    verified = load(directory/"verified-plan.json")
    source = Store(directory/"isolated-store")
    target = Store(data_dir)
    plan = Plan.model_validate(verified["plan"])
    if not verified["result"].get("complete") or verified["plan_key"] != fingerprint(plan):
        raise ValueError("Executable plan has no matching completed validation")
    job_id = verified["job_id"]
    with target.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        current = json.loads(c.execute("SELECT value FROM meta WHERE key='investment_settings'").fetchone()[0])
        if current["plan"] == plan.model_dump():
            print("Verified plan already installed")
            return
        state = c.execute("SELECT value FROM meta WHERE key='portfolio_state'").fetchone()
        if state and json.loads(state[0]):
            raise ValueError("Existing simulation must not be silently replaced")
        if c.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("Wait for active jobs before installing")
        backup = directory/"previous-settings.json"
        if backup.exists() and load(backup) != current:
            raise ValueError("Settings changed after backup; do not overwrite user changes")
        write_json(backup, current)
        destination = target.root/"portfolio_tests"/job_id
        if not destination.exists():
            shutil.copytree(source.root/"portfolio_tests"/job_id, destination)
        with source.connect() as origin:
            row = dict(origin.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        columns = tuple(row)
        c.execute(f"INSERT INTO jobs({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(row.values()))
        saved = {"plan": plan.model_dump(), "revision": current["revision"]+1, "saved_at": time.time()}
        c.execute("UPDATE meta SET value=? WHERE key='investment_settings'", (dumps(saved),))
    write_json(directory/"installed.json", {"revision": saved["revision"], "job_id": job_id,
        "plan_key": fingerprint(plan), "live_orders": False, "forward_study_unchanged": True})
    print(f"Installed verified paper plan revision {saved['revision']}; no orders placed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--install", type=Path)
    args = parser.parse_args()
    if args.install:
        install(args.output, args.install)
    else:
        prepare(args.output)
