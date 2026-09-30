"""Run real-data research in an isolated store, or serve its browser test fixture."""
import argparse
import json
from io import StringIO
from pathlib import Path

import pandas as pd

from ai_stock_bot.v2.evaluation import read_gzip
from ai_stock_bot.v2.schema import JobRequest
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4 import lab
from ai_stock_bot.v4.lab_schema import Study
from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS, Plan
from ai_stock_bot.v4.service import save_settings

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/reports/research-workspace-20261001"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--serve", type=int)
    parser.add_argument("--static-root", type=Path, default=OUTPUT / "ui-dist")
    parser.add_argument("--mode", choices=["compare", "tune"], default="compare")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    store = Store(OUTPUT / "ui-store")
    if args.serve:
        import uvicorn
        from ai_stock_bot.v2.api import create_app
        uvicorn.run(create_app(store.root, static_root=args.static_root, start_manager=False),
                    host="127.0.0.1", port=args.serve)
        return
    if not args.snapshot:
        parser.error("--snapshot is required for real-data verification")
    frozen = read_gzip(args.snapshot)
    histories = {s: pd.read_json(StringIO(text), orient="split") for s, text in frozen["frames"].items()}
    for frame in histories.values():
        frame.index = pd.to_datetime(frame.index)
    plan = Plan(**{k: v for k, v in FIXED_PROFILE_PRESETS["balanced"].items() if k != "label"},
                profile="balanced", preset_version=1)
    if not store.meta("investment_settings"):
        save_settings(store, plan, 1, "balanced")
    before = store.meta("investment_settings")
    study = Study(mode=args.mode, start_year=2023, end_year=2024,
                  recipe="ridge20", tuning={"regularization": 30}, budget=3,
                  recipes=["rotation", "ridge20", "hgb20_regularized"])
    request = JobRequest(kind="portfolio_research", investment=plan, study=study)
    prior = [store.job(j["id"], True) for j in store.jobs(100, kind="portfolio_research")["items"]]
    job = next((j for j in prior if j["request"]["study"] == study.model_dump() and j["status"] != "done"), None) if args.resume else None
    job = job or store.create_job(request.model_dump())
    # Read the existing frozen market without downloads or touching production data.
    lab.load_histories = lambda *a, **kw: (histories, frozen["audit"])
    store.update_job(job["id"], status="running")
    try:
        result = lab.run_study(store, job["id"], request, lambda p, m: print(f"{p:.1f}% {m}", flush=True))
        store.update_job(job["id"], status="done", result=result, progress=100, message="真实历史行情验证完成")
        assert store.meta("investment_settings") == before and store.meta("portfolio_state") is None
        receipt = {"id": job["id"], "mode": args.mode, "data_identity": result["data_identity"],
            "winner_id": result["winner_id"], "complete": result["complete"],
            "rows": [{"label": r["label"], "selection": {k: v for k, v in r["selection"].items() if k not in {"curve", "training"}},
                      "check": {k: v for k, v in r.get("check", {}).items() if k not in {"curve", "training"}}} for r in result["candidates"]]}
        (OUTPUT / f"{args.mode}.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(receipt, ensure_ascii=False), flush=True)
    except BaseException as exc:
        store.update_job(job["id"], status="failed", error=str(exc))
        raise


if __name__ == "__main__":
    main()
