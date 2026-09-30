import copy
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def experiment(monkeypatch):
    path = Path(__file__).resolve().parents[1]/"scripts"
    monkeypatch.syspath_prepend(str(path))
    spec = importlib.util.spec_from_file_location("balanced_refinement_test", path/"refine_balanced_core.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def periods(cagr=.11, later=.20, stressed=.105, recent=-.02):
    return {"full": {"base": {"liquidation_cagr": cagr}, "stress": {"liquidation_cagr": stressed}},
        "later": {"base": {"liquidation_cagr": later}},
        "recent": {"base": {"liquidation_return": recent}}}


def test_no_promotion_for_tiny_gain_or_failed_risk(experiment):
    baseline = periods()
    assert not all(experiment.promotion_checks(periods(cagr=.1101), baseline, True).values())
    assert not all(experiment.promotion_checks(periods(cagr=.12, stressed=.115), baseline, False).values())


@pytest.mark.parametrize("update", [{"later": .19}, {"stressed": .10}, {"recent": -.04}])
def test_cannot_hide_worse_validation_period(experiment, update):
    values = {"cagr": .12, "stressed": .115, **update}
    assert not all(experiment.promotion_checks(periods(**values), periods(), True).values())


def test_valid_gain_within_risk_can_pass(experiment):
    assert all(experiment.promotion_checks(periods(cagr=.12, stressed=.115), periods(), True).values())


@pytest.mark.parametrize("turnover", [False, True])
def test_search_does_not_increase_risk_or_change_defaults(experiment, turnover):
    from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS
    before = copy.deepcopy(FIXED_PROFILE_PRESETS)
    spec = experiment.protocol(turnover)
    for case in spec["cases"].values():
        assert case["target_volatility"] == .10
        assert case["research_multiple"] == 1
        assert case["max_exposure"] == .98
        assert case["max_weight"] == .40
        assert case["model_recipe"] == "hgb20_regularized"
    assert FIXED_PROFILE_PRESETS == before
