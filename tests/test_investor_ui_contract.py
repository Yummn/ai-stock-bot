from fastapi.testclient import TestClient

from ai_stock_bot.v2.api import create_app
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.schema import Plan, fingerprint
from ai_stock_bot.v4.service import save_settings, summary


def test_long_running_jobs_do_not_disappear_behind_twelve_recent_jobs(tmp_path):
    store = Store(tmp_path)
    old = store.create_job({"kind": "portfolio_research", "investment": Plan().model_dump()})
    store.update_job(old["id"], status="running")
    for _ in range(15):
        new = store.create_job({"kind": "portfolio_test", "investment": Plan().model_dump()})
        store.update_job(new["id"], status="done")
    rows = summary(store)["jobs"]
    assert old["id"] in {row["id"] for row in rows}
    assert len(rows) == 13


def test_account_preferences_and_years_do_not_change_other_strategies(tmp_path):
    store = Store(tmp_path)
    plan = Plan()
    save_settings(store, plan, 1, "balanced")
    with TestClient(create_app(store.root, start_manager=False)) as client:
        before = client.get('/api/v2/investment').json()
        values = before['settings']['plan'] | {'auto_update': True, 'validation_start_year': 2021}
        changed = client.put('/api/v2/investment/plan', json={
            'plan': values, 'revision': before['settings']['revision'], 'profile_slot': 'balanced'})
        assert changed.status_code == 200
        after = client.get('/api/v2/investment').json()
        assert after['profile_library']['active_slot'] == 'balanced'
        assert after['account'] == before['account']
        assert after['plan_key'] == fingerprint(plan)
        shared = set(after['profile_library']['shared_fields'])
        for slot, entry in before['profile_library']['entries'].items():
            assert {k: v for k, v in entry['plan'].items() if k not in shared} == {
                k: v for k, v in after['profile_library']['entries'][slot]['plan'].items() if k not in shared}
        # An older dialog may not silently overwrite another device's settings.
        stale = client.put('/api/v2/investment/plan', json={
            'plan': values, 'revision': before['settings']['revision'], 'profile_slot': 'balanced'})
        assert stale.status_code == 409
