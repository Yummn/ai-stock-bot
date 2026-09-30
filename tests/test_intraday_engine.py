import copy
import datetime as dt

import pytest

from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.hourly_paper import update_hourly_portfolio
from ai_stock_bot.v4.intraday import _replay, chronological_validation
from ai_stock_bot.v4.intraday_engine import IntradayEngine, lookback, signal
from ai_stock_bot.v4.schedule import enqueue_daily
from ai_stock_bot.v4.schema import Plan
from ai_stock_bot.v4.service import save_settings


def bars(days=40, half=False):
    rows, price = [], 5.
    clocks = (["10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"]
              if half else ["10:30", "11:30", "14:00", "15:00"])
    day = dt.date(2025, 1, 2)
    for _ in range(days):
        while day.weekday() > 4:
            day += dt.timedelta(days=1)
        for clock in clocks:
            opening = price
            price *= 1.0014 if len(rows) % 60 < 40 else .9985
            rows.append({"stamp": f"{day} {clock}:00", "open": opening,
                         "high": max(opening, price) * 1.0005, "low": min(opening, price) * .9995,
                         "close": price, "volume": 10_000_000., "source": "fixture"})
        day += dt.timedelta(days=1)
    return rows


def test_replay_and_forward_share_fills_with_delayed_signals(tmp_path):
    plan = Plan(research_interval="30m", research_multiple=3, intraday_risk_budget=.03,
                max_weight=.6, max_exposure=.8, profile="custom")
    rows = bars(12, half=True)
    store = Store(tmp_path)
    save_settings(store, plan, 1)
    begin = lookback(plan)
    for index in range(begin - 1, len(rows)):
        state = update_hourly_portfolio(store, plan, lambda *args: None, rows=rows[:index+1],
                now=dt.datetime.fromisoformat(rows[index]["stamp"]) + dt.timedelta(minutes=10))["state"]
    replay = _replay(rows, plan, 3, plan.research_annual_rate)
    assert state["account"]["trades"]
    assert state["account"]["trades"] == replay["trades"]
    assert state["account"]["equity"] == pytest.approx(replay["end_equity"])
    assert state["interest_paid"] == pytest.approx(replay["interest"])
    assert any(point["cash"] < 0 for point in state["curve"])
    for trade in replay["trades"]:
        opened = dt.datetime.fromisoformat(trade["stamp"]) - dt.timedelta(minutes=30)
        assert opened >= dt.datetime.fromisoformat(trade["signal_date"]) + dt.timedelta(minutes=10)


def test_later_prices_do_not_change_earlier_signals_or_candidate_selection():
    plan = Plan(research_interval="60m")
    rows = bars()
    first = chronological_validation(rows, plan)
    revised = copy.deepcopy(rows)
    for row in revised:
        if row["stamp"][:10] >= first["split_date"]:
            for key in ("open", "high", "low", "close"):
                row[key] *= .8
    second = chronological_validation(revised, plan)
    assert first["selected"] == second["selected"]
    assert first["candidates"] == second["candidates"]
    assert first["evaluation"]["return_pct"] != second["evaluation"]["return_pct"]


def test_volatility_cap_never_increases_target_with_multiple():
    rows = bars(half=True)
    for row in rows:
        row["high"] *= 1.05
        row["low"] *= .95
    plan = Plan(research_interval="30m", research_multiple=3)
    decision = signal(rows[:32], plan)
    assert decision["gross_target"] < plan.max_weight * 3
    assert decision["gross_target"] * decision["daily_move"] <= plan.intraday_risk_budget + 1e-9


def test_weekend_financing_counted_without_new_signals():
    plan = Plan(research_interval="60m", research_multiple=3, research_annual_rate=.10)
    engine = IntradayEngine(plan)
    engine.account.cash = -10000
    engine.state["interest_date"] = "2025-01-03"
    engine.accrue(dt.date(2025, 1, 6))
    assert engine.state["interest_paid"] == pytest.approx(10000 * ((1 + .1 / 365)**3 - 1))
    assert engine.account.trades == []


def test_halt_volume_and_price_limit_reject_orders():
    plan = Plan(research_interval="30m", intraday_strategy="legacy")
    rows = bars(5, half=True)
    engine = IntradayEngine(plan)
    pending = {"weight": .35, "stamp": rows[-1]["stamp"], "reason": "fixture"}
    for row in ({**rows[-1], "volume": 0}, {**rows[-1], "stamp": "2025-01-15 10:00:00",
                                           "open": rows[-1]["close"] * 1.11}):
        engine.execute(row, rows, pending, False)
    assert not engine.account.trades
    assert len(engine.state["rejections"]) == 2


def test_drawdown_pause_overrides_buy_and_survives_restore():
    plan = Plan(research_interval="60m", research_multiple=3)
    engine = IntradayEngine(plan)
    engine.account.cash = 80000
    rows = bars(5)
    engine.state["pending"] = {"weight": .35, "stamp": rows[-1]["stamp"],
                               "generated_at": "2026-01-01T00:00:00", "reason": "old buy"}
    engine.step(rows[-1], rows[:-1])
    assert engine.state["cooldown_until"]
    assert engine.state["pending"]["weight"] == 0
    assert IntradayEngine(plan, engine.dump()).state["cooldown_until"]


def test_halfhour_schedule_and_old_granularity_validation(tmp_path):
    plan = Plan(auto_update=True, research_interval="30m")
    store = Store(tmp_path)
    save_settings(store, plan, 1)
    enqueue_daily(store, dt.datetime(2025, 1, 7, 10, 5))
    assert store.jobs()["total"] == 0
    enqueue_daily(store, dt.datetime(2025, 1, 7, 10, 10))
    enqueue_daily(store, dt.datetime(2025, 1, 7, 10, 11))
    assert store.jobs()["total"] == 1
    with pytest.raises(ValueError):
        Plan(research_interval="1m")


def test_bankruptcy_metrics_remain_finite_and_margin_latches():
    plan = Plan(research_interval="60m", research_multiple=3, intraday_strategy="legacy",
                profile="custom", max_exposure=1, max_weight=1)
    rows = bars(6)
    for i, row in enumerate(rows):
        if i >= 16:
            for key in ("open", "high", "low", "close"):
                row[key] *= .2
    replay = _replay(rows, plan, 3, .08)
    assert replay["margin_exit_at"]
    assert replay["shortfall"] >= 0
    assert not any(t["side"] == "buy" and t["stamp"] >= replay["margin_exit_at"] for t in replay["trades"])
