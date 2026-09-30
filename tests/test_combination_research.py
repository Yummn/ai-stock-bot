import numpy as np
import pandas as pd
import pytest

from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.portfolio import simulate
from ai_stock_bot.v4.research import (ModelSpec, ResearchPortfolio, build_panel,
    chronological_masks, dataset, model_specs, replay, research_features)
from ai_stock_bot.v4.schema import Plan, fingerprint
from test_allocation import fixture_history


def test_extended_features_are_causal_and_group_one_hot():
    frame = fixture_history()["510300.SH"]
    changed = frame.copy()
    cutoff = pd.Timestamp("2021-06-30")
    changed.loc[changed.index > cutoff, ["adjusted", "close", "volume"]] *= 100
    before = research_features(frame, "510300.SH")
    after = research_features(changed, "510300.SH")
    pd.testing.assert_frame_equal(before.loc[:cutoff], after.loc[:cutoff])
    assert (before.filter(like="group_").sum(axis=1) == 1).all()
    assert len(before.columns) > 20


@pytest.mark.parametrize("horizon", [5, 20])
@pytest.mark.parametrize("window", [1, 20])
def test_training_and_validation_labels_are_mature_and_purged(horizon, window):
    histories = fixture_history()
    spec = ModelSpec(horizon=horizon, window=window)
    x, y, dates, ends, _ = dataset(histories, spec)
    mature, train, valid, split = chronological_masks(dates, ends, "2022-06-01", 3)
    assert mature.any() and train.any() and valid.any()
    assert ends[mature].max() < pd.Timestamp("2022-06-01").value
    assert ends[train].max() < dates[valid].min()
    assert not (train & valid).any()
    assert len(x) == len(y) == len(dates) == len(ends)
    changed = {s: f.copy() for s, f in histories.items()}
    for f in changed.values():
        f.loc[f.index >= "2022-06-01", ["open", "close", "adjusted"]] *= 2
    xx, yy, *_ = dataset(changed, spec)
    np.testing.assert_allclose(x[mature], xx[mature], equal_nan=True)
    np.testing.assert_allclose(y[mature], yy[mature], equal_nan=True)


def test_reference_replay_identical_to_execution_engine(tmp_path):
    histories = fixture_history()
    plan = Plan(symbols=list(histories), trend_signal="multi_horizon",
        exposure_policy="volatility_target", research_multiple=2, rebalance="monthly")
    store = Store(tmp_path)
    key = fingerprint(plan)
    research = replay(store, plan, histories, "2021-01-01", "2021-12-31", "multi")
    original = simulate(store, plan, histories, "2021-01-01", "2021-12-31")
    assert research["curve"] == original["curve"]
    for field in ("return_pct", "max_drawdown", "fees", "trade_count", "interest_paid"):
        assert research[field] == original[field]
    assert fingerprint(plan) == key


@pytest.mark.parametrize("family", ["breakout", "pullback", "allocation", "ml"])
def test_research_respects_cooldown_and_next_open(tmp_path, family):
    histories = fixture_history()
    plan = Plan(symbols=list(histories), research_multiple=2, exposure_policy="volatility_target")
    date = pd.Timestamp("2021-06-30")
    panel = {"rows": [{"date": "2021-06-30", "symbol": s, "value": .03} for s in histories],
        "reports": [{"cutoff": "2021-06-01", "status": "model"}]}
    engine = ResearchPortfolio(Store(tmp_path), plan, histories, family, panel)
    decision = engine.make_plan(date)
    engine.execute(decision, date)
    assert not engine.account.trades
    engine.state["cooldown"] = 2
    decision = engine.make_plan(date)
    assert not decision["weights"]
    assert decision["risk_budget"]["target_gross"] == 0


def test_monthly_panel_contains_only_month_predictions_and_mature_labels(tmp_path):
    histories = fixture_history()
    spec = ModelSpec(model="ridge")
    panel = build_panel(histories, spec, tmp_path, "fixture", start="2022-06-01", end="2022-06-30")
    report = panel["reports"][0]
    assert report["label_end"] < report["cutoff"]
    assert all(row["date"].startswith("2022-06") for row in panel["rows"])
    assert build_panel(histories, spec, tmp_path, "fixture", start="2022-06-01", end="2022-06-30") == panel
    with pytest.raises(ValueError, match="inputs changed"):
        build_panel(histories, spec, tmp_path, "changed", start="2022-06-01", end="2022-06-30")


def test_all_models_have_fixed_deterministic_configuration():
    assert len(model_specs()) == 13
    assert model_specs()["hgb5_old"].horizon == 5
    assert model_specs()["hgb20_5y"].years == 5
    assert model_specs()["cnn20"].window == 20
    assert model_specs()["cnn20_long"].window == 40
    assert model_specs()["ridge20_light"].regularization == 10
    assert model_specs()["xgb20_smooth"].regularization == 50
    assert model_specs()["extra20_smooth"].leaf_samples == 80


def test_research_api_is_read_only_and_separate_from_current_evidence(tmp_path):
    from fastapi.testclient import TestClient
    from ai_stock_bot.v2.api import create_app
    from ai_stock_bot.v4.research_report import KEY
    from ai_stock_bot.v4.service import settings
    store = Store(tmp_path)
    before = settings(store)
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        assert client.get('/api/v2/investment/research-combinations').json() == {"report": None}
        fixture = {"id": "fixture", "goal_proven": False, "applied": False}
        store.set_meta(KEY, fixture)
        assert client.get('/api/v2/investment/research-combinations').json()["report"] == fixture
        response = client.get('/api/v2/investment/research-combinations/export')
        assert response.json() == fixture
        assert 'attachment' in response.headers['content-disposition']
        assert client.post('/api/v2/investment/research-combinations', json={}).status_code == 405
    assert settings(store) == before


def test_incomplete_study_cannot_be_published(tmp_path):
    from ai_stock_bot.v4.research import write_json
    from ai_stock_bot.v4.research_report import publication
    write_json(tmp_path / 'protocol.json', {})
    write_json(tmp_path / 'results.json', {})
    with pytest.raises(ValueError, match='completed'):
        publication(tmp_path)


def test_complete_hold_reference_does_not_skip_small_initial_allocations(tmp_path):
    from ai_stock_bot.v4.portfolio import Portfolio
    from ai_stock_bot.v4.schema import ASSETS
    frame = fixture_history()["510300.SH"]
    histories = {s: frame.copy() for s in ASSETS}
    plan = Plan(max_exposure=.9, max_weight=.45, drift_band=0.)
    engine = Portfolio(Store(tmp_path), plan, histories, method="hold")
    pending = engine.make_plan(pd.Timestamp("2020-01-01"))
    engine.execute(pending, pd.Timestamp("2020-01-02"))
    assert set(engine.account.positions) == set(ASSETS)
    assert len(engine.account.trades) == 6
