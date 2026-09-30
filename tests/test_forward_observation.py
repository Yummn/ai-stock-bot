import copy
import datetime as dt
import json
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ai_stock_bot.v2.api import create_app
from ai_stock_bot.v2.market import TZ
from ai_stock_bot.v2.store import ConflictError, Store, dumps
from ai_stock_bot.v4.forward import advance_daily
from ai_stock_bot.v4.observation import (KEY, control, describe, enqueue_observation,
                                        observation_view, update_observation)
from ai_stock_bot.v4.portfolio import Portfolio
from ai_stock_bot.v4.schema import Plan, fingerprint
from ai_stock_bot.v4.service import save_settings, update_portfolio


@pytest.fixture
def frames():
    dates = pd.bdate_range('2023-01-01', '2025-01-15')
    close = 3 + np.arange(len(dates)) / 2000
    frame = pd.DataFrame({'open': close, 'close': close, 'adjusted': close,
                          'high': close * 1.001, 'low': close * .999, 'volume': 1e8,
                          'source': 'fixture'}, index=dates)
    return {'510300.SH': frame, '518880.SH': frame.copy()}


@pytest.fixture
def plan():
    return Plan(symbols=['510300.SH', '518880.SH'], method='hold', research_multiple=2,
                max_exposure=.9, max_weight=.6)


def clock(day, hour=17):
    return dt.datetime.fromisoformat(f'2025-01-{day:02}T{hour:02}:00:00').replace(tzinfo=TZ)


def test_pinned_study_three_books_no_past_fills_and_idempotent(tmp_path, frames, plan):
    store = Store(tmp_path)
    study = control(store, 'start', plan, now=clock(2))
    result = update_observation(store, study['id'], lambda *a: None, frames, clock(2))
    assert result['last_quote'] == '2025-01-02'  # Future fixture prices are cut off.
    first = store.meta(KEY)
    assert all(not book['account']['trades'] for book in first['books'].values())
    assert all(b['pending']['execute_after'] == '2025-01-02' for b in first['books'].values())
    assert describe(first, plan)['books']['strategy']['sessions'] == 0
    assert json.loads(dumps(observation_view(store, plan)))['current']['books']['strategy']['volatility'] == 0
    update_observation(store, study['id'], lambda *a: None, frames, clock(2))
    assert store.meta(KEY) == first
    update_observation(store, study['id'], lambda *a: None, frames, clock(3))
    saved = store.meta(KEY)
    view = describe(saved, plan)
    assert view['books']['strategy']['trade_count'] > 0
    assert view['books']['strategy']['observed_sessions'] == 1
    assert all(t['date'] >= '2025-01-03' for t in saved['books']['strategy']['account']['trades'])
    assert saved['books']['strategy']['account']['borrowed'] > 0
    assert saved['books']['unlevered']['account']['borrowed'] == 0
    assert saved['books']['hold']['account']['borrowed'] == 0
    assert saved['books']['unlevered']['account'] == saved['books']['hold']['account']
    for receipt in saved['receipts']:
        assert (store.root / receipt['snapshot']).exists()
    changed = plan.model_copy(update={'research_multiple': 3})
    assert not describe(saved, changed)['same_plan']
    assert saved['plan']['research_multiple'] == 2
    assert Store(tmp_path).meta(KEY) == saved


def test_offline_marks_not_counted_as_observed_and_decisions_not_backfilled(tmp_path, frames):
    plan = Plan(symbols=list(frames), rebalance='daily')
    store = Store(tmp_path)
    study = control(store, 'start', plan, now=clock(2))
    update_observation(store, study['id'], lambda *a: None, frames, clock(2))
    update_observation(store, study['id'], lambda *a: None, frames, clock(8))
    saved = store.meta(KEY)
    result = describe(saved, plan)
    assert result['books']['strategy']['sessions'] == 4
    assert result['books']['strategy']['observed_sessions'] == 1
    assert result['books']['strategy']['reconstructed_sessions'] == 3
    assert [d['date'] for d in saved['books']['strategy']['decisions']] == ['2025-01-02', '2025-01-08']
    assert saved['books']['strategy']['pending']['execute_after'] == '2025-01-08'


def test_receipt_time_not_delayed_quote_creates_start_boundary(tmp_path, frames, plan):
    store = Store(tmp_path)
    study = control(store, 'start', plan, now=clock(8))
    old = {s: f.loc[:'2025-01-02'] for s, f in frames.items()}
    update_observation(store, study['id'], lambda *a: None, old, clock(8))
    update_observation(store, study['id'], lambda *a: None, frames, clock(8))
    saved = store.meta(KEY)
    assert all(not b['account']['trades'] for b in saved['books'].values())
    assert describe(saved, plan)['books']['strategy']['sessions'] == 0


