from __future__ import annotations

import datetime as dt
import json
import time
from io import StringIO

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from ..v2.evaluation import read_gzip, write_gzip
from ..v2.market import TZ, today
from ..v2.store import ConflictError, dumps
from .data import audit, load_histories, PortfolioMarket
from .portfolio import Portfolio, simulate
from .forward import advance_daily, checked_daily
from .schema import ASSETS, ENGINE_VERSION, METHODS, FIXED_PROFILE_PRESETS, Plan, comparison_fingerprint, fingerprint


def settings(store):
    value = store.meta("investment_settings")
    if not value:
        return {"plan": Plan(execution_policy="corrected").model_dump(), "revision": 1}
    return {**value, "plan": Plan.model_validate(value["plan"]).model_dump()}


def save_settings(store, plan, revision, profile_slot=None):
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT value FROM meta WHERE key='investment_settings'").fetchone()
        current = json.loads(row[0]) if row else {"plan": Plan().model_dump(), "revision": 1}
        if c.execute("SELECT 1 FROM jobs WHERE kind='portfolio_update' AND status IN ('running','queued') LIMIT 1").fetchone():
            raise ValueError("模拟正在更新，请完成或停止后再保存计划")
        if current["revision"] != revision:
            raise ConflictError("其他设备已更新投资计划，请重新载入后再保存")
        state_row = c.execute("SELECT value FROM meta WHERE key='portfolio_state'").fetchone()
        state = json.loads(state_row[0]) if state_row else None
        if state and plan.starting_cash != current["plan"]["starting_cash"]:
            raise ValueError("模拟已开始；调整起始资金前请先结束本次模拟")
        if state and any(getattr(plan, field) != current["plan"][field] for field in
                         ("research_interval", "research_multiple", "research_symbol")):
            raise ValueError("更换时间粒度、倍数或小时 ETF 前，请先归档当前模拟账户；历史成交不会被重新解释")
        if state and plan.execution_policy != current["plan"].get("execution_policy", "legacy"):
            raise ValueError("更换执行规则前，请先归档当前模拟账户；不会改写已有成交")
        if state and plan.allocation_policy != current["plan"].get("allocation_policy", "legacy"):
            raise ValueError("更换组合政策前，请先归档当前模拟账户；不会混用旧政策的成交")
        if state and set(state["account"]["positions"]) - set(plan.symbols):
            raise ValueError("投资范围中仍有持仓，不能直接移除；请先结束本次模拟或保持这些标的")
        from .profile_library import save_in_transaction
        slot = profile_slot or plan.profile
        save_in_transaction(c, current, plan, slot)
        saved = {"plan": plan.model_dump(), "revision": revision + 1, "saved_at": time.time(), "profile_slot": slot}
        c.execute("INSERT INTO meta VALUES('investment_settings',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (dumps(saved),))
    return saved


def report_for(store, plan, years=None):
    wanted = fingerprint(plan)
    start = f"{years[0]}-01-01" if years else None
    end = f"{years[1]}-12-31" if years else None
    with store.connect() as c:
        rows = c.execute("SELECT id,created,result FROM jobs WHERE kind='portfolio_test' AND status='done' "
                         "AND json_extract(result,'$.plan_key')=? "
                         "AND json_extract(result,'$.complete')=1 "
                         "AND (? IS NULL OR json_extract(result,'$.start')=?) "
                         "AND (? IS NULL OR json_extract(result,'$.end')=?) "
                         "ORDER BY created DESC LIMIT 1", (wanted, start, start, end, end)).fetchall()
    for row in rows:
        result = json.loads(row["result"] or "{}")
        if result.get("plan_key") == wanted and result.get("complete"):
            return {"id": row["id"], "tested_at": row["created"], **result}
    with store.connect() as c:
        rows = c.execute("SELECT id,created,result FROM jobs WHERE kind='portfolio_test' AND status='done' "
                         "AND (? IS NULL OR json_extract(result,'$.start')=?) "
                         "AND (? IS NULL OR json_extract(result,'$.end')=?) "
                         "ORDER BY created DESC LIMIT 30", (start, start, end, end)).fetchall()
    comparable = comparison_fingerprint(plan)
    for row in rows:
        result = json.loads(row["result"] or "{}")
        if result.get("engine") != ENGINE_VERSION or not result.get("complete"):
            continue
        if not all(key in result.get("results", {}) for key in ("rotation", "ml_class", "ml", "cnn_class", "cnn", "hold", "hold_full")):
            continue
        if result.get("comparison_key") != comparable:
            try:
                previous = Plan.model_validate(result["plan"])
            except (KeyError, ValueError):
                continue
            if comparison_fingerprint(previous) != comparable:
                continue
        reused = {**result, "id": row["id"], "tested_at": row["created"], "plan": plan.model_dump(), "plan_key": wanted,
                  "comparison_key": comparable, "comparison_reused": True,
                  "results": {key: value for key, value in result["results"].items() if key != "stress"}}
        reused["checks"], reused["verdict"] = assessment(reused, plan.method)
        return reused
    return None


def current_report_for(store, plan):
    return report_for(store, plan, (plan.validation_start_year, plan.validation_end_year))


def snapshot_revisions(store, report):
    if not report:
        return None
    current = {row["symbol"]: row["hash"] for row in report.get("audit", {}).get("rows", [])}
    if not current:
        return None
    with store.connect() as c:
        rows = c.execute(
            "SELECT id,json_extract(result,'$.audit.rows') AS audit_rows FROM jobs "
            "WHERE kind='portfolio_test' AND status='done' AND id<>? "
            "AND json_extract(result,'$.start')=? AND json_extract(result,'$.end')=? "
            "ORDER BY created DESC LIMIT 30", (report["id"], report["start"], report["end"])).fetchall()
    for row in rows:
        if not row["audit_rows"]:
            continue
        previous = {item["symbol"]: item["hash"] for item in json.loads(row["audit_rows"])}
        if set(previous) != set(current):
            continue
        changed = [symbol for symbol in current if current[symbol] != previous[symbol]]
        if changed:
            return {"previous_report_id": row["id"], "symbols": changed,
                    "names": [ASSETS[symbol]["name"] for symbol in changed]}
        return None
    return None


def assessment(output, method):
    results = output["results"]
    chosen, hold = results[method], results["hold"]
    matching = {year["year"]: year["return_pct"] for year in hold["per_year"]}
    wins = sum(year["return_pct"] > matching[year["year"]] for year in chosen["per_year"])
    stress_method = output.get("stress_method", output["plan"]["method"])
    stress = results.get("stress") if stress_method == method else None
    checks = [
        {"name": "行情完整与口径一致", "ok": output["audit"]["ok"], "detail": f"{len(output['plan']['symbols'])} 只 ETF，固定同一份行情"},
        {"name": "未来标签隔离", "ok": all(row["label_end"] is None or row["label_end"] < row["cutoff"]
                                   for model in ("ml", "ml_class", "cnn", "cnn_class") for row in results.get(model, {}).get("training", [])),
         "not_applicable": not any(results.get(m, {}).get("training") for m in ("ml", "ml_class", "cnn", "cnn_class")),
         "detail": "每次拟合只使用截止日前完整结束的标签；不训练模型的方案不适用"},
        {"name": "跑赢同板块偏好持有", "ok": method != "hold" and chosen["return_pct"] > hold["return_pct"],
         "not_applicable": method == "hold",
         "detail": "持有方案就是对照基准，不与自身比较" if method == "hold" else f"{wins}/{len(chosen['per_year'])} 年跑赢持有"},
        {"name": "交易成本加倍仍为正", "ok": stress["return_pct"] > 0 if stress else False,
         "pending": stress is None,
         "detail": f"加倍后累计收益 {stress['return_pct']:.2%}" if stress else "当前方案尚未运行成本加倍验证"},
        {"name": "前向验证", "ok": False, "pending": True,
         "detail": "独立记录保存计划之后的模拟表现，并与同板块偏好持有比较"},
    ]
    years = chosen["per_year"]
    stable = len(years) >= 3 and all(row["return_pct"] > 0 for row in years)
    checks.append({"name": "跨年稳定性", "ok": stable,
                   "detail": f"{sum(row['return_pct'] > 0 for row in years)}/{len(years)} 年为正；单年盈利不等于稳定"})
    if "unlevered" in results:
        normal = results["unlevered"]
        checks.append({"name": "放大仓位是否增加净收益", "ok": chosen["return_pct"] > normal["return_pct"],
                       "not_applicable": output["plan"]["research_multiple"] == 1,
                       "detail": f"同策略 1x 收益 {normal['return_pct']:.2%}，回撤 {normal['max_drawdown']:.2%}"})
    verdict = ("被动持有基准，需前向模拟观察" if method == "hold"
               else "可以进入前向模拟观察" if checks[2]["ok"] and checks[3]["ok"]
               else "当前方案成本压力待验证" if checks[2]["ok"] and stress is None
               else "历史优势不足，继续观察与研究")
    return checks, verdict


def summary(store):
    from .preset_evidence import current_evidence
    from .profile_library import view as profile_view
    from .model_recipes import recipe_catalog
    current = settings(store)
    plan = Plan.model_validate(current["plan"])
    state = store.meta("portfolio_state")
    market = PortfolioMarket(store)
    latest = state["decisions"][-1] if state and state.get("decisions") else None
    review = store.meta("portfolio_review")
    if review and (review.get("plan_key") != fingerprint(plan) or review.get("as_of") != (state or {}).get("last_date")
                   or review.get("input", {}).get("rules") != review_rules(plan)):
        review = None
    account = state["account"] if state else {"cash": plan.starting_cash, "equity": plan.starting_cash, "positions": {}, "trades": [], "fees": 0}
    with store.connect() as c:
        jobs = [dict(r) for r in c.execute(
            "SELECT id,kind,status,progress,message,created,updated,error FROM jobs "
            "WHERE (kind LIKE 'portfolio_%' OR kind LIKE 'intraday_%') AND "
            "(status IN ('queued','running') OR id IN (SELECT id FROM jobs "
            "WHERE kind LIKE 'portfolio_%' OR kind LIKE 'intraday_%' ORDER BY created DESC LIMIT 12)) "
            "ORDER BY created DESC")]
    return {"settings": current, "plan_key": fingerprint(plan), "account": {**account, "trades": account["trades"][-30:]},
            "date": state.get("last_date") if state else None,
            "quote_age_days": (today()-dt.date.fromisoformat(state['last_date'][:10])).days if state and state.get('last_date') else None,
            "started_at": state.get("started_at") if state else None,
            "cooldown": state.get("cooldown", 0) if state else 0,
            "curve": state.get("curve", []) if state else [], "decision": latest,
            "rejections": state.get('rejections', [])[-20:] if state else [],
            "plan_changed": bool(state and state.get("plan_key") != fingerprint(plan)),
            "forward": forward_progress(state, plan) if plan.research_interval == "daily" else {"status": "not_available", "sessions": 0},
            "coverage": market.coverage(plan.symbols), "catalog": ASSETS,
            "profiles": FIXED_PROFILE_PRESETS,
            "profile_library": profile_view(store, current), "model_recipes": recipe_catalog(),
            "profile_algorithm_study": store.meta("profile_algorithm_study"),
            "profile_evidence": current_evidence(store),
            "ai_review": review, "mode": "paper",
            "execution": {"interval": plan.research_interval, "multiple": plan.research_multiple,
                          "symbol": plan.research_symbol if plan.research_interval != "daily" else None,
                          "paper_status": "active" if state and state.get("plan_key") == fingerprint(plan) else "not_started",
                          "live_status": "blocked_no_broker"},
            "broker": {"connected": False, "can_trade": False},
            "live": {"status": "not_connected", "provider": None, "account_masked": None,
                     "requested_interval": plan.research_interval, "requested_multiple": plan.research_multiple,
                     "requested_symbol": plan.research_symbol if plan.research_interval != "daily" else None,
                     "last_sync": None, "equity": None, "cash": None, "positions": None,
                     "open_orders": None, "programmatic_access": "unverified",
                     "account_reconciled": False, "limits_configured": False,
                     "orders_enabled": False, "automatic_orders_enabled": False,
                     "remote_orders_enabled": False},
            "jobs": jobs}


def comparison(store, job_id, request, progress):
    plan = request.investment
    start = dt.date(min(request.years), 1, 1)
    end = dt.date(max(request.years), 12, 31)
    if end >= today():
        raise ValueError("完整自然年验证只能选择已结束年份")
    root = store.root / "portfolio_tests" / job_id
    snapshot = root / "market.json.gz"
    if not snapshot.exists():
        histories, checked = load_histories(store, plan, start, end, progress)
        write_gzip(snapshot, {"engine": ENGINE_VERSION, "plan_key":fingerprint(plan), "audit": checked, "frames": {s: f.to_json(orient="split", date_format="iso", double_precision=15) for s, f in histories.items()}})
    frozen = read_gzip(snapshot)
    if frozen.get('engine') != ENGINE_VERSION or frozen.get('plan_key') != fingerprint(plan):
        raise ValueError('计算版本或计划已改变；请新建验证，不能混用旧任务的阶段结果')
    histories = {s: pd.read_json(StringIO(data), orient="split") for s, data in frozen["frames"].items()}
    for frame in histories.values():
        frame.index = pd.to_datetime(frame.index)
    checked = audit(histories, plan.symbols, start, end)
    if not checked["ok"]:
        raise ValueError("冻结行情未通过检查：" + "；".join(checked["blockers"]))
    active_groups = {ASSETS[symbol]["group"] for symbol in plan.symbols}
    active_total = sum(part for group, part in plan.allocation.items() if group in active_groups)
    full_allocation = {group: part / active_total if group in active_groups and active_total else 0.
                       for group, part in plan.allocation.items()}
    full_hold_plan = plan.model_copy(update={"max_exposure": 1., "max_weight": 1.,
                                             "allocation": full_allocation, "profile": "custom"})
    output = {"plan": plan.model_dump(), "plan_key": fingerprint(plan), "comparison_key": comparison_fingerprint(plan),
              "stress_method": plan.method, "engine": ENGINE_VERSION,
              "start": str(start), "end": str(end), "audit": checked, "results": {}, "complete": False,
              "protocol": f"同一冻结日线行情、资产板块偏好和组合执行规则；目标仓位按 {plan.research_multiple}x 放大，并逐笔重放次日开盘成交、佣金、滑点、借款利息与维保减仓，不是净值曲线简单放大。同配置持有使用相同倍数。分类沿用未来收盘涨幅超过 0.1% 的标签，回归预测下一开盘成交净收益；按月滚动训练。历史研究数据不等于未观察过的样本外数据，也不代表券商真实融资成交。"}
    runs = [("rotation", plan, "rotation"), ("ml_class", plan, "ml_class"), ("ml", plan, "ml"),
            ("cnn_class", plan, "cnn_class"), ("cnn", plan, "cnn"), ("hold", plan, "hold"),
            ("hold_full", full_hold_plan, "hold"),
            ("stress", plan.model_copy(update={"commission_rate": plan.commission_rate * 2,
                                               "min_commission": plan.min_commission * 2,
                                               "slippage_bps": plan.slippage_bps * 2,
                                               "research_annual_rate": plan.research_annual_rate * (1.5 if plan.execution_policy != "legacy" else 1)}), plan.method)]
    if not request.compare_models:
        runs = [(key, current, method) for key, current, method in runs
                if key in {plan.method, "hold", "hold_full", "stress"}]
        unlevered = plan.model_copy(update={"research_multiple": 1})
        runs.extend([("unlevered", unlevered, plan.method), ("hold_1x", unlevered, "hold")])
    if plan.allocation_policy != "legacy":
        runs = [(key, current, method) for key, current, method in runs if method in {"ml", "rotation", "hold"}]
    output["scope"] = "all_models" if request.compare_models else "current_plan"
    output["protocol"] += " 同策略 1x 与 1x 持有使用独立账户；持有基准不应用择时与动态波动控仓。融资按实际借款跨越的自然日计息。"
    if plan.execution_policy != "legacy":
        output["protocol"] += " 新执行规则首次建仓不受微小调仓门槛影响；压力场景融资利率额外提高50%。成本约束模式先优化仓位，不一定用满目标倍数。"
    if plan.method == "ml" and plan.model_recipe != "legacy":
        from .profile_library import signal_name
        output["protocol"] += f" 当前信号配方为{signal_name(plan)}，与独立研究共用训练实现；分类对照不使用该配方。"
    if plan.allocation_policy != "legacy":
        output["protocol"] += " 核心组合采用一次联合配置，比较当前回归配方、趋势及持有基准，不混入旧分类或旧CNN管线。现金收益按零计，国债仍有利率风险。"
    for index, (key, current, method) in enumerate(runs):
        path = root / f"{key}.json.gz"
        if path.exists():
            result = read_gzip(path)
        else:
            result = simulate(store, current, histories, start, end, method,
                              progress=lambda p, m: progress(10 + (index + p / 100) * 85 / len(runs), f"{METHODS[method]} · {key} · {m}"),
                              forecast_plan=plan)
            write_gzip(path, result)
        output["results"][key] = {k: v for k, v in result.items() if k not in {"decisions", "rejections"}}
        store.update_job(job_id, result=output)
    output["complete"] = True
    output["checks"], output["verdict"] = assessment(output, plan.method)
    return output


def frequency_comparison(store, request, progress):
    plan = request.investment
    method = request.research_method or plan.method
    report = current_report_for(store, plan)
    if not report:
        raise ValueError("请先对当前投资计划完成历史验证")
    snapshot = store.root / "portfolio_tests" / report["id"] / "market.json.gz"
    if not snapshot.exists():
        raise ValueError("历史行情快照已不存在，请重新运行历史验证")
    frozen = read_gzip(snapshot)
    if frozen.get("engine") != ENGINE_VERSION or set(plan.symbols) != set(frozen.get("frames", {})):
        raise ValueError("历史行情快照与当前投资计划不一致")
    histories = {s: pd.read_json(StringIO(data), orient="split") for s, data in frozen["frames"].items()}
    for frame in histories.values():
        frame.index = pd.to_datetime(frame.index)
    start, end = report["start"], report["end"]
    checked = audit(histories, plan.symbols, start, end)
    if not checked["ok"]:
        raise ValueError("冻结行情未通过检查：" + "；".join(checked["blockers"]))
    output = {"plan_key": fingerprint(plan), "source_report_id": report["id"],
              "method": method, "start": start, "end": end, "results": {},
              "protocol": "同一冻结行情与同一模型参数；分别模拟每月、每周、每日检查调仓。成本加倍仅改变执行费用，不重新拟合模型。"}
    for index, cadence in enumerate(("monthly", "weekly", "daily")):
        candidate = plan.model_copy(update={"rebalance": cadence})
        stressed = candidate.model_copy(update={"commission_rate": plan.commission_rate * 2,
                                                "min_commission": plan.min_commission * 2,
                                                "slippage_bps": plan.slippage_bps * 2})
        output["results"][cadence] = {}
        for stressed_cost, run_plan in ((False, candidate), (True, stressed)):
            label = "成本加倍" if stressed_cost else "原成本"
            progress(10 + 14 * (index * 2 + int(stressed_cost)), f"{METHODS[method]} · {cadence} · {label}")
            result = simulate(store, run_plan, histories, start, end, method,
                              forecast_plan=candidate if stressed_cost else None)
            output["results"][cadence]["stress" if stressed_cost else "base"] = {
                key: result[key] for key in ("return_pct", "annualized_return", "max_drawdown",
                                               "mean_exposure", "trade_count", "fees", "per_year")
            }
            if not stressed_cost:
                output["results"][cadence]["base"]["curve"] = result["curve"]
            output["results"][cadence]["stress" if stressed_cost else "base"]["decision_count"] = len(result["decisions"])
            output["results"][cadence]["stress" if stressed_cost else "base"]["model_months"] = sum(
                row["status"] == "model" for row in result["training"])
    return output


def normalize_forward_curve(state):
    start = str(state.get('started_at',''))[:10]
    if not start:
        return False
    rows = {}
    for row in state.get('curve',[]):
        date = max(start,row['date'])
        rows[date] = {**row,'date':date, **({'quote_date':row['date']} if row['date']<start else {})}
    updated = [rows[date] for date in sorted(rows)]
    changed = updated != state.get('curve',[])
    state['curve'] = updated
    return changed


def forward_progress(state, plan):
    if not state or state.get("plan_key") != fingerprint(plan):
        return {"status": "not_started", "sessions": 0}
    baseline = state.get("forward_baseline")
    if not baseline or baseline.get("plan_key") != fingerprint(plan):
        return {"status": "not_started", "sessions": 0}
    anchor = baseline["anchor_date"]
    starting = baseline["anchor_equity"]
    shadow = baseline["state"]
    points = [row for row in state["curve"] if row["date"] > anchor]
    shadow_points = [row for row in shadow["curve"] if row["date"] > anchor]
    values = np.array([starting] + [row["equity"] for row in points])
    return {"status": "collecting", "anchor_date": anchor, "last_date": state["last_date"],
            "sessions": len(shadow_points), "observed_updates": len(points),
            "strategy_return": float(values[-1] / starting - 1),
            "hold_return": float(shadow["account"]["equity"] / starting - 1),
            "max_drawdown": float((values / np.maximum.accumulate(values) - 1).min()),
            "same_plan": True}


def update_forward_baseline(engine, store, plan, histories, now, latest, reset):
    baseline = engine.state.get("forward_baseline")
    if reset or not baseline or baseline.get("plan_key") != fingerprint(plan):
        anchor_equity = engine.equity()
        shadow_plan = plan.model_copy(update={"starting_cash": anchor_equity})
        shadow = Portfolio(store, shadow_plan, histories, method="hold")
        shadow.step(latest, execute=False)
        shadow.state["pending"]["execute_after"] = str(now.date())
        engine.state["forward_baseline"] = {
            "plan_key": fingerprint(plan), "anchor_date": str(latest.date()),
            "anchor_equity": anchor_equity, "state": shadow.state}
        return
    shadow_plan = plan.model_copy(update={"starting_cash": baseline["anchor_equity"]})
    shadow = Portfolio(store, shadow_plan, histories, method="hold", restored=baseline["state"])
    advance_daily(shadow, latest, now)
    baseline["state"] = shadow.state


def update_portfolio(store, plan, progress, histories=None, now=None):
    if plan.research_interval != "daily":
        from .hourly_paper import update_hourly_portfolio
        return update_hourly_portfolio(store, plan, progress, rows=histories, now=now)
    now = now or dt.datetime.now(TZ)
    end = now.date() if now.hour >= 16 else now.date() - dt.timedelta(days=1)
    if histories is None:
        histories, _ = load_histories(store, plan, end - dt.timedelta(days=30), end, progress)
    histories, checked, latest = checked_daily(histories, plan, now)
    restored = store.meta("portfolio_state")
    if restored and restored.get("mode") in {"30m", "60m"}:
        raise ValueError("现有模拟账户是盘中模式，请先归档，再启用日线模拟")
    if restored:
        normalize_forward_curve(restored)
    if restored and restored.get('pending') and 'execute_after' not in restored['pending']:
        restored['pending']['execute_after'] = str(now.date())
        restored['pending']['generated_at'] = now.isoformat()
    if restored and restored.get("plan_key") != fingerprint(plan):
        restored["pending"] = None
        restored["period"] = None
    engine = Portfolio(store, plan, histories, restored=restored, progress=progress)
    changed = bool(restored and restored.get("plan_key") != fingerprint(plan))
    already_processed = bool(restored and restored.get("last_date") == str(latest.date()) and not changed)
    if already_processed:
        note = "该交易日已处理，没有重复成交"
    elif not restored or changed:
        new_dates = advance_daily(engine, latest, now)
        if changed and not new_dates:
            # Replan at the existing close without repeating a session's accounting.
            engine.decide(latest, engine.equity())
            engine.state["plan_key"] = fingerprint(plan)
        engine.state.setdefault("started_at", now.isoformat())
        note = "已生成下一交易日计划；首次观察不回填历史成交" if not restored else "新计划从下一交易日生效"
    else:
        before = len(engine.account.trades)
        new_dates = advance_daily(engine, latest, now)
        note = f"已处理新增行情；本次模拟成交 {len(engine.account.trades)-before} 笔"
        if new_dates > 1:
            note += "；离线期间仅回放已保存订单，没有补造中间决策"
    pending = engine.state.get('pending')
    if pending and 'generated_at' not in pending:
        pending['generated_at'] = now.isoformat()
        pending['execute_after'] = str(now.date())
    if engine.state.get('decisions'):
        engine.account.last_signal_date = engine.state['decisions'][-1]['date']
        engine.state['account']['last_signal_date'] = engine.account.last_signal_date
    if not already_processed:
        update_forward_baseline(engine, store, plan, histories, now, latest, reset=not restored or changed)
    normalize_forward_curve(engine.state)
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT value FROM meta WHERE key='investment_settings'").fetchone()
        current_plan = Plan.model_validate(json.loads(row[0])["plan"]) if row else Plan()
        if fingerprint(current_plan) != fingerprint(plan):
            raise ValueError("投资计划已更改，本次计算未写入账户，请按新计划重新更新")
        c.execute("INSERT INTO meta VALUES('portfolio_state',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (dumps(engine.state),))
    return {"state": engine.state, "audit": checked, "note": note}


class Review(BaseModel):
    summary: str = Field(min_length=1, max_length=1200)
    observations: list[str] = Field(min_length=1, max_length=5)
    risks: list[str] = Field(min_length=1, max_length=5)
    watch_next: list[str] = Field(min_length=1, max_length=5)


def review_rules(plan):
    from .profile_library import signal_name

    holding = plan.method == 'hold'
    core = plan.allocation_policy != 'legacy'
    momentum = '持有方案不使用择时信号'
    if not holding:
        momentum = ('20、60、120、252个交易日四个周期，至少两个上涨'
                    if plan.trend_signal == 'multi_horizon'
                    else '60和120个交易日收益均为正、价格高于120日均线')
        if plan.trend_signal == 'multi_horizon' and plan.signal_policy in {'consensus', 'fast_confirmed'}:
            momentum = '20、60、120、252个交易日四个周期，至少三个上涨'
            if plan.signal_policy == 'fast_confirmed':
                momentum += '，同时最近20日上涨且价格高于60日均线'
        if not core and plan.method == 'ml' and plan.model_recipe != 'legacy' and plan.signal_policy == 'confirmed':
            momentum += '；模型生效时额外要求至少三个周期上涨'
    return {
        'signal': signal_name(plan), 'momentum': momentum,
        'model': ('仅模型时间验证通过时使用预测，失败则回退趋势；实际是否使用模型以decision.model和status为准'
                  if plan.method not in {'hold', 'rotation'} else '不使用机器学习预测'),
        'ranking': ('持有方案按已选板块买入后持有' if holding else
                    '趋势筛选，同类资产最多1只；核心组合的模型预测只调整配置，不单独否决核心持仓' if core else
                    '趋势或通过验证的模型排序，须同时符合趋势条件；同类资产最多1只'),
        'weights': ('按板块偏好和投入上限持有，不使用动态择时预算' if holding else
                    '核心分散配置、模型预测、交易成本、波动及回撤预算共同优化；现金收益按零计，国债仍有风险' if core else
                    '板块偏好、波动和趋势强度分配，并应用所选执行规则与风险预算'),
        'allocation_policy': plan.allocation_policy, 'execution_policy': plan.execution_policy,
        'exposure_policy': plan.exposure_policy, 'rebalance': plan.rebalance, 'risk_review': plan.risk_review,
        'execution': '收盘信号，之后的交易日开盘模拟成交；前向记录不能早于计划实际保存时间',
        'pause': ('持有方案不使用自动回撤暂停' if holding else
                  f'资产较阶段高点回撤达到{plan.drawdown_pause:.1%}时计划退出，冷静期{plan.cooldown_sessions}个交易日；不是最大亏损保证'),
    }


def review_portfolio(store, plan, progress):
    if plan.research_interval != "daily":
        raise ValueError("AI 解读尚未适配盘中账户；请查看模拟记录与盘中验证")
    from ..config import load_settings
    from ..v2.ai_transport import codex_json
    saved = store.meta("portfolio_state")
    if not saved or saved.get("plan_key") != fingerprint(plan):
        raise ValueError("请先更新当前计划的模拟观察")
    evidence = current_report_for(store, plan)
    context = {"plan": plan.model_dump(), "date": saved["last_date"], "account": {k: v for k, v in saved["account"].items() if k != "trades"},
               "decision": saved["decisions"][-1] if saved["decisions"] else None,
               "cooldown_remaining": saved.get('cooldown',0),
               "rules": review_rules(plan),
               "validation": {"checks": evidence["checks"], "verdict": evidence["verdict"], "start":evidence['start'],"end":evidence['end'],"results":{k:{a:v[a] for a in ['return_pct','max_drawdown','trade_count','per_year']} for k,v in evidence['results'].items()}} if evidence else None}
    prompt = "\n".join(["你是个人ETF投资研究助手，请用中文解释给不懂股票的用户。以下是模拟账户的事实。",
                        "分析当前持仓/现金、目标仓位、模型验证和风险；把未知写成未知。没有当前新闻，不得靠记忆补新闻。不要给出保证收益或新的交易指令。不要调用工具或读取文件。",
                        "说明日期，区分预测收益和已经实现的收益。只返回以下 JSON Schema 对应的 JSON。",
                        "summary不超过180字，其他列表各最多3条，每条不超过80字。朴素、简洁；没有发生的成交不要当成已经发生。",
                        json.dumps(context, ensure_ascii=False), json.dumps(Review.model_json_schema(), ensure_ascii=False)])
    config = load_settings()
    progress(15, "AI 正在阅读投资计划和验证证据")
    if config.ai_provider == "openai":
        from openai import OpenAI
        response = OpenAI(api_key=config.openai_api_key, timeout=120, max_retries=0).responses.create(model=config.openai_model, input=prompt)
        payload = json.loads(response.output_text)
        provider, model = "openai", response.model
    else:
        payload, _ = codex_json(config, prompt, web=False)
        provider, model = "codex", config.codex_model or "provider_default"
    value = {**Review.model_validate(payload).model_dump(), "as_of": saved["last_date"], "created_at": time.time(),
             "provider": provider, "model": model, "model_scope": "returned" if provider=='openai' else "requested", "plan_key": fingerprint(plan), "input": context}
    store.set_meta("portfolio_review", value)
    return value
