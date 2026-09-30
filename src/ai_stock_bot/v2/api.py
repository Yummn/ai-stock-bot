from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from starlette.middleware.gzip import GZipMiddleware

from .execution import Account
from .evaluation import find_evidence, read_gzip
from .profile_selection import preview_profiles
from .jobs import JobManager
from .market import Market
from .schema import AppConfig, JobRequest, MODEL_FIELDS, MODEL_NAMES, PROFILES, SYMBOLS, StrictModel, default_presets
from .store import ConflictError, Store, dumps


class SettingsWrite(StrictModel):
    config: AppConfig
    revision: int = Field(ge=1)


class ManualTrade(StrictModel):
    symbol: str
    side: str
    quantity: int = Field(ge=1)


class DefaultsWrite(StrictModel):
    revision: int = Field(ge=1)


class AccessLogin(StrictModel):
    key: str = Field(min_length=1, max_length=200)


def create_app(data_root: Path | None = None, legacy_path: Path | None = None, start_manager=True, static_root: Path | None = None):
    root = Path(data_root or "data/v2")
    store = Store(root)
    key_path = root / "access-key.txt"
    try:
        with key_path.open("x", encoding="utf-8") as output:
            output.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    access_key = key_path.read_text(encoding="utf-8").strip()
    if legacy_path is not None:
        store.import_legacy(legacy_path)
    market = Market(store)
    manager = None
    account_lock = threading.RLock()

    @asynccontextmanager
    async def lifespan(app):
        nonlocal manager
        if start_manager:
            manager = JobManager(store)
        yield
        if manager:
            manager.close()

    app = FastAPI(title="StockLab", version="3.0.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.state.store = store

    def remote(request):
        local_hosts = {"127.0.0.1", "::1", "localhost"}
        peer = request.client.host if request.client else None
        if peer == "testclient":
            local_hosts.add("testserver")
        try:
            host = request.url.hostname
        except ValueError:
            return True
        # A loopback peer alone is insufficient behind a tunnel or DNS rebinding.
        return bool(request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
                    or peer not in {"127.0.0.1", "::1", "localhost", "testclient"}
                    or host not in local_hosts)

    def write_allowed(request):
        if not remote(request):
            return True
        try:
            payload, signature = request.cookies.get("stocklab_session", "").rsplit(".", 1)
            valid = hmac.new(access_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
            return int(payload.split(".")[0]) > time.time() and hmac.compare_digest(signature, valid)
        except (ValueError, TypeError):
            return False

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            from urllib.parse import urlsplit
            try:
                same_host = urlsplit(origin).netloc == request.headers.get("host")
            except ValueError:
                same_host = False
            if not same_host:
                return JSONResponse({"error": "不允许跨站修改本机数据"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path != "/api/v2/access/login" and not write_allowed(request):
            return JSONResponse({"error": "远程访问默认只读，请先输入管理口令"}, status_code=401)
        response = await call_next(request)
        if request.url.path.startswith("/api/v2/access"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v2/access")
    def access(request: Request):
        return {"remote": remote(request), "write_allowed": write_allowed(request)}

    @app.get("/api/v2/access/key")
    def local_key(request: Request):
        if remote(request):
            raise HTTPException(403, "管理口令只能在本机查看")
        return {"key": access_key}

    @app.post("/api/v2/access/login")
    def login(payload: AccessLogin, request: Request):
        if not hmac.compare_digest(payload.key, access_key):
            raise HTTPException(401, "管理口令不正确")
        session = f"{int(time.time() + 7 * 86400)}.{secrets.token_urlsafe(16)}"
        signature = hmac.new(access_key.encode(), session.encode(), hashlib.sha256).hexdigest()
        response = JSONResponse({"write_allowed": True})
        response.set_cookie("stocklab_session", session + "." + signature, max_age=7 * 86400, httponly=True, samesite="strict", secure=request.url.scheme == "https")
        return response

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        logging.getLogger("stocklab").exception("API failed: %s", request.url.path, exc_info=exc)
        return JSONResponse({"error": "服务处理失败，详细原因已记录到本机日志"}, status_code=500)

    @app.exception_handler(ConflictError)
    async def conflict(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        errors = [".".join(str(p) for p in e["loc"]) + ": " + e["msg"] for e in exc.errors()]
        return JSONResponse({"error": "；".join(errors)}, status_code=422)

    @app.get("/api/v2/health")
    def health():
        return {"status": "ok", "version": "4.8.0" if static_root else "3.1.0", "capabilities": ["investment_portfolio", "paper_execution_modes", "intraday_validation", "dynamic_exposure", "focused_validation", "forward_observation", "cnn_portfolio", "classification_portfolio", "ml_ai_review", "market_research"], "time": time.time(), "manager": manager is not None}

    @app.get("/api/v2/research")
    def research():
        return {"report": store.meta("market_research"), "jobs": store.jobs(5, kind="research")["items"]}

    @app.get("/api/v2/catalog")
    def catalog():
        baseline = store.defaults()
        return {"models": MODEL_NAMES, "profiles": PROFILES, "symbols": SYMBOLS, "parameters": MODEL_FIELDS, "defaults": baseline["config"], "defaults_saved_at": baseline["saved_at"], "execution": "日线 · 下一交易日开盘成交 · 手续费与滑点 · 分红未模拟"}

    @app.post("/api/v2/settings/defaults")
    def capture_defaults(payload: DefaultsWrite):
        return store.capture_defaults(payload.revision)

    @app.get("/api/v2/settings")
    def settings():
        return store.settings()

    @app.put("/api/v2/settings")
    def save(payload: SettingsWrite):
        return store.save_settings(payload.config, payload.revision)

    @app.post("/api/v2/settings/validate")
    def validate_config(payload: AppConfig):
        return {"config": payload.model_dump()}

    @app.post("/api/v2/evidence")
    def evidence(payload: AppConfig):
        return find_evidence(store, payload)

    @app.get("/api/v2/evidence")
    def active_evidence():
        return find_evidence(store, AppConfig.model_validate(store.settings()["config"]))

    @app.post("/api/v2/profile-selection/preview")
    def profile_selection_preview(payload: AppConfig):
        return preview_profiles(store, payload)

    @app.get("/api/v2/profile-selection/preview")
    def active_profile_selection_preview():
        return preview_profiles(store, AppConfig.model_validate(store.settings()["config"]))

    @app.get("/api/v2/overview")
    def overview():
        cfg = AppConfig.model_validate(store.settings()["config"])
        account = Account(cfg.risk, store.meta("account"))
        with store.connect() as c:
            latest = c.execute("SELECT id FROM jobs WHERE kind='backtest' AND status='done' ORDER BY created DESC LIMIT 1").fetchone()
        summary = None
        if latest:
            result = store.job(latest[0], True)["result"]
            summary = {k: result.get(k) for k in ("return_pct", "excess_return", "trade_count", "max_drawdown", "start", "end")}
            summary["id"] = latest[0]
        data = account.dump()
        data["trades"] = data["trades"][-20:]
        return {"account": data, "decisions": store.meta("last_decisions", []), "last_test": summary, "jobs": store.jobs(6)["items"], "migration_notes": store.meta("migration_notes", []), "mode": cfg.engine, "model": cfg.model, "profile": cfg.profile, "name": cfg.name}

    @app.get("/api/v2/market")
    def quotes():
        cfg = AppConfig.model_validate(store.settings()["config"])
        return {"items": market.curves(cfg.symbols)}

    @app.get("/api/v2/data")
    def data():
        cfg = AppConfig.model_validate(store.settings()["config"])
        return {"items": market.coverage(list(dict.fromkeys(cfg.symbols + cfg.training_symbols)))}

    @app.get("/api/v2/labels")
    def labels():
        cfg = AppConfig.model_validate(store.settings()["config"])
        return {"items": market.labels(list(dict.fromkeys(cfg.symbols + cfg.training_symbols)), cfg.annual_labels)}

    @app.get("/api/v2/plans")
    def plans():
        with store.connect() as c:
            return {"items": [{"id": r["id"], "name": r["name"], "updated": r["updated"]} for r in c.execute("SELECT * FROM plans ORDER BY updated DESC")]}

    @app.post("/api/v2/plans")
    def save_plan(payload: AppConfig):
        plan_id = uuid.uuid4().hex
        with store.connect() as c:
            c.execute("INSERT INTO plans VALUES(?,?,?,?)", (plan_id, payload.name, dumps(payload.model_dump()), time.time()))
        return {"id": plan_id, "name": payload.name}

    @app.get("/api/v2/plans/{plan_id}")
    def plan(plan_id: str):
        with store.connect() as c:
            row = c.execute("SELECT config FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise KeyError("方案不存在")
        return {"config": json.loads(row[0])}

    @app.get("/api/v2/jobs")
    def jobs(limit: int = 20, offset: int = 0, kind: str | None = None):
        if kind and kind not in {"backtest", "evaluate", "learn", "refresh", "paper", "research"}:
            raise ValueError("任务类型无效")
        return store.jobs(max(1, min(limit, 100)), max(offset, 0), kind)

    @app.post("/api/v2/jobs", status_code=202)
    def submit(request: JobRequest):
        cfg = AppConfig.model_validate(store.settings()["config"]) if request.kind == "paper" else request.config or AppConfig.model_validate(store.settings()["config"])
        request.config = cfg
        if request.kind == "learn" and cfg.engine != "ml":
            raise ValueError("自动学习需要选择机器学习模式")
        if request.kind == "backtest" and cfg.engine == "ai" and request.period != "random14":
            raise ValueError("AI 仅允许 14 个交易日的短期测试")
        if request.kind == "evaluate" and cfg.engine == "ai":
            raise ValueError("AI 请使用短期验证")
        if store.jobs(100)["items"] and sum(j["status"] in {"queued", "running"} for j in store.jobs(100)["items"]) >= 5:
            raise ValueError("已有 5 个任务等待或执行，请完成后再提交")
        with account_lock:
            if request.kind == "research" and any(j["status"] in {"running", "queued"} for j in store.jobs(100, kind="research")["items"]):
                raise ValueError("联网研究已在进行，请等待完成")
            return store.create_job(request.model_dump())

    @app.get("/api/v2/jobs/{job_id}")
    def job(job_id: str):
        return store.job(job_id, True)

    @app.get("/api/v2/jobs/{job_id}/years/{year}")
    def evaluation_year(job_id: str, year: int):
        job = store.job(job_id, True)
        if job["kind"] != "evaluate" or year not in job["request"]["years"]:
            raise KeyError("该任务没有这个年份")
        path = store.root / "evaluations" / job["id"] / f"{year}.json.gz"
        if not path.exists():
            raise KeyError("该年份尚未完成")
        return {**job, "result": read_gzip(path)}

    @app.get("/api/v2/jobs/{job_id}/status")
    def job_status(job_id: str):
        return store.job(job_id)

    @app.get("/api/v2/jobs/{job_id}/events")
    def events(job_id: str, after: int = 0):
        return {"items": store.events(job_id, after)}

    @app.get("/api/v2/jobs/{job_id}/trials")
    def trial_page(job_id: str, limit: int = 15, offset: int = 0):
        store.job(job_id)
        return store.trial_page(job_id, max(1,min(limit,100)), max(0,offset))

    @app.post("/api/v2/jobs/{job_id}/stop")
    def stop_job(job_id: str):
        if not manager:
            raise ValueError("任务执行器未启动")
        return manager.stop(job_id)

    @app.post("/api/v2/jobs/{job_id}/resume")
    def resume_job(job_id: str):
        if not manager:
            raise ValueError("任务执行器未启动")
        return manager.resume(job_id)

    @app.post("/api/v2/jobs/{job_id}/apply/{profile}")
    def apply(job_id: str, profile: str):
        if profile not in PROFILES:
            raise ValueError("档位无效")
        job = store.job(job_id, True)
        result = job.get("result") or {}
        suggestion = result.get("best", {}).get(profile)
        if not suggestion or not suggestion["complete"]:
            raise ValueError("该档位尚无完整验证的建议")
        required_checks = job["request"].get("check_years", [])
        checked = {row["year"] for row in suggestion.get("check_years", []) if "return_pct" in row}
        if not set(required_checks).issubset(checked):
            raise ValueError("验收年份尚未全部完成，暂不能启用")
        current = store.settings()
        cfg = AppConfig.model_validate(current["config"])
        suggested = AppConfig.model_validate(suggestion["config"])
        cfg.models[suggested.model][profile] = suggested.models[suggested.model][profile]
        cfg.engine, cfg.model, cfg.profile = "ml", suggested.model, profile
        saved = store.save_settings(cfg, current["revision"])
        store.event(job_id, f"已应用并保存 {profile} 档建议，配置版本 {saved['revision']}")
        return saved

    @app.post("/api/v2/account/reset")
    def reset_account():
        with account_lock:
            with store.connect() as c:
                if c.execute("SELECT 1 FROM jobs WHERE kind='paper' AND status IN ('queued','running')").fetchone():
                    raise ValueError("请先停止正在运行的模拟任务")
            cfg = AppConfig.model_validate(store.settings()["config"])
            account = Account(cfg.risk).dump()
            store.set_meta("account", account)
            store.set_meta("last_decisions", [])
            return account

    @app.post("/api/v2/account/trade")
    def manual_trade(payload: ManualTrade):
        with account_lock:
            return perform_manual_trade(payload)

    def perform_manual_trade(payload):
        if payload.symbol not in SYMBOLS or payload.side not in {"buy", "sell"}:
            raise ValueError("标的或方向无效")
        with store.connect() as c:
            if c.execute("SELECT 1 FROM jobs WHERE kind='paper' AND status IN ('queued','running')").fetchone():
                raise ValueError("请等待模拟任务结束后手动交易")
        frame = market.read(payload.symbol)
        if frame.empty:
            raise ValueError("该标的没有缓存行情，请先更新数据")
        cfg = AppConfig.model_validate(store.settings()["config"])
        account = Account(cfg.risk, store.meta("account"))
        date = str(frame.index[-1].date())
        if account.settlement_date and date < account.settlement_date:
            raise ValueError("该标的行情落后于账户日期，请先更新行情")
        account.new_day(date)
        row, reason = account.trade(payload.symbol, payload.side, float(frame.iloc[-1].close), date, "手动模拟交易（最近缓存收盘价）", cfg.profile, payload.quantity, allow_add=True)
        if not row:
            raise ValueError(reason)
        account.mark(payload.symbol, frame.iloc[-1].close)
        store.set_meta("account", account.dump())
        store.set_meta("last_manual_date", date)
        return {"trade": row, "account": account.dump()}

    from ..v4.routes import register
    register(app, store, account_lock)
    static = static_root or Path(__file__).parent / "static"
    if (static / "assets").exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/")
    def index():
        if not (static / "index.html").exists():
            raise HTTPException(503, "请先在 frontend 目录执行 npm run build")
        return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def main():
    import multiprocessing
    import uvicorn

    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="StockLab V2")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default="data/v2")
    args = parser.parse_args()
    uvicorn.run(create_app(Path(args.data_dir), Path("data/paper_trading.sqlite3")), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
