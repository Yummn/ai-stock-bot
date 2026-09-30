import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.cost_allocation import cost_allocation
from ai_stock_bot.v4.portfolio import Portfolio, simulate
from ai_stock_bot.v4.schema import ASSETS, ENGINE_VERSION, RESEARCH_FIELDS, Plan, fingerprint, comparison_fingerprint
from ai_stock_bot.v4.service import save_settings, settings
from test_allocation import fixture_history


def returns_fixture():
    dates = pd.bdate_range("2020-01-01", periods=300)
    return pd.DataFrame(np.random.default_rng(12).normal(.0015, .005, (300, 2)),
                        index=dates, columns=["518880.SH", "511260.SH"])


@pytest.mark.parametrize("comparison", [False, True])
def test_legacy_fingerprint_unchanged(comparison):
    plan = Plan()
    excluded = {"name", "auto_update", "execution_policy", "model_recipe", "preset_version", "signal_policy", "risk_review",
                "allocation_policy", "core_anchor", "risk_aversion", *RESEARCH_FIELDS}
    if comparison:
        excluded.add("method")
    old = hashlib.sha256(json.dumps({"engine": ENGINE_VERSION,
        **plan.model_dump(exclude=excluded)}, sort_keys=True).encode()).hexdigest()[:24]
    fn = comparison_fingerprint if comparison else fingerprint
    assert fn(plan) == old
    assert fn(plan.model_copy(update={"execution_policy": "corrected"})) != old
    assert fn(plan.model_copy(update={"execution_policy": "cost_aware"})) != old


@pytest.mark.parametrize("policy, count", [("legacy", 3), ("corrected", 6), ("cost_aware", 6)])
def test_initial_orders_not_suppressed_by_drift(tmp_path, policy, count):
    histories = {s: fixture_history()["510300.SH"].copy() for s in ASSETS}
    plan = Plan(max_exposure=.9, max_weight=.45, drift_band=.08, execution_policy=policy)
    engine = Portfolio(Store(tmp_path), plan, histories, method="hold")
    pending = engine.make_plan(pd.Timestamp("2020-01-01"))
    engine.execute(pending, pd.Timestamp("2020-01-01"))
    assert not engine.account.trades
    engine.execute(pending, pd.Timestamp("2020-01-02"))
    assert len(engine.account.trades) == count


def test_cost_decision_causal_bounded_and_bonds_not_leveraged():
    history = returns_fixture()
    plan = Plan(execution_policy="cost_aware", research_multiple=3, rebalance="monthly")
    original = {"518880.SH": .45, "511260.SH": .45}
    weights, report = cost_allocation(plan, original, history, history.index[260], 100000, {})
    assert report["status"] == "optimized"
    assert sum(weights.values()) <= sum(original.values())
    assert weights.get("511260.SH", 0) * 3 <= .45 + 1e-8
    assert all(0 <= w <= original[s] + 1e-8 for s, w in weights.items())
    history.iloc[261:] = 100
    assert cost_allocation(plan, original, history, history.index[260], 100000, {}) == (weights, report)


def test_financing_penalty_reduces_borrowing():
    history = returns_fixture()
    weights = {"518880.SH": .6, "511260.SH": .3}
    plan = Plan(execution_policy="cost_aware", research_multiple=3, rebalance="monthly", research_annual_rate=0)
    cheap, _ = cost_allocation(plan, weights, history, history.index[-1], 100000, {})
    costly, _ = cost_allocation(plan.model_copy(update={"research_annual_rate": .30}),
                              weights, history, history.index[-1], 100000, {})
    assert sum(costly.values()) < sum(cheap.values())
    assert sum(costly.values()) * 3 <= 1 + 1e-6


def test_unreliable_data_or_failed_solver_fails_closed(monkeypatch):
    from types import SimpleNamespace
    history = returns_fixture()
    args = (Plan(execution_policy="cost_aware"), {"518880.SH": .5})
    assert not cost_allocation(*args, history.iloc[:20], history.index[19], 100000, {})[0]
    monkeypatch.setattr("ai_stock_bot.v4.cost_allocation.minimize", lambda *a, **k:
                        SimpleNamespace(success=False, x=np.array([np.nan]), message="failure"))
    weights, report = cost_allocation(*args, history, history.index[-1], 100000, {})
    assert weights == {} and report["status"] == "failed"


def test_executed_plan_equals_restored_forward_and_cooldown(tmp_path):
    histories = fixture_history()
    plan = Plan(symbols=list(histories), execution_policy="cost_aware", research_multiple=3,
                rebalance="monthly", trend_signal="multi_horizon", exposure_policy="volatility_target")
    store = Store(tmp_path)
    reference = simulate(store, plan, histories, "2021-01-01", "2021-12-31")
    engine = Portfolio(store, plan, histories)
    engine.state["pending"] = engine.make_plan(pd.Timestamp("2020-12-31"))
    for date in histories[plan.symbols[0]].loc["2021"].index:
        engine.step(date)
        engine = Portfolio(store, plan, histories, restored=engine.state)
    assert engine.state["curve"] == reference["curve"]
    assert engine.account.trades == reference["trades"]
    engine.state["cooldown"] = 2
    assert engine.make_plan(pd.Timestamp("2021-12-31"))["weights"] == {}


def test_new_defaults_and_active_account_policy_guard(tmp_path):
    store = Store(tmp_path)
    assert settings(store)["plan"]["execution_policy"] == "corrected"
    save_settings(store, Plan(), 1)
    store.set_meta("portfolio_state", {"account": {"positions": {}}})
    with pytest.raises(ValueError, match="执行规则"):
        save_settings(store, Plan(execution_policy="cost_aware"), 2)
    assert settings(store)["plan"]["execution_policy"] == "legacy"
