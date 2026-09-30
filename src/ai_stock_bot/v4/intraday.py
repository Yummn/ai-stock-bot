from __future__ import annotations

import hashlib
import json
import math
from statistics import mean
from datetime import datetime, timedelta

from .data import execution_interval, intraday_table
from .intraday_engine import IntradayEngine, lookback
from .intraday_quality import quality
from .schema import ASSETS, ENGINE_VERSION, Plan, fingerprint


MIN_SESSIONS = 252
MIN_BARS = 800
LOOKBACK = 8


def _validate(symbol, rows):
    if symbol not in ASSETS or not ASSETS[symbol]["t0"]:
        raise ValueError("小时级当日回转实验只接受已标记 T+0 的 ETF")
    previous = None
    sources = set()
    for row in rows:
        stamp = datetime.fromisoformat(row["stamp"])
        values = [float(row[key]) for key in ("open", "high", "low", "close", "volume")]
        opening, high, low, close, volume = values
        if (previous is not None and stamp <= previous) or not all(math.isfinite(v) for v in values):
            raise ValueError("小时行情时间重复、倒序或包含无效数字")
        if min(opening, low, close) <= 0 or volume < 0 or high < max(opening, close, low) or low > min(opening, close, high):
            raise ValueError("小时行情价格或成交量不一致")
        previous = stamp
        sources.add(row["source"])
    if len(sources) > 1:
        raise ValueError("小时行情混用了不同来源，不能拼接回放")
    if len(rows) > 1 and any(abs(float(current["open"]) / float(prior["close"]) - 1) > .25
                             for prior, current in zip(rows, rows[1:])):
        raise ValueError("小时行情出现超过 25% 的相邻缺口，请核对折算或数据口径")
    return next(iter(sources), None)


def _target(closes):
    recent = closes[-LOOKBACK:]
    short = sum(recent[-3:]) / 3
    long = sum(recent) / LOOKBACK
    momentum = recent[-1] / recent[-5] - 1
    return short > long * 1.0015 and momentum > .002


def _replay(rows, plan, multiple, borrow_rate, stress=False, hold=False, start_index=None, forecast=None):
    actual = plan.model_copy(update={"research_multiple": multiple, "research_annual_rate": borrow_rate})
    engine = IntradayEngine(actual, stress=stress)
    if forecast is not None:
        engine.forecast = forecast
    first = lookback(actual) if start_index is None else max(lookback(actual), start_index)
    if first >= len(rows):
        raise ValueError("盘中测试区间不足，不能只用预热数据生成结果")
    decision = ({"weight": min(plan.max_exposure, plan.max_weight), "reason": "同预算持有"} if hold else
                engine.decision(rows[:first]))
    engine.state["pending"] = {**decision, "stamp": rows[first-1]["stamp"],
                                "hold": hold, "generated_at": (datetime.fromisoformat(rows[first-1]["stamp"])
                                                                + timedelta(minutes=10)).isoformat()}
    for index in range(first, len(rows)):
        engine.step(rows[index], rows[:index], hold=hold,
                    issued_at=(datetime.fromisoformat(rows[index]["stamp"]) + timedelta(minutes=10)).isoformat())
    state, account = engine.state, engine.account
    curve, trades = state["curve"], account.trades
    final = account.equity()
    peak, max_drawdown = plan.starting_cash, 0.
    for point in curve:
        peak = max(peak, point["equity"])
        max_drawdown = min(max_drawdown, point["equity"] / peak - 1)
    quantity = account.positions.get(plan.research_symbol, {}).get("quantity", 0)
    estimated_exit = (max(plan.min_commission, quantity * float(rows[-1]["close"]) * plan.commission_rate)
                      + quantity * float(rows[-1]["close"]) * plan.slippage_bps / 10000) * (2 if stress else 1) if quantity else 0.
    return {"multiple": multiple, "return_pct": final / plan.starting_cash - 1,
            "end_equity": final, "max_drawdown": max_drawdown,
            "trade_count": len(trades), "fees": account.fees, "interest": state["interest_paid"],
            "slippage": state["slippage_paid"], "turnover": state["turnover"],
            "cost_pct": (account.fees + state["interest_paid"] + state["slippage_paid"]) / plan.starting_cash,
            "liquidation_return": (final - estimated_exit) / plan.starting_cash - 1,
            "estimated_exit_cost": estimated_exit,
            "mean_exposure": sum(p["exposure"] for p in curve) / len(curve),
            "margin_exit_at": state["margin_exit_at"], "shortfall": max(0., -final),
            "open_quantity": quantity, "curve": curve, "trades": trades,
            "rejections": state["rejections"],
            "model_checks": [d["model"] for d in state["decisions"] if d.get("model")]}


