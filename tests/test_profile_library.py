import copy

import pytest

from ai_stock_bot.v2.store import Store, ConflictError
from ai_stock_bot.v4.model_recipes import model_specs, recipe_parameters
from ai_stock_bot.v4.profile_library import KEY, view
from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS, Plan, fingerprint
from ai_stock_bot.v4.service import settings, save_settings


def fixed(slot):
    return Plan(**{k: v for k, v in FIXED_PROFILE_PRESETS[slot].items() if k != 'label'}, profile=slot, preset_version=1)


def test_three_profiles_keep_independent_models_after_reload(tmp_path):
    store = Store(tmp_path)
    revision = 1
    chosen = {'conservative': ('rotation', 'legacy'), 'balanced': ('ml', 'ridge20_light'), 'aggressive': ('ml', 'xgb20_smooth')}
    keys = {}
    for slot, (method, recipe) in chosen.items():
        plan = Plan.model_validate({**fixed(slot).model_dump(), 'method': method, 'model_recipe': recipe})
        result = save_settings(store, plan, revision, slot)
        revision = result['revision']
        keys[slot] = fingerprint(plan)
    store = Store(tmp_path)
    current = settings(store)
    library = view(store, current)
    assert library['active_slot'] == 'aggressive'
    for slot, (method, recipe) in chosen.items():
        saved = library['entries'][slot]
        assert saved['plan']['method'] == method
        assert saved['plan']['model_recipe'] == recipe
        assert saved['plan_key'] == keys[slot]
    assert current['plan']['model_recipe'] == 'xgb20_smooth'


def test_editing_one_slot_does_not_overwrite_another(tmp_path):
    store = Store(tmp_path)
    first = save_settings(store, fixed('aggressive'), 1, 'aggressive')
    before = copy.deepcopy(store.meta(KEY)['entries'])
    plan = Plan.model_validate({**fixed('balanced').model_dump(), 'core_anchor': .8, 'model_recipe': 'extra20'})
    save_settings(store, plan, first['revision'], 'balanced')
    after = store.meta(KEY)['entries']
    assert after['aggressive'] == before['aggressive']
    assert after['conservative'] == before['conservative']
    assert after['balanced']['core_anchor'] == .8
    assert after['balanced']['model_recipe'] == 'extra20'


def test_cash_costs_and_validation_dates_shared_not_strategy_fields(tmp_path):
    store = Store(tmp_path)
    plan = Plan.model_validate({**fixed('aggressive').model_dump(), 'starting_cash': 140000,
                              'commission_rate': .001, 'validation_start_year': 2022})
    saved = save_settings(store, plan, 1, 'aggressive')
    for entry in store.meta(KEY)['entries'].values():
        assert not {'starting_cash', 'commission_rate', 'validation_start_year'} & entry.keys()
    for entry in view(store, saved)['entries'].values():
        assert entry['plan']['starting_cash'] == 140000
        assert entry['plan']['commission_rate'] == .001
        assert entry['plan']['validation_start_year'] == 2022


def test_conflict_and_invalid_slot_leave_library_and_active_plan_intact(tmp_path):
    store = Store(tmp_path)
    saved = save_settings(store, fixed('aggressive'), 1, 'aggressive')
    before = copy.deepcopy(store.meta(KEY))
    with pytest.raises(ConflictError):
        save_settings(store, fixed('balanced'), 1, 'balanced')
    with pytest.raises(ValueError):
        save_settings(store, fixed('balanced'), saved['revision'], 'unknown')
    assert store.meta(KEY) == before and settings(store) == saved


def test_initial_read_does_not_write_or_replace_existing_plan(tmp_path):
    store = Store(tmp_path)
    old = Plan.model_validate({**fixed('aggressive').model_dump(), 'method': 'rotation'})
    saved = {'plan': old.model_dump(), 'revision': 8, 'profile_slot': 'aggressive'}
    store.set_meta('investment_settings', saved)
    result = view(store, settings(store))
    assert result['entries']['aggressive']['plan']['method'] == 'rotation'
    assert store.meta(KEY) is None and settings(store) == saved


def test_api_preserves_named_slot_when_parameters_are_custom(tmp_path):
    from fastapi.testclient import TestClient
    from ai_stock_bot.v2.api import create_app
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        plan = {**fixed('balanced').model_dump(), 'method': 'rotation'}
        response = client.put('/api/v2/investment/plan', json={'plan': plan, 'revision': 1, 'profile_slot': 'balanced'})
        assert response.status_code == 200, response.text
        assert response.json()['plan']['profile'] == 'custom'
        data = client.get('/api/v2/investment').json()
        assert data['profile_library']['active_slot'] == 'balanced'
        assert data['profile_library']['entries']['balanced']['plan']['method'] == 'rotation'
        assert data['profile_library']['entries']['aggressive']['plan']['method'] == 'ml'
        assert data['model_recipes']['xgb20_smooth']['regularization'] == 50


@pytest.mark.parametrize('recipe', model_specs())
def test_named_recipe_is_canonical_and_has_distinct_identity(recipe):
    plan = Plan(method='ml', model_recipe=recipe)
    for key, value in recipe_parameters(recipe).items():
        assert getattr(plan, key) == value
    assert fingerprint(plan) != fingerprint(Plan(method='ml', model_recipe='legacy'))


def test_unknown_recipe_rejected():
    with pytest.raises(ValueError, match='未知模型配方'):
        Plan(method='ml', model_recipe='invented')


@pytest.mark.parametrize('recipe', ['cnn20', 'cnn20_long', 'xgb20', 'xgb20_smooth'])
def test_optional_models_use_isolated_runtime(recipe):
    from ai_stock_bot.v2.jobs import external_runtime
    from ai_stock_bot.v2.schema import JobRequest
    request = JobRequest(kind='portfolio_test', investment=Plan(method='ml', model_recipe=recipe))
    assert external_runtime(request, model_python='full-python') == 'full-python'