def test_gap_interest_uses_debt_before_sale_in_both_helper_and_service(tmp_path, frames, plan):
    store = Store(tmp_path)
    save_settings(store, plan, 1)
    engine = Portfolio(store, plan, frames)
    advance_daily(engine, pd.Timestamp('2025-01-02'), clock(2))
    advance_daily(engine, pd.Timestamp('2025-01-03'), clock(3))
    borrowed = -engine.account.cash
    assert borrowed > 0
    engine.state['pending'] = {'date': '2025-01-03', 'execute_after': '2025-01-03',
                               'generated_at': clock(3).isoformat(), 'weights': {}, 'reason': 'sell'}
    engine.state['started_at'] = clock(2).isoformat()
    initial = copy.deepcopy(engine.state)
    advance_daily(engine, pd.Timestamp('2025-01-08'), clock(8))
    expected = borrowed * plan.research_annual_rate * 3 / 365
    assert engine.state['interest_paid'] == pytest.approx(expected)
    assert not engine.account.positions
    store.set_meta('portfolio_state', initial)

    update_portfolio(store, plan, lambda *a: None, frames, now=clock(8))
    assert store.meta('portfolio_state')['interest_paid'] == pytest.approx(expected)


def test_pause_archive_and_optimistic_write_guard(tmp_path, frames, plan):
    store = Store(tmp_path)
    study = control(store, 'start', plan, now=clock(2))
    update_observation(store, study['id'], lambda *a: None, frames, clock(2))
    saved = store.meta(KEY)
    with pytest.raises(ConflictError):
        control(store, 'pause', plan, study['revision'])
    paused = control(store, 'pause', plan, saved['revision'])
    update_observation(store, study['id'], lambda *a: None, frames, clock(3))
    assert store.meta(KEY) == paused
    resumed = control(store, 'resume', plan, paused['revision'])

    def pause_during_work(*args):
        current = store.meta(KEY)
        if current['status'] == 'active':
            control(store, 'pause', plan, current['revision'])

    with pytest.raises(ConflictError):
        update_observation(store, study['id'], pause_during_work, frames, clock(3))
    assert store.meta(KEY)['last_quote'] == '2025-01-02'
    control(store, 'archive', plan, resumed['revision'] + 1)
    assert store.meta(KEY) is None
    archive = store.meta(f'{KEY}:{study["id"]}')
    assert archive['status'] == 'archived' and archive['books']
    assert len(observation_view(store, plan)['archives']) == 1
    assert 'curve' not in observation_view(store, plan)['archives'][0]['books']['strategy']


def test_scheduler_retries_failed_or_stale_jobs_but_bounds_attempts(tmp_path, plan):
    store = Store(tmp_path)
    now = dt.datetime.now(TZ).replace(hour=17)
    while now.weekday() >= 5:
        now += dt.timedelta(days=1)
    control(store, 'start', plan, now=now)
    for i in range(3):
        at = now + dt.timedelta(minutes=16 * i)
        job = enqueue_observation(store, at)
        assert job
        assert enqueue_observation(store, at) is None
        store.update_job(job['id'], status='failed', error='fixture')
        with store.connect() as c:
            c.execute('UPDATE jobs SET created=?,updated=? WHERE id=?', (at.timestamp(), at.timestamp(), job['id']))
        assert enqueue_observation(store, at + dt.timedelta(minutes=1)) is None
    assert enqueue_observation(store, now + dt.timedelta(hours=2)) is None
    assert enqueue_observation(store, now + dt.timedelta(hours=2), manual=True)


def test_observation_api_does_not_change_main_account_or_plan(tmp_path):
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        before = client.get('/api/v2/investment').json()
        assert client.post('/api/v2/investment/observation', json={'action': 'start', 'plan_key': 'stale'}).status_code == 409
        started = client.post('/api/v2/investment/observation', json={'action': 'start', 'plan_key': before['plan_key']}).json()
        study = started['current']
        assert started['job']['status'] == 'queued'
        assert client.post('/api/v2/investment/observation', json={'action': 'start', 'plan_key': before['plan_key']}).status_code == 400
        after = client.get('/api/v2/investment').json()
        assert after['settings'] == before['settings'] and after['account'] == before['account']
        archived = client.post('/api/v2/investment/observation', json={'action': 'archive', 'revision': study['revision']}).json()
        assert archived['current'] is None and len(archived['archives']) == 1
        exported = client.get('/api/v2/investment/observation/export', params={'study_id': study['id']})
        assert exported.status_code == 200 and exported.json()['status'] == 'archived'
        assert client.get('/api/v2/investment/observation/export', params={'study_id': '../secret'}).status_code == 422


