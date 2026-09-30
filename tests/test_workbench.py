import copy
import json
from pathlib import Path
import runpy

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ai_stock_bot.v2 import evaluation, learning
from ai_stock_bot.v2.api import create_app
from ai_stock_bot.v2.evaluation import find_evidence, scorecard, strategy_key
from ai_stock_bot.v2.schema import AppConfig, JobRequest
from ai_stock_bot.v2.store import Store


def annual(year, gain=.1):
    return dict(year=year, scene="bull", return_pct=gain, excess_return=gain-.05,
                benchmark_return=.05, max_drawdown=-.08, trade_count=3,
                start=f"{year}-01-02", end=f"{year}-12-31", data_hash="fixture")


def test_effective_strategy_identity():
    config = AppConfig(engine="ml")
    changed = config.model_copy(deep=True)
    changed.name = "A different display name"
    changed.models["cnn"]["custom"].params["epochs"] += 1
    assert strategy_key(config) == strategy_key(changed)
    changed.preset().buy_threshold += .01
    assert strategy_key(config) != strategy_key(changed)
    changed = config.model_copy(deep=True)
    changed.risk.commission_rate *= 2
    assert strategy_key(config) != strategy_key(changed)
    changed = config.model_copy(deep=True)
    changed.auto_profile = True
    first = strategy_key(changed)
    changed.models[changed.model]["aggressive"].buy_threshold += .01
    assert strategy_key(changed) != first


def test_scorecard_does_not_hide_missing_or_losing_years():
    rows = [annual(2022, -.1), annual(2023,.2), {"year":2024,"error":"interrupted"}]
    result = scorecard(rows)
    assert not result["complete"]
    assert result["mean_return"] == pytest.approx(.05)
    assert result["loss_years"] == [2022]
    assert result["beat_hold"] == 1
    assert result["verdict"] == "评估尚未完成"


def test_evidence_requires_same_effective_parameters(tmp_path):
    store = Store(tmp_path)
    config = AppConfig(engine="ml", profile="custom")
    request = JobRequest(kind="learn", config=config).model_dump()
    job = store.create_job(request)
    item = dict(config=config.model_dump(), complete=True, per_year=[annual(2022)], check_years=[annual(2023)])
    store.update_job(job["id"],status="done",result={"best":{"custom":item}})
    assert find_evidence(store, config)["matched"]
    config.risk.max_trade_cash += 100
    assert not find_evidence(store, config)["matched"]


def test_evaluation_resumes_frozen_data_and_completed_years(tmp_path, monkeypatch):
    store=Store(tmp_path)
    config=AppConfig()
    request=JobRequest(kind="evaluate", years=[2022,2023], config=config)
    job=store.create_job(request.model_dump())
    frame=pd.DataFrame({"open":[3.11,3.22],"high":[3.3,3.4],"low":[3,3],"close":[3.2,3.3],"adjusted":[3.2,3.3],"volume":[100,200],"source":["fixture","fixture"]},index=pd.to_datetime(["2021-01-01","2021-01-04"]))
    monkeypatch.setattr(evaluation,"load_histories",lambda *a:({"510300.SH":frame},[]))
    calls=[]
    def backtest(store, req, **kwargs):
        calls.append(req.year)
        pd.testing.assert_frame_equal(kwargs['histories']["510300.SH"],frame,check_dtype=False)
        if req.year==2023 and len(calls)==2:
            raise RuntimeError("interrupted")
        return annual(req.year)
    monkeypatch.setattr(evaluation,"run_backtest",backtest)
    with pytest.raises(RuntimeError):
        evaluation.run_evaluation(store,job['id'],request,lambda *a:None)
    assert store.job(job['id'],True)['result']['scorecard']['count']==1
    assert not store.job(job['id'],True)['result']['scorecard']['complete']
    monkeypatch.setattr(evaluation,"load_histories",lambda *a:pytest.fail("must not download again"))
    result=evaluation.run_evaluation(store,job['id'],request,lambda *a:None)
    assert calls==[2022,2023,2023]
    assert result['scorecard']['complete']


def test_paper_cannot_run_an_unsaved_experiment_config(tmp_path):
    app=create_app(tmp_path,start_manager=False)
    with TestClient(app) as client:
        active=client.get('/api/v2/settings').json()['config']
        draft=copy.deepcopy(active)
        draft['profile']='aggressive'
        draft['risk']['max_trade_cash']=999
        submitted=client.post('/api/v2/jobs',json={'kind':'paper','config':draft}).json()
        snapshot=client.get('/api/v2/jobs/'+submitted['id']).json()['request']['config']
        assert snapshot==active
        assert client.get('/api/v2/jobs?kind=paper').json()['total']==1
        assert client.get('/api/v2/jobs?kind=learn').json()['total']==0


