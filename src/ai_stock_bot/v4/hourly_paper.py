from __future__ import annotations

import datetime as dt
import json

from ..v2.market import TZ
from ..v2.store import dumps
from .data import execution_interval, intraday_table, refresh_intraday
from .intraday import _validate
from .intraday_engine import IntradayEngine, lookback
from .schema import Plan, fingerprint


def update_hourly_portfolio(store, plan: Plan, progress, rows=None, now=None):
    """Replay only issued orders across offline gaps; never invent past decisions."""
    now = now or dt.datetime.now(TZ)
    local_now = now.astimezone(TZ).replace(tzinfo=None) if now.tzinfo else now
    symbol = plan.research_symbol
    if rows is None:
        refresh_intraday(store, [symbol], progress, plan.research_interval)
        intraday_table(store)
        with store.connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT stamp,open,high,low,close,volume,source FROM intraday_bars "
                "WHERE symbol=? AND interval=? ORDER BY stamp", (symbol, execution_interval(plan.research_interval)))]
    rows = [row for row in rows if dt.datetime.fromisoformat(row["stamp"]) <= local_now]
    _validate(symbol, rows)
    if len(rows) < lookback(plan):
        raise ValueError(f"需要至少 {lookback(plan)} 根已完成的 {plan.research_interval} 行情")
    stamp = rows[-1]["stamp"]
    if (local_now.date() - dt.date.fromisoformat(stamp[:10])).days > 7:
        raise ValueError(f"盘中行情只更新至 {stamp}，暂停模拟")
    restored = store.meta("portfolio_state")
    if restored and restored.get("mode") != plan.research_interval:
        raise ValueError("模拟账户时间粒度不同，请先归档，再启用新粒度模拟")
    engine = IntradayEngine(plan, restored)
    state = engine.state
    state.setdefault("started_at", now.isoformat())
    if state.get("last_date") == stamp and state.get("plan_key") == fingerprint(plan):
        return {"state": state, "note": "这根 K 线已处理，没有重复成交"}
    if state.get("plan_key") != fingerprint(plan):
        state["pending"] = None
    before = len(engine.account.trades)
    indices = ([i for i, row in enumerate(rows) if row["stamp"] > state.get("last_date", "")]
               if restored else [len(rows) - 1])
    if not indices:
        # Reconfigured on the same candle: update the decision, not financing or fills.
        indices = [len(rows) - 1]
    for index in indices:
        engine.step(rows[index], rows[:index], make_decision=index == len(rows) - 1,
                    issued_at=local_now.isoformat())
    state["plan_key"] = fingerprint(plan)
    state = engine.dump()
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM meta WHERE key='investment_settings'").fetchone()
        current = Plan.model_validate(json.loads(row[0])["plan"]) if row else Plan()
        if fingerprint(current) != fingerprint(plan):
            raise ValueError("投资计划已更改，本次盘中计算未写入账户")
        connection.execute("INSERT INTO meta VALUES('portfolio_state',?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (dumps(state),))
    return {"state": state, "note": f"已处理 {stamp} 的 {plan.research_interval} 行情；新增模拟成交 {len(engine.account.trades) - before} 笔"}