def research(store, plan: Plan, symbol: str, borrow_rate: float = .08, progress=lambda *args: None, forecast=None):
    if not math.isfinite(borrow_rate) or not 0 <= borrow_rate <= .30:
        raise ValueError("假设融资年利率应为 0% 至 30%")
    intraday_table(store)
    with store.connect() as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT stamp,open,high,low,close,volume,source FROM intraday_bars "
            "WHERE symbol=? AND interval=? ORDER BY stamp", (symbol, execution_interval(plan.research_interval)))]
    plan = plan.model_copy(update={"research_symbol": symbol, "research_annual_rate": borrow_rate})
    source = _validate(symbol, rows)
    sessions = len({row["stamp"][:10] for row in rows})
    coverage = {"bars": len(rows), "sessions": sessions, "quality": quality(rows, plan.research_interval),
                "first": rows[0]["stamp"] if rows else None,
                "last": rows[-1]["stamp"] if rows else None,
                "source": source,
                "snapshot": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16] if rows else None}
    if len(rows) < lookback(plan) + 2:
        return {"symbol": symbol, "coverage": coverage, "status": "no_data", "plan_key": fingerprint(plan), "engine": ENGINE_VERSION,
                "reason": f"至少需要 {lookback(plan) + 2} 根有效 K 线才能演示回放", "scenarios": []}
    ready = sessions >= MIN_SESSIONS and len(rows) >= MIN_BARS
    from .intraday_forecast import ForecastTape
    if forecast is None:
        forecast = ForecastTape(rows, plan, progress)
    baseline = _replay(rows, plan, 1, 0, hold=True)
    scenarios = []
    for multiple in (1, 2, 3):
        progress(35 + multiple * 5, f"验证 {multiple}x 盘中成本与风险")
        base = _replay(rows, plan, multiple, borrow_rate, forecast=forecast)
        stressed = _replay(rows, plan, multiple, borrow_rate, stress=True, forecast=forecast)
        scenarios.append({"multiple": multiple, "base": base, "stress": stressed})
    return {"symbol": symbol, "name": ASSETS[symbol]["name"], "coverage": coverage,
            "plan_key": fingerprint(plan), "engine": ENGINE_VERSION,
            "status": "exploratory" if not ready else "research_sample",
            "reason": (f"仅 {sessions} 个交易日 / {len(rows)} 根小时线；至少需要 {MIN_SESSIONS} 日和 {MIN_BARS} 根才可开始跨阶段检验"
                       if not ready else "数据长度达到初步研究门槛，但仍需独立年份、前向模拟及真实成交验证"),
            "benchmark": {key: baseline[key] for key in ("return_pct", "max_drawdown", "trade_count", "fees")},
            "scenarios": scenarios,
            "validation": chronological_validation(rows, plan, forecast),
            "walk_forward": walk_forward(rows, plan, forecast, progress),
            "assumptions": {"signal": "已完成 K 线后延迟 10 分钟产生指令；在之后尚未开始的首根 K 线开盘执行，跳过已发生的开盘价",
                            "decision_delay_minutes": 10,
                            "strategy": plan.intraday_strategy, "interval": plan.research_interval,
                            "interest_basis": "假设按日复计，年利率 / 365；含周末",
                            "base_target_weight": min(plan.max_exposure, plan.max_weight),
                            "lot": 100, "participation": plan.participation,
                            "commission_rate": plan.commission_rate,
                            "min_commission": plan.min_commission,
                            "slippage_bps": plan.slippage_bps,
                            "borrow_rate": borrow_rate,
                            "maintenance_ratio": plan.research_maintenance_ratio,
                            "live_orders": False}}


def chronological_validation(rows, plan, forecast=None):
    """Small fixed search, selected without reading the later evaluation slice."""
    days = sorted({row["stamp"][:10] for row in rows})
    if len(days) < 20:
        return {"status": "insufficient", "reason": "不足 20 个交易日，不能再切分筛选段和后段验证", "candidates": []}
    boundary = days[int(len(days) * .65)]
    split = next(i for i, row in enumerate(rows) if row["stamp"][:10] >= boundary)
    specs = [("legacy", "原始均线", {"intraday_strategy": "legacy"}),
             ("filtered", "成本过滤", {"intraday_strategy": "cost_aware", "intraday_cost_buffer": 1.5}),
             ("strict", "严格成本过滤", {"intraday_strategy": "cost_aware", "intraday_cost_buffer": 2.5})]
    candidates = []
    for key, label, changes in specs:
        candidate = plan.model_copy(update=changes)
        development = _replay(rows[:split], candidate, plan.research_multiple, plan.research_annual_rate)
        stressed = _replay(rows[:split], candidate, plan.research_multiple, plan.research_annual_rate, stress=True)
        score = stressed["liquidation_return"] + .5 * development["max_drawdown"]
        candidates.append({"key": key, "name": label, "changes": changes, "score": score,
                           "development": metrics(development), "development_stress": metrics(stressed)})
    winner = max(candidates, key=lambda item: item["score"])
    candidate = plan.model_copy(update=winner["changes"])
    evaluation = _replay(rows, candidate, plan.research_multiple, plan.research_annual_rate, start_index=split)
    stressed = _replay(rows, candidate, plan.research_multiple, plan.research_annual_rate, stress=True, start_index=split)
    baseline = _replay(rows, plan, plan.research_multiple, plan.research_annual_rate, hold=True, start_index=split)
    legacy = _replay(rows, plan.model_copy(update={"intraday_strategy": "legacy"}), plan.research_multiple,
                     plan.research_annual_rate, start_index=split)
    current = _replay(rows, plan, plan.research_multiple, plan.research_annual_rate, start_index=split, forecast=forecast)
    checks = [{"name": "后段成本加倍仍盈利", "ok": stressed["liquidation_return"] > 0},
              {"name": "后段跑赢同预算持有", "ok": evaluation["liquidation_return"] > baseline["liquidation_return"]},
              {"name": "后段至少十笔成交", "ok": evaluation["trade_count"] >= 10},
              {"name": "后段回撤不超计划上限", "ok": evaluation["max_drawdown"] >= -plan.drawdown_pause},
              {"name": "无维保触发", "ok": evaluation["margin_exit_at"] is None},
              {"name": "至少一年盘中样本", "ok": len(days) >= MIN_SESSIONS}]
    return {"status": "passed_research" if all(c["ok"] for c in checks) else "not_qualified",
            "reason": "仅前段筛选，后段独立起始资金；本次未用后段选参，但历史已多次查看，不是全新样本外证据。未自动应用参数。",
            "split_date": boundary, "development_sessions": int(len(days) * .65),
            "evaluation_sessions": len(days) - int(len(days) * .65),
            "selected": winner["key"], "selected_name": winner["name"], "candidates": candidates,
            "evaluation": metrics(evaluation), "stress": metrics(stressed), "hold": metrics(baseline),
            "legacy": metrics(legacy), "current": metrics(current), "checks": checks,
            "applied": False}


