from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr, UUID4

from ..v2.schema import AppConfig, JobRequest
from ..v2.store import dumps
from .data import audit, cached_histories, intraday_coverage, intraday_points, PortfolioMarket
from .leverage import daily_leverage_scenarios
from .schema import ASSETS, Plan, fingerprint
from .service import current_report_for, save_settings, settings, snapshot_revisions, summary


class PlanWrite(BaseModel):
    plan: Plan
    revision: int = Field(ge=1)
    profile_slot: Literal["conservative", "balanced", "aggressive", "custom"] | None = None


class RunRequest(BaseModel):
    kind: str
    start_year: int | None = Field(default=None, ge=2015, le=2099)
    end_year: int | None = Field(default=None, ge=2015, le=2099)
    research_method: str | None = None
    compare_models: bool = False


class ObservationAction(BaseModel):
    action: Literal['start', 'pause', 'resume', 'archive', 'update']
    revision: int | None = None
    plan_key: str | None = None


class BrokerSnapshotRequest(BaseModel):
    account: str = Field(min_length=3, max_length=40)
    password: SecretStr


class BrokerTestBuyRequest(BrokerSnapshotRequest):
    symbol: str
    price: Decimal = Field(gt=0, le=10, decimal_places=3)
    request_id: UUID4
    confirmation: Literal['提交仿真测试委托']


def _local_broker_request(request: Request) -> bool:
    host = urlsplit(f"http://{request.headers.get('host', '')}").hostname
    return (request.client is not None and request.client.host in {'127.0.0.1', '::1'}
            and host in {'127.0.0.1', 'localhost'}
            and not request.headers.get('cf-connecting-ip')
            and not request.headers.get('x-forwarded-for'))


