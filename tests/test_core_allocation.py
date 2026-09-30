from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.core_allocation import core_allocation
from ai_stock_bot.v4.portfolio import Portfolio, simulate
from ai_stock_bot.v4.schema import Plan, fingerprint
from test_allocation import fixture_history


def plan(policy="balanced_v1", **kw):
    return Plan.model_validate(dict(allocation_policy=policy, profile="custom", execution_policy="cost_aware",
        exposure_policy="volatility_target", trend_signal="multi_horizon", rebalance="monthly", **kw))


def fixture():
    rng = np.random.default_rng(29)
    history = pd.DataFrame(rng.normal(.001, .01, (300, 3)), index=pd.bdate_range("2020-01-01", periods=300),
                           columns=["510300.SH", "518880.SH", "511260.SH"])
    history["511260.SH"] *= .25
    rows = [{"symbol": s, "strength": 1.} for s in history]
    return history, rows


@pytest.mark.parametrize("policy", ["defensive_v1", "balanced_v1"])
def test_causal_budget_caps_and_no_borrowing(policy):
    history, rows = fixture()
    p = plan(policy, max_exposure=.9, max_weight=.4, target_volatility=.05)
    args = (p, rows, history, history.index[260], 100000, 100000, {})
    w, r = core_allocation(*args)
    assert r["status"] == "optimized"
    assert sum(w.values()) <= p.max_exposure+1e-7
    assert max(w.values()) <= p.max_weight+1e-7
    assert r["estimated_volatility"] <= .05+1e-6
    if policy == "defensive_v1":
        assert sum(v for s, v in w.items() if s != "511260.SH") <= .35+1e-7
    history.iloc[261:] = 999.
    assert core_allocation(*args) == (w, r)


def test_soft_anchor_allows_cash_and_does_not_cap_each_asset():
    history, rows = fixture()
    p = plan(core_anchor=.2, max_exposure=.9, max_weight=.5, target_volatility=.2)
    w, r = core_allocation(p, rows, history, history.index[-1], 100000, 100000, {}, {s: .04 for s in history})
    assert any(w.get(s, 0) > r["core_weights"][s] for s in w)
    empty, _ = core_allocation(p, rows, history, history.index[-1], 100000, 100000, {}, {s: -1 for s in history})
    assert sum(empty.values()) < 1e-6


def test_invalid_inputs_and_solver_fail_closed(monkeypatch):
    h, rows = fixture()
    p = plan()
    assert core_allocation(p, rows, h.iloc[:20], h.index[19], 100000, 100000, {})[0] == {}
    monkeypatch.setattr("ai_stock_bot.v4.core_allocation.minimize", lambda *a, **k: SimpleNamespace(success=False, message="failed"))
    w, r = core_allocation(p, rows, h, h.index[-1], 100000, 100000, {})
    assert w == {} and r["status"] == "failed"


@pytest.mark.parametrize("kw", [{"research_multiple": 2}, {"research_interval": "30m"}, {"execution_policy": "legacy"}, {"method": "ml_class"}, {"method": "cnn"}])
def test_invalid_core_execution_rejected(kw):
    with pytest.raises(ValueError):
        Plan.model_validate({**plan().model_dump(), **kw})


def test_profile_identity_is_versioned_and_legacy_unchanged():
    old = Plan()
    assert fingerprint(old) == fingerprint(old.model_copy(update={"core_anchor": 1, "risk_aversion": 5}))
    new = plan()
    assert fingerprint(new) != fingerprint(new.model_copy(update={"core_anchor": 1}))
    assert fingerprint(new) != fingerprint(new.model_copy(update={"allocation_policy": "defensive_v1"}))


def test_restored_forward_matches_offline_core(tmp_path):
    histories = fixture_history()
    p = plan(symbols=list(histories), target_volatility=.08)
    store = Store(tmp_path)
    reference = simulate(store, p, histories, "2021-01-01", "2021-12-31")
    engine = Portfolio(store, p, histories)
    engine.state["pending"] = engine.make_plan(pd.Timestamp("2020-12-31"))
    for day in histories[p.symbols[0]].loc["2021"].index:
        engine.step(day)
        engine = Portfolio(store, p, histories, restored=engine.state)
    assert engine.state["curve"] == reference["curve"]
    assert engine.account.trades == reference["trades"]
    assert all(t["signal_date"] < t["date"] for t in reference["trades"])
    engine.state["cooldown"] = 2
    assert engine.make_plan(pd.Timestamp("2021-12-31"))["weights"] == {}


def test_changing_core_policy_requires_archive(tmp_path):
    from ai_stock_bot.v4.service import save_settings, settings
    store = Store(tmp_path)
    original = save_settings(store, plan("defensive_v1"), 1)
    store.set_meta("portfolio_state", {"account": {"positions": {}}})
    with pytest.raises(ValueError, match="组合政策"):
        save_settings(store, plan("balanced_v1"), 2)
    assert settings(store) == original


def test_unverified_model_override_fails_explicitly(tmp_path):
    with pytest.raises(ValueError, match="尚未验证"):
        Portfolio(Store(tmp_path), plan(), fixture_history(), method="cnn_class")