def metrics(result):
    return {key: value for key, value in result.items() if key not in {"curve", "trades", "rejections", "model_checks"}}


def walk_forward(rows, plan, forecast=None, progress=lambda *args: None):
    """Fixed algorithms and forward slices, with no winner fitted to their scores."""
    days = sorted({r["stamp"][:10] for r in rows})
    if len(days) < 40:
        return {"status": "insufficient", "reason": "至少需 40 个交易日；前 30 日预热，之后每 10 日一段", "strategies": []}
    specs = [("legacy", "原始均线"), ("cost_aware", "成本过滤"),
             ("breakout", "通道突破"), ("ml_edge", "盘中回归")]
    if forecast is None:
        from .intraday_forecast import ForecastTape
        forecast = ForecastTape(rows, plan, progress)
    folds = []
    for offset in range(30, len(days) - 9, 10):
        start, end = days[offset], days[offset + 9]
        sample = [r for r in rows if r["stamp"][:10] <= end]
        begin = next(i for i, r in enumerate(sample) if r["stamp"][:10] >= start)
        baseline = _replay(sample, plan, 1, 0, hold=True, start_index=begin)
        folds.append((sample, begin, start, end, metrics(baseline)))
    results = []
    for index, (strategy, name) in enumerate(specs):
        candidate = plan.model_copy(update={"intraday_strategy": strategy})
        periods = []
        for sample, begin, start, end, baseline in folds:
            progress(55 + index * 10, f"验证 {name}：{start} 至 {end}")
            base = _replay(sample, candidate, plan.research_multiple, plan.research_annual_rate,
                           start_index=begin, forecast=forecast)
            stress = _replay(sample, candidate, plan.research_multiple, plan.research_annual_rate,
                             stress=True, start_index=begin, forecast=forecast)
            checks = base["model_checks"]
            periods.append({"start": start, "end": end, "base": metrics(base), "stress": metrics(stress),
                            "hold": baseline, "model_decisions": len(checks),
                            "model_ready": sum(c["status"] == "model" for c in checks)})
        gates = [
            {"name": "各段成本加倍后均盈利", "ok": all(p["stress"]["liquidation_return"] > 0 for p in periods)},
            {"name": "各段跑赢不融资同预算持有", "ok": all(p["base"]["liquidation_return"] > p["hold"]["liquidation_return"] for p in periods)},
            {"name": "每段至少十笔成交", "ok": all(p["base"]["trade_count"] >= 10 for p in periods)},
            {"name": "回撤受控且无维保触发", "ok": all(p["base"]["max_drawdown"] >= -plan.drawdown_pause and not p["base"]["margin_exit_at"] for p in periods)},
            {"name": "盘中数据覆盖至少 98%", "ok": quality(rows, plan.research_interval)["coverage_ratio"] >= .98},
            {"name": "至少一年且前向模拟另行验证", "ok": False},
        ]
        results.append({"strategy": strategy, "name": name, "periods": periods, "checks": gates,
                        "status": "not_qualified", "positive_stress_periods": sum(p["stress"]["liquidation_return"] > 0 for p in periods),
                        "mean_return": mean(p["base"]["liquidation_return"] for p in periods),
                        "mean_stress_return": mean(p["stress"]["liquidation_return"] for p in periods)})
    return {"status": "not_qualified", "multiple": plan.research_multiple, "warmup_sessions": 30,
            "period_sessions": 10, "strategies": results, "applied": False,
            "reason": "各段独立本金、同日开始；模型仅训练当天之前已结束标签，保留末段隔离校验，误差不优于零预测时停用。固定方案未按后段成绩选参，但这些历史已被观察并用于迭代，不是全新盲测。模型分歧不是统计置信区间。"}
