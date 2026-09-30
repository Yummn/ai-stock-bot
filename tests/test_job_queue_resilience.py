import time

import pytest

from ai_stock_bot.v2 import jobs
from ai_stock_bot.v2.schema import AppConfig, JobRequest
from ai_stock_bot.v2.store import Store


@pytest.mark.parametrize('failure', ['invalid_request', 'runtime', 'constructor', 'start_and_cleanup'])
def test_bad_job_does_not_stop_queue(tmp_path, monkeypatch, failure):
    from ai_stock_bot.v4 import observation, schedule

    store = Store(tmp_path)
    monkeypatch.setattr(schedule, 'enqueue_daily', lambda *_: None)
    monkeypatch.setattr(observation, 'enqueue_observation', lambda *_: None)
    bad_id = None
    calls = 0

    def runtime(*args):
        nonlocal calls
        calls += 1
        if failure == 'runtime' and calls == 1:
            raise RuntimeError('runtime resolution failed')
        return 'isolated-test-python'

    class Process:
        exitcode = 0

        def __init__(self, executable, root, job_id):
            self.job_id = job_id
            if failure == 'constructor' and job_id == bad_id:
                raise RuntimeError('constructor failed')

        def start(self):
            if self.job_id == bad_id:
                raise RuntimeError('start failed')
            store.update_job(self.job_id, status='done', result={'complete': True})

        def is_alive(self):
            return False

        def join(self, *_):
            pass

        def close(self):
            if failure == 'start_and_cleanup' and self.job_id == bad_id:
                raise RuntimeError('cleanup failed')

    monkeypatch.setattr(jobs, 'external_runtime', runtime)
    monkeypatch.setattr(jobs, 'ResearchProcess', Process)
    manager = jobs.JobManager(store)
    try:
        with manager.lock:
            request = JobRequest(kind='backtest', config=AppConfig()).model_dump()
            if failure == 'invalid_request':
                request['years'] = ['not-a-year']
            bad_id = store.create_job(request)['id']
            good_id = store.create_job(JobRequest(kind='backtest', config=AppConfig()).model_dump())['id']
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if store.job(good_id)['status'] == 'done' and manager.active is None:
                break
            time.sleep(.05)
        assert manager.thread.is_alive()
        assert store.job(bad_id)['status'] == 'failed'
        assert store.job(bad_id)['error']
        assert store.job(good_id)['status'] == 'done'
        assert store.events(bad_id)
    finally:
        manager.close()
        manager.thread.join(3)
    assert not manager.thread.is_alive()