def test_first_receipt_api_json_and_engine_version_guard(tmp_path, frames, plan):
    store = Store(tmp_path)
    study = control(store, 'start', plan, now=clock(2))
    update_observation(store, study['id'], lambda *a: None, frames, clock(2))
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        response = client.get('/api/v2/investment/observation')
        assert response.status_code == 200
        assert response.json()['current']['books']['strategy']['return_pct'] == 0
    old = store.meta(KEY)
    old['engine'] = 'different-version'
    store.set_meta(KEY, old)
    with pytest.raises(ValueError, match='引擎已升级'):
        update_observation(store, study['id'], lambda *a: None, frames, clock(3))
    assert enqueue_observation(store, manual=True) is None
    assert store.meta(KEY) == old


def test_daily_receipts_match_shared_backtest_engine_on_identical_orders(tmp_path, frames):
    plan = Plan(symbols=list(frames), rebalance='daily', research_multiple=2)
    forward = Portfolio(Store(tmp_path), plan, frames)
    replay = Portfolio(Store(tmp_path), plan, frames)
    replay.step(pd.Timestamp('2025-01-02'), execute=False)
    advance_daily(forward, pd.Timestamp('2025-01-02'), clock(2))
    for day in [3, 6, 7, 8]:
        replay.step(pd.Timestamp(clock(day).date()))
        advance_daily(forward, pd.Timestamp(clock(day).date()), clock(day))
        assert forward.equity() == pytest.approx(replay.equity())
        assert forward.state['interest_paid'] == pytest.approx(replay.state['interest_paid'])
        assert len(forward.account.trades) == len(replay.account.trades)


def test_bad_data_does_not_partially_write_any_of_three_books(tmp_path, frames, plan):
    store = Store(tmp_path)
    study = control(store, 'start', plan, now=clock(2))
    update_observation(store, study['id'], lambda *a: None, frames, clock(2))
    saved = store.meta(KEY)
    incomplete = {**frames, '518880.SH': frames['518880.SH'].loc[:'2025-01-02']}
    with pytest.raises(ValueError, match='日期不一致'):
        update_observation(store, study['id'], lambda *a: None, incomplete, clock(3))
    assert store.meta(KEY) == saved


def test_ml_observation_runs_causal_training_with_three_isolated_books(tmp_path, frames):
    store = Store(tmp_path)
    plan = Plan(method='ml', symbols=list(frames), model_iterations=20, min_samples=100)
    study = control(store, 'start', plan, now=clock(2))
    update_observation(store, study['id'], lambda *a: None, frames, clock(2))
    saved = store.meta(KEY)
    assert set(saved['books']) == {'strategy', 'unlevered', 'hold'}
    assert all(not book['account']['trades'] for book in saved['books'].values())
    decisions = saved['books']['strategy']['decisions']
    assert decisions and decisions[-1]['model'] is not None
    assert decisions[-1]['generated_at'] == clock(2).isoformat()


def test_failed_worker_keeps_observation_and_manager_alive(tmp_path, frames, plan, monkeypatch):
    from ai_stock_bot.v2 import jobs
    from ai_stock_bot.v2.schema import JobRequest
    from ai_stock_bot.v4 import observation, schedule

    class FailedProcess:
        exitcode = 3221225477
        def start(self): pass
        def is_alive(self): return False
        def join(self, *args): pass
        def close(self): pass

    class Context:
        def Process(self, **kwargs): return FailedProcess()

    store = Store(tmp_path)
    study = control(store, 'start', plan, now=clock(2))
    update_observation(store, study['id'], lambda *a: None, frames, clock(2))
    before = store.meta(KEY)
    monkeypatch.setattr(jobs.mp, 'get_context', lambda *args: Context())
    monkeypatch.setattr(observation, 'enqueue_observation', lambda *args: None)
    monkeypatch.setattr(schedule, 'enqueue_daily', lambda *args: None)
    manager = jobs.JobManager(store)
    try:
        job = store.create_job(JobRequest(kind='portfolio_observe', observation_id=study['id'],
                                          investment=plan).model_dump())
        for _ in range(50):
            current = store.job(job['id'])
            if current['status'] == 'failed':
                break
            time.sleep(.1)
        assert current['status'] == 'failed' and '0xC0000005' in current['error']
        assert manager.thread.is_alive()
        assert store.meta(KEY) == before
    finally:
        manager.close()


def test_two_devices_and_scheduler_cannot_duplicate_observation_job(tmp_path, plan):
    from concurrent.futures import ThreadPoolExecutor

    store = Store(tmp_path)
    control(store, 'start', plan)
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: enqueue_observation(store, manual=True), range(8)))
    assert sum(result is not None for result in results) == 1
    assert store.jobs(kind='portfolio_observe')['total'] == 1


def test_manual_updates_do_not_exhaust_daily_automatic_retry_budget(tmp_path, plan):
    store = Store(tmp_path)
    now = clock(2)
    control(store, 'start', plan, now=now)
    for _ in range(4):
        job = enqueue_observation(store, now=now, manual=True)
        store.update_job(job['id'], status='done')
    scheduled = enqueue_observation(store, now=now)
    assert scheduled
    assert store.job(scheduled['id'], True)['request']['observation_automatic']