def test_learning_custom_and_check_years_do_not_change_ranking(tmp_path, monkeypatch):
    store=Store(tmp_path)
    config=AppConfig(engine='ml')
    request=JobRequest(kind='learn',config=config,profiles=['custom'],years=[2022],check_years=[2023],trials=1)
    job=store.create_job(request.model_dump())
    monkeypatch.setattr(learning,'load_histories',lambda *a:({},[]))
    calls=[]
    def backtest(store,req,**kwargs):
        calls.append(req.year)
        return annual(req.year,.1 if req.year==2022 else -.99)
    monkeypatch.setattr(learning,'run_backtest',backtest)
    result=learning.run_learning(store,job['id'],request,lambda *a:None)
    assert list(result['best'])==['custom']
    item=result['best']['custom']
    assert item['score']==pytest.approx(learning.score_year(annual(2022)))
    assert item['check_years'][0]['return_pct']==-.99
    assert calls==[2022,2023]
    with pytest.raises(ValueError):
        JobRequest(kind='learn',years=[2022],check_years=[2022])


def test_missing_regime_weight_is_redistributed():
    rows=[annual(2025)]
    weights={'specialized':.5,'fallback':.2,'composite':.3}
    assert learning.candidate_score(rows,'conservative',weights)==pytest.approx(learning.score_year(rows[0]))


def test_incomplete_acceptance_cannot_be_applied(tmp_path):
    app=create_app(tmp_path,start_manager=False)
    store=app.state.store
    config=AppConfig(engine='ml',profile='custom')
    request=JobRequest(kind='learn',config=config,profiles=['custom'],years=[2022],check_years=[2023])
    job=store.create_job(request.model_dump())
    suggestion={'complete':True,'config':config.model_dump(),'per_year':[annual(2022)],'check_years':[{'year':2023,'error':'failure'}]}
    store.update_job(job['id'],status='done',result={'best':{'custom':suggestion}})
    with TestClient(app) as client:
        result=client.post(f"/api/v2/jobs/{job['id']}/apply/custom")
        assert result.status_code==400
        assert store.settings()['revision']==1


def test_upgrade_copies_data_once_without_touching_previous_store(tmp_path):
    migrate = runpy.run_path(str(Path(__file__).parents[1] / 'scripts' / 'launch_workbench.py'))['migrate']
    previous = Store(tmp_path / 'v2')
    previous.set_meta('account', {'cash': 12345})
    evaluation.write_gzip(previous.root / 'evaluations' / 'example' / '2022.json.gz', annual(2022))
    target = tmp_path / 'v3'
    migrate(previous.root, target)
    current = Store(target)
    assert current.meta('account') == {'cash': 12345}
    assert evaluation.read_gzip(target / 'evaluations' / 'example' / '2022.json.gz')['year'] == 2022
    current.set_meta('account', {'cash': 23456})
    migrate(previous.root, target)
    assert current.meta('account') == {'cash': 23456}
    assert previous.meta('account') == {'cash': 12345}


def test_saved_defaults_survive_settings_changes_and_restart(tmp_path):
    app = create_app(tmp_path, start_manager=False)
    store = app.state.store
    cfg = AppConfig(engine='ml', profile='custom')
    cfg.models['cnn']['custom'].params['channels'] = 37
    store.save_settings(cfg, 1)
    with TestClient(app) as client:
        assert client.post('/api/v2/settings/defaults', json={'revision': 1}).status_code == 409
        assert client.post('/api/v2/settings/defaults', json={'revision': 2}, headers={'x-forwarded-for': '203.0.113.2'}).status_code == 401
        saved = client.post('/api/v2/settings/defaults', json={'revision': 2}).json()
        assert saved['config'] == cfg.model_dump()
        cfg.models['cnn']['custom'].params['channels'] = 19
        store.save_settings(cfg, 2)
        catalog = client.get('/api/v2/catalog').json()
        assert catalog['defaults']['models']['cnn']['custom']['params']['channels'] == 37
        assert catalog['defaults_saved_at'] == saved['saved_at']
    assert Store(tmp_path).defaults() == saved
    assert Store(tmp_path).settings()['config']['models']['cnn']['custom']['params']['channels'] == 19