def register(app, store, lock):
    from .lab_routes import register as register_lab
    register_lab(app, store, lock)

    @app.get('/api/v2/investment/broker/availability')
    def broker_availability(request: Request):
        from .broker_emt import available, order_available
        local = _local_broker_request(request)
        return {"local_only": True, "available": available() if local else False,
                "manual_test_order_available": order_available() if local else False}

    @app.post('/api/v2/investment/broker/snapshot')
    def broker_snapshot(payload: BrokerSnapshotRequest, request: Request):
        from .broker_emt import snapshot
        if not _local_broker_request(request):
            raise HTTPException(403, '券商账户只允许在本机 127.0.0.1 查询')
        return JSONResponse(snapshot(payload.account, payload.password.get_secret_value()),
                            headers={"Cache-Control": "no-store"})

    @app.get('/api/v2/investment/broker/live-readiness')
    def broker_live_readiness(request: Request):
        from .broker_emt_live import readiness
        if not _local_broker_request(request):
            raise HTTPException(403, '实盘账户状态只允许在本机查看')
        return JSONResponse(readiness(), headers={"Cache-Control": "no-store"})

    @app.post('/api/v2/investment/broker/live-snapshot')
    def broker_live_snapshot(request: Request):
        from .broker_emt_live import snapshot
        if not _local_broker_request(request):
            raise HTTPException(403, '实盘只读查询只允许在本机使用')
        origin = request.headers.get('origin')
        if origin and origin != f"{request.url.scheme}://{request.headers['host']}":
            raise HTTPException(403, '实盘只读查询来源不匹配')
        return JSONResponse(snapshot(), headers={"Cache-Control": "no-store"})

    @app.post('/api/v2/investment/broker/test-buy')
    def broker_test_buy(payload: BrokerTestBuyRequest, request: Request):
        from .broker_emt import submit_test_buy
        if not _local_broker_request(request):
            raise HTTPException(403, '仿真测试委托只允许在本机 127.0.0.1 提交')
        origin = request.headers.get('origin')
        if origin and origin != f"{request.url.scheme}://{request.headers['host']}":
            raise HTTPException(403, '测试委托来源不匹配')
        result = submit_test_buy(payload.account, payload.password.get_secret_value(),
                                 payload.symbol, payload.price, str(payload.request_id))
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get('/api/v2/investment/research-combinations')
    def research_combinations():
        from .research_report import KEY
        return {"report": store.meta(KEY)}

    @app.get('/api/v2/investment/research-combinations/export')
    def export_research_combinations():
        from .research_report import KEY
        result = store.meta(KEY)
        if not result:
            raise ValueError('还没有已完成的组合研究')
        return JSONResponse(result, headers={'Content-Disposition': 'attachment; filename="combination-research.json"'})

    @app.get('/api/v2/investment/observation')
    def observation():
        from .observation import observation_view
        return observation_view(store, Plan.model_validate(settings(store)['plan']))

    @app.get('/api/v2/investment/observation/export')
    def export_observation(study_id: str = Query(pattern=r'^[a-f0-9]{32}$')):
        from .observation import KEY
        study = store.meta(KEY)
        if not study or study['id'] != study_id:
            study = store.meta(f'{KEY}:{study_id}')
        if not study:
            raise ValueError('观察记录不存在')
        return JSONResponse(study, headers={'Content-Disposition': f'attachment; filename="observation-{study_id}.json"'})

    @app.post('/api/v2/investment/observation')
    def observe(payload: ObservationAction):
        from .observation import control, enqueue_observation, observation_view
        from ..v2.store import ConflictError
        with lock:
            plan = Plan.model_validate(settings(store)['plan'])
            if payload.action == 'start' and payload.plan_key != fingerprint(plan):
                raise ConflictError('投资计划已更改，请刷新后再开始观察')
            if payload.action != 'update':
                control(store, payload.action, plan, payload.revision)
            if payload.action in {'start', 'resume', 'update'}:
                enqueue_observation(store, manual=True)
            return observation_view(store, plan)

    @app.get("/api/v2/investment")
    def overview():
        return summary(store)

    @app.put("/api/v2/investment/plan")
    def save(payload: PlanWrite):
        with lock:
            return save_settings(store, payload.plan, payload.revision, payload.profile_slot)

    @app.get("/api/v2/investment/evidence")
    def evidence():
        report = current_report_for(store, Plan.model_validate(settings(store)["plan"]))
        if report:
            report["market_revisions"] = snapshot_revisions(store, report)
        return JSONResponse({"report": report})

    @app.get("/api/v2/investment/leverage")
    def leverage(method: str | None = None, cadence: str | None = None,
                 annual_rate: float | None = Query(default=None, ge=0, le=.3),
                 maintenance_ratio: float | None = Query(default=None, ge=0, le=.8)):
        plan = Plan.model_validate(settings(store)["plan"])
        if plan.research_multiple != 1 or plan.research_interval != "daily":
            raise ValueError("当前计划已按所选倍数逐笔执行；不再对结果重复应用旧版净值倍数估算")
        method = method or plan.method
        annual_rate = plan.research_annual_rate if annual_rate is None else annual_rate
        maintenance_ratio = plan.research_maintenance_ratio if maintenance_ratio is None else maintenance_ratio
        if method not in {"rotation", "hold", "ml_class", "ml", "cnn_class", "cnn"}:
            raise ValueError("不支持的基础组合")
        report = current_report_for(store, plan)
        if not report or method not in report["results"]:
            raise ValueError("请先对当前投资计划运行历史验证")
        selected_cadence = cadence or plan.rebalance
        if selected_cadence not in {"daily", "weekly", "monthly"}:
            raise ValueError("调仓频率无效")
        curve = report["results"][method]["curve"] if selected_cadence == plan.rebalance or method == "hold" else None
        source_frequency_job_id = None
        if curve is None:
            with store.connect() as c:
                row = c.execute("SELECT id,result FROM jobs WHERE kind='portfolio_frequency' AND status='done' "
                                "AND json_extract(result,'$.plan_key')=? AND json_extract(result,'$.method')=? "
                                "AND json_extract(result,'$.source_report_id')=? ORDER BY created DESC LIMIT 1",
                                (report["plan_key"], method, report["id"])).fetchone()
            experiment = json.loads(row["result"]) if row else None
            curve = (experiment or {}).get("results", {}).get(selected_cadence, {}).get("base", {}).get("curve")
            if not curve:
                raise ValueError("此调仓频率尚无同源结果，请先运行频率实验")
            source_frequency_job_id = row["id"]
        return JSONResponse({"method": method, "start": report["start"], "end": report["end"],
                             "source_report_id": report["id"], "starting_cash": plan.starting_cash,
                             "cadence": selected_cadence, "source_frequency_job_id": source_frequency_job_id,
                             "plan_key": report["plan_key"],
                             "annual_rate": annual_rate, "maintenance_ratio": maintenance_ratio,
                             "scenarios": daily_leverage_scenarios(curve,
                                                                    plan.starting_cash, annual_rate,
                                                                    maintenance_ratio)})

    @app.get("/api/v2/investment/frequency")
    def frequency(method: str = "rotation"):
        if method not in {"rotation", "ml_class", "ml", "cnn_class", "cnn"}:
            raise ValueError("不支持的研究模型")
        plan = Plan.model_validate(settings(store)["plan"])
        report = current_report_for(store, plan)
        if not report:
            return {"result": None}
        with store.connect() as c:
            row = c.execute("SELECT result FROM jobs WHERE kind='portfolio_frequency' AND status='done' "
                            "AND json_extract(result,'$.plan_key')=? AND json_extract(result,'$.method')=? "
                            "AND json_extract(result,'$.source_report_id')=? ORDER BY created DESC LIMIT 1",
                            (report["plan_key"], method, report["id"])).fetchone()
        result = json.loads(row["result"]) if row else None
        if result:
            for cadence in result.get("results", {}).values():
                base = cadence.get("base", {})
                base["has_curve"] = bool(base.pop("curve", None))
        return {"result": result}

    @app.get("/api/v2/investment/data")
    def data():
        plan = Plan.model_validate(settings(store)["plan"])
        return {"audit": audit(cached_histories(store, plan), plan.symbols), "intraday": intraday_coverage(store)}

    @app.get("/api/v2/investment/intraday-research")
    def intraday(symbol: str | None = None, annual_rate: float | None = Query(default=None, ge=0, le=.3)):
        plan = Plan.model_validate(settings(store)["plan"])
        symbol = symbol or plan.research_symbol
        if symbol not in plan.symbols or not ASSETS[symbol]["t0"]:
            raise ValueError("请先在投资计划中选择支持当日回转的 ETF")
        selected = plan.model_copy(update={"research_symbol": symbol,
            "research_annual_rate": plan.research_annual_rate if annual_rate is None else annual_rate})
        with store.connect() as connection:
            row = connection.execute("SELECT id,created,result FROM jobs WHERE kind='intraday_test' AND status='done' "
                                     "AND json_extract(result,'$.plan_key')=? ORDER BY created DESC LIMIT 1",
                                     (fingerprint(selected),)).fetchone()
        return {"result": {**json.loads(row["result"]), "job_id": row["id"], "tested_at": row["created"]} if row else None}

    @app.get("/api/v2/investment/chart/{symbol}")
    def chart(symbol: str, interval: str = "daily"):
        if symbol not in ASSETS or interval not in {"daily", "30m", "60m"}:
            raise ValueError("标的或时间粒度无效")
        if interval != "daily":
            points = intraday_points(store, symbol, interval)
        else:
            frame = PortfolioMarket(store).read(symbol).tail(250)
            points = [{"date": str(d.date()), "close": float(r.close)} for d, r in frame.iterrows()]
        return {"symbol": symbol, "name": ASSETS[symbol]["name"], "interval": interval, "points": points}

    @app.post("/api/v2/investment/run", status_code=202)
    def run(payload: RunRequest):
        if payload.kind not in {"portfolio_test", "portfolio_frequency", "portfolio_update", "portfolio_review", "intraday_refresh", "intraday_test"}:
            raise ValueError("任务类型无效")
        if payload.kind == "portfolio_frequency" and payload.research_method not in {"rotation", "ml_class", "ml", "cnn_class", "cnn"}:
            raise ValueError("请选择支持的研究模型")
        with lock:
            active = [j for j in store.jobs(100)["items"] if j["status"] in {"queued", "running"}]
            if any(j["kind"] == payload.kind for j in active) or len(active) >= 5:
                raise ValueError("已有同类任务正在进行，请等待完成")
            plan = Plan.model_validate(settings(store)["plan"])
            if payload.kind == "intraday_test" and plan.research_interval == "daily":
                raise ValueError("请先选择 30 或 60 分钟的盘中计划")
            if plan.research_interval != "daily" and payload.kind in {"portfolio_test", "portfolio_frequency"}:
                raise ValueError("盘中计划请使用盘中验证；日线组合回测不适用于该执行模式")
            first_year = payload.start_year if payload.start_year is not None else plan.validation_start_year
            last_year = payload.end_year if payload.end_year is not None else plan.validation_end_year
            if first_year > last_year:
                raise ValueError("起始年份不能晚于结束年份")
            if payload.kind == "portfolio_frequency" and not current_report_for(store, plan):
                raise ValueError("请先对当前投资计划完成历史验证")
            job = JobRequest(kind=payload.kind, investment=plan,
                             years=list(range(first_year, last_year + 1)),
                             research_method=payload.research_method,
                             compare_models=payload.compare_models,
                             config=AppConfig.model_validate(store.settings()["config"]))
            return store.create_job(job.model_dump())

    @app.post("/api/v2/investment/reset")
    def reset():
        with lock, store.connect() as c:
            # Serialize the active-job check, archive and reset with scheduler writes.
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM jobs WHERE kind='portfolio_update' AND status IN ('queued','running') LIMIT 1").fetchone():
                raise ValueError("请先停止正在更新的模拟")
            row = c.execute("SELECT value FROM meta WHERE key='portfolio_state'").fetchone()
            state = json.loads(row[0]) if row else None
            if state:
                c.execute("INSERT INTO meta(key,value) VALUES(?,?)", (f"portfolio_archive:{time.time_ns()}", dumps(state)))
            for key in ("portfolio_state", "portfolio_review"):
                c.execute("INSERT INTO meta(key,value) VALUES(?,'null') ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key,))
            return {"archived": bool(state)}
