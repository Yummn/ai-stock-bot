import numpy as np
import pandas as pd
import pytest

from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.portfolio import Portfolio
from ai_stock_bot.v4.schema import Plan, fingerprint


@pytest.fixture
def engine(tmp_path):
    dates = pd.bdate_range("2023-01-02", periods=400)
    histories = {}
    for symbol in ("510300.SH", "518880.SH"):
        close = 10*np.exp(np.linspace(0, .5, len(dates)))
        histories[symbol] = pd.DataFrame({"open": close, "close": close, "adjusted": close,
            "high": close*1.01, "low": close*.99, "volume": 1e8}, index=dates)
    plan = Plan(symbols=list(histories), risk_review="daily", execution_policy="corrected",
        exposure_policy="volatility_target", target_volatility=.3, drift_band=.01)
    return Portfolio(Store(tmp_path), plan, histories)


def hold(engine, symbol, quantity=1000):
    engine.account.trade(symbol, "buy", 10., "2023-01-02", "fixture", "rotation", quantity=quantity)
    engine.account.new_day("2024-01-02")


def test_disabled_review_keeps_old_behavior(engine):
    engine.plan = engine.plan.model_copy(update={"risk_review": "rebalance"})
    hold(engine, "510300.SH")
    assert engine.review_risk(pd.Timestamp("2024-04-01")) is None
    assert "risk_review_period" not in engine.state


def test_trend_exit_and_weekly_dedup(engine):
    engine.plan = engine.plan.model_copy(update={"risk_review": "weekly"})
    hold(engine, "510300.SH")
    engine.trends["510300.SH"].loc[:, "strength"] = .25
    pending = engine.review_risk(pd.Timestamp("2024-04-01"))
    assert pending["weights"] == {}
    assert pending["reduce_only"]
    assert pending["removed"] == ["510300.SH"]
    assert engine.review_risk(pd.Timestamp("2024-04-02")) is None
    assert engine.review_risk(pd.Timestamp("2024-04-08")) is not None


def test_reduce_only_never_buys_even_if_prices_or_equity_change(engine):
    hold(engine, "510300.SH")
    before = engine.account.positions["510300.SH"]["quantity"]
    count = len(engine.account.trades)
    engine.execute({"date": "2024-04-01", "weights": {"510300.SH": .5, "518880.SH": .4},
                    "reduce_only": True, "reason": "risk review"}, pd.Timestamp("2024-04-02"))
    assert all(t["side"] == "sell" for t in engine.account.trades[count:])
    assert "518880.SH" not in engine.account.positions
    assert engine.account.positions["510300.SH"]["quantity"] <= before


def test_guard_only_uses_past_and_does_not_train(engine, monkeypatch):
    hold(engine, "510300.SH")
    date = pd.Timestamp("2024-04-01")
    engine.trends["510300.SH"].loc[:date, "strength"] = .25
    monkeypatch.setattr(engine, "make_plan", lambda *a: pytest.fail("Risk review must not fit the model"))
    a = engine.review_risk(date)
    engine.state.pop("risk_review_period")
    engine.trends["510300.SH"].loc[engine.trends["510300.SH"].index > date, "strength"] = 1.
    engine.returns.loc[engine.returns.index > date] = 100.
    b = engine.review_risk(date)
    assert a == b


def test_unobserved_dates_do_not_generate_guard_orders(engine, monkeypatch):
    hold(engine, "510300.SH")
    monkeypatch.setattr(engine, "review_risk", lambda *a: pytest.fail("Offline gap cannot create new orders"))
    engine.step(pd.Timestamp("2024-04-01"), execute=False, make_decision=False)
    assert engine.state["pending"] is None


def test_hypothesis_changes_identity():
    base = Plan()
    assert fingerprint(base) != fingerprint(base.model_copy(update={"risk_review": "daily"}))
    assert fingerprint(base) != fingerprint(base.model_copy(update={"signal_policy": "trend_weighted"}))
