import numpy as np
import pandas as pd
import pytest

from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.portfolio import Portfolio
from ai_stock_bot.v4.research import ResearchPortfolio, build_panel, model_specs
from ai_stock_bot.v4.research_forecast import ResearchForecasts
from ai_stock_bot.v4.schema import Plan, fingerprint
from test_allocation import fixture_history


def test_target_normalization_cannot_see_future_prices():
    from ai_stock_bot.v4.research import target_scales
    histories = fixture_history()
    cutoff = pd.Timestamp("2022-06-30")
    changed = {s: f.copy() for s, f in histories.items()}
    for symbol, frame in histories.items():
        altered = changed[symbol]
        altered.loc[altered.index > cutoff, "adjusted"] *= 100
        a = target_scales({symbol: frame}, 20)
        b = target_scales({symbol: altered}, 20)
        np.testing.assert_allclose(a[frame.index <= cutoff], b[frame.index <= cutoff], equal_nan=True)


def test_normalized_training_cache_and_label_maturity(tmp_path):
    from ai_stock_bot.v4.research import RiskNormalizedSpec, ModelSpec
    histories = fixture_history()
    spec = RiskNormalizedSpec(iterations=20, leaves=5, leaf_samples=80, regularization=10.)
    normalized = build_panel(histories, spec, tmp_path/"normalized", "fixture", start="2022-06-01", end="2022-06-01")
    base = build_panel(histories, ModelSpec(iterations=20), tmp_path/"base", "fixture", start="2022-06-01", end="2022-06-01")
    assert normalized["key"] != base["key"]
    assert all(r["label_end"] < r["cutoff"] for r in normalized["reports"])
    date = pd.Timestamp("2022-06-20")
    changed = {s: f.copy() for s, f in histories.items()}
    for frame in changed.values():
        frame.loc[frame.index > date, ["open", "close", "high", "low", "adjusted"]] *= 100
    other = build_panel(changed, spec, tmp_path/"other", "mutated", start="2022-06-01", end="2022-06-01")
    expected = [r for r in normalized["rows"] if r["date"] <= str(date.date())]
    actual = [r for r in other["rows"] if r["date"] <= str(date.date())]
    assert expected == actual
    assert normalized["reports"][0]["status"] == other["reports"][0]["status"]


def test_named_recipe_is_canonical_and_separate_from_legacy():
    original = Plan(method="ml")
    upgraded = Plan.model_validate({**original.model_dump(), "model_recipe": "hgb20_regularized"})
    assert (upgraded.training_years, upgraded.horizon, upgraded.model_leaves,
            upgraded.model_iterations, upgraded.min_samples) == (3, 20, 5, 120, 800)
    assert fingerprint(original) != fingerprint(upgraded)


def test_adapter_predictions_equal_research_and_ignore_future(tmp_path):
    histories = fixture_history()
    date = pd.Timestamp("2022-06-30")
    reference = build_panel(histories, model_specs()["hgb20_regularized"], tmp_path/"reference",
                            "fixture", start="2022-06-01", end="2022-06-01")
    forecasts = ResearchForecasts(Store(tmp_path/"actual"), histories)
    values, report = forecasts.at(date)
    expected = {r["symbol"]: r["value"] for r in reference["rows"] if r["date"] == "2022-06-30"}
    assert values == expected
    assert report["status"] == reference["reports"][0]["status"]
    assert report["label_end"] < report["cutoff"]
    changed = {s: f.copy() for s, f in histories.items()}
    for frame in changed.values():
        frame.loc[frame.index > date, ["open", "high", "low", "close", "adjusted"]] *= 100
    actual, other = ResearchForecasts(Store(tmp_path/"future"), changed).at(date)
    assert actual == values and other["status"] == report["status"]


def test_production_model_allocation_matches_research(tmp_path, monkeypatch):
    histories = fixture_history()
    plan = Plan(symbols=list(histories), method="ml", model_recipe="hgb20_regularized",
                trend_signal="multi_horizon", research_multiple=3, execution_policy="cost_aware",
                exposure_policy="volatility_target")
    date = pd.Timestamp("2021-06-30")
    rows = [{"date": str(date.date()), "symbol": s, "value": .04} for s in histories]
    report = {"status": "model", "cutoff": "2021-06-01"}
    panel = {"rows": rows, "reports": [report], "spec": {"horizon": 20}}
    monkeypatch.setattr(ResearchForecasts, "at", lambda self, date: ({r["symbol"]: r["value"] for r in rows}, report))
    original = ResearchPortfolio(Store(tmp_path/"reference"), plan, histories, "ml", panel, "trend")
    actual = Portfolio(Store(tmp_path/"production"), plan, histories)
    assert actual.make_plan(date)["weights"] == original.make_plan(date)["weights"]


def test_recipe_saved_through_api_and_changes_evidence_key(tmp_path):
    from fastapi.testclient import TestClient
    from ai_stock_bot.v2.api import create_app
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        previous = client.get("/api/v2/investment").json()["settings"]
        plan = {**previous["plan"], "method": "ml", "model_recipe": "hgb20_regularized"}
        response = client.put("/api/v2/investment/plan", json={"plan": plan, "revision": previous["revision"]})
        assert response.status_code == 200
        current = client.get("/api/v2/investment").json()["settings"]
        assert current["plan"]["horizon"] == 20 and current["revision"] == previous["revision"] + 1
