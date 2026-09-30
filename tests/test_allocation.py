import copy

import numpy as np
import pandas as pd
import pytest

from ai_stock_bot.v4.allocation import exposure_budget, trend_features
from ai_stock_bot.v4.portfolio import Portfolio, simulate
from ai_stock_bot.v4.schema import Plan, fingerprint
from ai_stock_bot.v2.store import Store


def fixture_history():
    dates = pd.bdate_range("2019-01-01", "2022-12-31")
    result = {}
    for i, symbol in enumerate(("510300.SH", "518880.SH")):
        close = 5 * np.exp(np.cumsum(np.random.default_rng(i).normal(.0007, .012, len(dates))))
        opening = np.r_[close[0], close[:-1]]
        result[symbol] = pd.DataFrame({"open": opening, "close": close, "adjusted": close,
                                      "high": np.maximum(opening, close) * 1.01,
                                      "low": np.minimum(opening, close) * .99,
                                      "volume": 1e8}, index=dates)
    return result


def test_features_do_not_change_when_future_changes():
    frame = fixture_history()["510300.SH"]
    cutoff = pd.Timestamp("2021-06-30")
    changed = frame.copy()
    changed.loc[changed.index > cutoff, "adjusted"] *= 10
    pd.testing.assert_frame_equal(trend_features(frame).loc[:cutoff], trend_features(changed).loc[:cutoff])
    assert trend_features(frame).iloc[:252].strength.isna().all()


def test_risk_budget_uses_only_past_and_never_exceeds_requested():
    dates = pd.bdate_range("2020-01-01", periods=100)
    r = pd.DataFrame(np.random.default_rng(3).normal(0, .03, (100, 2)), index=dates, columns=["a", "b"])
    plan = Plan(research_multiple=3, exposure_policy="volatility_target", target_volatility=.10)
    args = (plan, {"a": .35, "b": .35})
    weights, report = exposure_budget(*args, r, dates[65], 100, 100)
    r.loc[dates[66]:] *= 100
    assert exposure_budget(*args, r, dates[65], 100, 100) == (weights, report)
    assert 0 < report["scale"] < 1
    assert sum(weights.values()) < .7
    assert report["estimated_volatility"] * report["scale"] <= .10 + 1e-9
    assert not exposure_budget(*args, r.iloc[:20], dates[19], 100, 100)[0]


def test_calendar_interest_charged_before_repayment(tmp_path):
    engine = Portfolio(Store(tmp_path), Plan(research_annual_rate=.10), fixture_history())
    engine.account.cash = -10000
    engine.accrue_interest(pd.Timestamp("2022-09-30"))
    assert engine.state["interest_paid"] == 0
    engine.accrue_interest(pd.Timestamp("2022-10-10"))
    assert engine.state["interest_paid"] == pytest.approx(10000 * .10 * 10 / 365)
    engine.account.cash = 1000
    engine.accrue_interest(pd.Timestamp("2022-10-11"))
    assert engine.state["interest_paid"] == pytest.approx(10000 * .10 * 10 / 365)


def test_cooldown_budget_matches_zero_target(tmp_path):
    history = fixture_history()
    engine = Portfolio(Store(tmp_path), Plan(symbols=list(history), exposure_policy="volatility_target"), history)
    engine.state["cooldown"] = 3
    decision = engine.make_plan(pd.Timestamp("2021-06-30"))
    assert decision["weights"] == {}
    assert decision["risk_budget"]["target_gross"] == 0


def test_backtest_matches_restored_forward_replay(tmp_path):
    h = fixture_history()
    plan = Plan(symbols=list(h), trend_signal="multi_horizon", exposure_policy="volatility_target",
                research_multiple=3, rebalance="daily")
    store = Store(tmp_path)
    result = simulate(store, plan, h, "2021-01-01", "2021-12-31")
    engine = Portfolio(store, plan, h)
    engine.state["pending"] = engine.make_plan(pd.Timestamp("2020-12-31"))
    for date in h[plan.symbols[0]].loc["2021"].index:
        engine.step(date)
        engine = Portfolio(store, plan, h, restored=copy.deepcopy(engine.state))
    assert engine.state["curve"] == result["curve"]
    assert engine.account.trades == result["trades"]
    assert all(t["signal_date"] < t["date"] for t in result["trades"])
    assert fingerprint(plan) != fingerprint(plan.model_copy(update={"target_volatility": .18}))
