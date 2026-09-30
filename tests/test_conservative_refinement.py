import copy
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def experiment(monkeypatch):
    directory = Path(__file__).resolve().parents[1]/'scripts'
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location('conservative_refinement_test', directory/'refine_conservative_core.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def periods(cagr=.06, later=.10, stressed=.055, recent=-.015, drawdown=-.047):
    return {'full': {'base': {'liquidation_cagr': cagr, 'max_drawdown': drawdown}, 'stress': {'liquidation_cagr': stressed}},
            'later': {'base': {'liquidation_cagr': later}}, 'recent': {'base': {'liquidation_return': recent}}}


def test_research_preserves_defensive_limits_and_other_profiles(experiment):
    from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS, Plan
    before = copy.deepcopy(FIXED_PROFILE_PRESETS)
    spec = experiment.protocol()
    assert len(spec['cases']) == 14
    for case in spec['cases'].values():
        Plan.model_validate(case)
        assert case['research_multiple'] == 1 and case['target_volatility'] == .04
        assert case['max_exposure'] == .70 and case['max_weight'] == .35
        assert case['drawdown_pause'] == .06 and case['allocation_policy'] == 'defensive_v1'
        assert case['model_recipe'] == 'hgb20_regularized'
    assert FIXED_PROFILE_PRESETS == before


@pytest.mark.parametrize('values', [
    {'cagr': .0601}, {'later': .09}, {'stressed': .054}, {'recent': -.02}, {'drawdown': -.06},
])
def test_gain_does_not_hide_risk_or_worse_later_period(experiment, values):
    assert not all(experiment.promotion_checks(periods(**{'cagr': .066, 'stressed': .061, **values}), periods(), True).values())


def test_failed_historical_gate_blocks_promotion(experiment):
    assert not all(experiment.promotion_checks(periods(cagr=.066, stressed=.061), periods(), False).values())
    assert all(experiment.promotion_checks(periods(cagr=.066, stressed=.061), periods(), True).values())


def test_selection_prefers_risk_eligible_over_high_return(experiment):
    def pair(cagr, drawdown, annual):
        item = {'liquidation_cagr': cagr, 'max_drawdown': drawdown, 'per_year': [{'return_pct': annual}]}
        return {'base': item, 'stress': item}
    assert experiment.selection_score(pair(.04, -.04, -.01)) > experiment.selection_score(pair(.20, -.07, -.01))
    assert experiment.selection_score(pair(.04, -.04, -.01)) > experiment.selection_score(pair(.20, -.04, -.04))
