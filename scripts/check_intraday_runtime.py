"""Exercise actual spawned workers against an isolated, read-only market copy."""
import argparse
import json
import platform
import tempfile
import time
from pathlib import Path

from ai_stock_bot.v2.jobs import JobManager
from ai_stock_bot.v2.schema import JobRequest
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.data import intraday_table
from ai_stock_bot.v4.schema import Plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = Store(root / "data" / "v4-intraday-preview")
    with source.connect() as connection:
        rows = connection.execute("SELECT * FROM intraday_bars WHERE symbol='518880.SH'").fetchall()
    results = []
    with tempfile.TemporaryDirectory(prefix="stocklab-worker-check-") as directory:
        store = Store(Path(directory))
        intraday_table(store)
        with store.connect() as connection:
            connection.executemany("INSERT INTO intraday_bars VALUES(?,?,?,?,?,?,?,?,?)", [tuple(r) for r in rows])
        manager = JobManager(store)
        try:
            for _ in range(args.runs):
                request = JobRequest(kind="intraday_test", investment=Plan(
                    research_interval="30m", intraday_strategy="ml_edge", research_multiple=3))
                task = store.create_job(request.model_dump())
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    task = store.job(task["id"], True)
                    if task["status"] not in {"queued", "running"}:
                        break
                    time.sleep(.2)
                if task["status"] in {"queued", "running"}:
                    task = manager.stop(task["id"])
                result = {"id": task["id"], "status": task["status"], "error": task.get("error")}
                if task["status"] == "done":
                    result["snapshot"] = task["result"]["coverage"]["snapshot"]
                    result["returns"] = [s["base"]["return_pct"] for s in task["result"]["scenarios"]]
                else:
                    log = store.root / "logs" / f"{task['id']}.native.log"
                    result["native_log"] = log.read_text(encoding="utf-8") if log.exists() else ""
                results.append(result)
                print(json.dumps({k: v for k, v in result.items() if k != "native_log"}), flush=True)
        finally:
            manager.close()
    payload = {"python": platform.python_version(), "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all(r["status"] == "done" for r in results):
        raise SystemExit(1)
    if len({tuple(r["returns"]) for r in results}) != 1:
        raise SystemExit("Repeated workers produced different metrics")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
