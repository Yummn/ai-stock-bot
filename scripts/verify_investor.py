"""Reproducible multi-asset validation, persisted through the normal job path."""
import os
import urllib.request
import urllib.error
from pathlib import Path

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(name, "2")

from launch_workbench import migrate
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v2.schema import AppConfig, JobRequest
from ai_stock_bot.v2.jobs import execute_job
from ai_stock_bot.v4.schema import Plan


if __name__ == "__main__":
    try:
        with urllib.request.urlopen('http://127.0.0.1:8771/api/v2/health',timeout=2):
            raise SystemExit('Server is running. Start validation from its UI to use the managed queue.')
    except urllib.error.URLError:
        pass
    root = Path(__file__).resolve().parents[1]
    target = root / "data" / "v4"
    migrate(root / "data" / "v3-hybrid-preview", target)
    store = Store(target)
    request = JobRequest(kind="portfolio_test", investment=Plan(), years=[2021, 2022, 2023, 2024, 2025], config=AppConfig())
    job = store.create_job(request.model_dump())
    print(job["id"], flush=True)
    execute_job(target, job["id"])
    result = store.job(job["id"], True)
    print(result["status"], result.get("error"), flush=True)
    if result["status"] == "done":
        import json
        print(json.dumps({k: {m: v[m] for m in ("return_pct", "annualized_return", "max_drawdown", "trade_count", "per_year")} for k, v in result["result"]["results"].items()}, ensure_ascii=True), flush=True)
    else:
        raise SystemExit(1)
