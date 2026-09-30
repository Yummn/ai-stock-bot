import copy
import datetime as dt
from dataclasses import asdict

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ai_stock_bot.v2.api import create_app
from ai_stock_bot.v2.schema import JobRequest
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4 import lab
from ai_stock_bot.v4.lab_schema import Study
from ai_stock_bot.v4.model_recipes import ModelTuning, resolved_spec
from ai_stock_bot.v4.schema import Plan, fingerprint


def test_old_plan_identity_and_redundant_overrides_unchanged():
    plan = Plan()
    assert fingerprint(plan) == "1bf291d00ed1e75cc9a26da8"
    named = Plan(method="ml", model_recipe="ridge20")
    redundant = Plan(method="ml", model_recipe="ridge20", model_tuning={"regularization": 100})
    assert redundant.model_tuning is None
    assert fingerprint(named) == fingerprint(redundant)


@pytest.mark.parametrize("recipe,params", [
    ("hgb20_regularized", {"iterations": 50, "leaves": 9, "leaf_samples": 40, "regularization": 3.}),
    ("ridge20", {"regularization": 30}), ("extra20", {"iterations": 60, "leaf_samples": 20}),
    ("xgb20", {"iterations": 60, "regularization": 50}), ("cnn20", {"window": 40, "epochs": 12}),
])
def test_each_model_receives_effective_parameters_and_distinct_cache(tmp_path, recipe, params):
    from ai_stock_bot.v4.research_forecast import ResearchForecasts
    from ai_stock_bot.v4.portfolio import Portfolio
    from test_allocation import fixture_history
    plan = Plan(method="ml", model_recipe=recipe, model_tuning=params)
    original = Plan(method="ml", model_recipe=recipe)
    assert fingerprint(plan) != fingerprint(original)
    restored = Plan.model_validate(plan.model_dump())
    assert restored == plan
    histories = fixture_history()
    store = Store(tmp_path)
    engine = Portfolio(store, plan, histories)
    default = ResearchForecasts(store, histories, recipe=recipe)
    for key, value in params.items():
        assert getattr(engine.forecasts.spec, key) == value
    assert engine.forecasts.directory != default.directory


def test_inactive_or_invalid_parameters_rejected():
    with pytest.raises(ValueError, match="不使用"):
        Plan(method="ml", model_recipe="ridge20", model_tuning={"iterations": 100})
    with pytest.raises(ValueError):
        Plan(method="rotation", model_tuning={"years": 5})
    with pytest.raises(ValueError):
        ModelTuning(regularization=float("nan"))


@pytest.mark.parametrize("changes", [{"check_start_year": 2024}, {"check_end_year": 2026}, {"start_year": 2025, "end_year": 2024}])
def test_study_time_splits_validated(changes):
    with pytest.raises(ValueError):
        Study(mode="tune", **changes)


def test_candidates_do_not_change_shared_portfolio_rules_or_base():
    base = Plan(method="ml", model_recipe="hgb20_regularized", research_multiple=2)
    original = base.model_dump()
    rows = lab.candidates(base, Study(mode="tune", budget=9))
    assert base.model_dump() == original
    for row in rows:
        for key in ("symbols", "allocation", "starting_cash", "research_multiple", "commission_rate", "slippage_bps", "rebalance", "max_exposure", "max_weight", "execution_policy"):
            assert row["plan"][key] == original[key]
    values = [resolved_spec(r["plan"]["model_recipe"], r["plan"]["model_tuning"]).regularization for r in rows if r["plan"]["method"] == "ml"]
    assert min(values) < 10 < max(values)
    assert len({r["id"] for r in rows}) == len(rows)


@pytest.fixture
def fake_replay(monkeypatch):
    calls = []
    frame = pd.DataFrame({"close": [1., 1.1]}, index=pd.to_datetime(["2020-12-30", "2025-12-31"]))
    monkeypatch.setattr(lab, "load_histories", lambda *a, **k: ({"510300.SH": frame}, {"ok": True}))
    monkeypatch.setattr(lab, "audit", lambda *a: {"ok": True})

    def replay(store, plan, histories, start, end, **kwargs):
        calls.append((fingerprint(plan), start.year, end.year))
        score = .01
        if plan.method == "ml":
            reg = resolved_spec(plan.model_recipe, plan.model_tuning).regularization
            score = .2 if reg == 5 else .1
            if start.year == 2025:
                score = -.3 if reg == 5 else .3
        return {"per_year": [{"year": y, "return_pct": score} for y in range(start.year, end.year + 1)],
                "annualized_return": score, "return_pct": score, "max_drawdown": -.05,
                "curve": [{"date": str(start), "equity": 100000}, {"date": str(end), "equity": 100000 * (1 + score)}],
                "trade_count": 4, "fees": 10., "interest_paid": 0., "training": []}
    monkeypatch.setattr(lab, "simulate", replay)
    return calls, replay


def new_job(tmp_path, study=None):
    store = Store(tmp_path)
    request = JobRequest(kind="portfolio_research", investment=Plan(), study=study or Study(mode="tune", budget=3))
    job = store.create_job(request.model_dump())
    return store, request, job["id"]


def test_later_check_never_changes_winner_and_resume_reuses_frozen_results(tmp_path, fake_replay):
    calls, _ = fake_replay
    store, request, job_id = new_job(tmp_path)
    result = lab.run_study(store, job_id, request, lambda *a: None)
    best = next(r for r in result["candidates"] if r["id"] == result["winner_id"])
    assert best["plan"]["model_tuning"]["regularization"] == 5
    assert best["check"]["annualized_return"] == -.3
    assert result["complete"]
    original_calls = len(calls)
    second = lab.run_study(store, job_id, request, lambda *a: None)
    assert second == result and len(calls) == original_calls
    with pytest.raises(ValueError, match="版本或参数"):
        lab.run_study(store, job_id, request.model_copy(update={"study": Study(mode="tune", budget=5)}), lambda *a: None)


def test_failed_candidate_blocks_promotion_and_resume_retries_only_missing_stage(tmp_path, fake_replay, monkeypatch):
    calls, replay = fake_replay
    store, request, job_id = new_job(tmp_path)
    failed = []

    def crash_once(store, plan, *args, **kwargs):
        if plan.method == "ml" and not failed:
            failed.append(True)
            raise RuntimeError("deliberate worker error")
        return replay(store, plan, *args, **kwargs)
    monkeypatch.setattr(lab, "simulate", crash_once)
    with pytest.raises(ValueError, match="部分候选"):
        lab.run_study(store, job_id, request, lambda *a: None)
    partial = store.job(job_id, True)["result"]
    assert not partial["complete"] and partial["winner_id"] is None
    with pytest.raises(ValueError):
        lab.candidate_draft(store, job_id, partial["candidates"][0]["id"])
    count = len(calls)
    result = lab.run_study(store, job_id, request, lambda *a: None)
    assert result["complete"] and len(calls) == count + 4
    store.update_job(job_id, result=result, status="done")
    draft = lab.candidate_draft(store, job_id, result["winner_id"])
    assert draft["plan"]["auto_update"] is False and draft["plan"]["profile"] == "custom"
    assert store.meta("investment_settings") is None and store.meta("portfolio_state") is None
    unchecked = next(r for r in result["candidates"] if r["role"] == "neighbor" and not r.get("check"))
    with pytest.raises(ValueError, match="检验"):
        lab.candidate_draft(store, job_id, unchecked["id"])


def test_compact_does_not_score_missing_years():
    with pytest.raises(ValueError, match="年份不完整"):
        lab.compact({"per_year": [{"year": 2025}]}, 2021, 2025)


def test_tuning_compares_original_plan_but_selects_within_requested_model(tmp_path, fake_replay, monkeypatch):
    _, replay = fake_replay
    store, request, job_id = new_job(tmp_path)
    def superior_baseline(store, plan, *args, **kwargs):
        result = replay(store, plan, *args, **kwargs)
        if plan.method == 'rotation':
            result['annualized_return'] = 100.
        return result
    monkeypatch.setattr(lab, 'simulate', superior_baseline)
    result = lab.run_study(store, job_id, request, lambda *a: None)
    winner = next(r for r in result['candidates'] if r['id'] == result['winner_id'])
    assert winner['plan']['model_recipe'] == request.study.recipe
    assert winner.get('check')
    assert next(r for r in result['candidates'] if r['role'] == 'baseline')['selection']['score'] > winner['selection']['score']


def test_research_api_freezes_plan_does_not_activate_and_requires_fresh_baseline(tmp_path):
    app = create_app(tmp_path, start_manager=False)
    with TestClient(app) as client:
        before = client.get("/api/v2/investment").json()
        context = client.get("/api/v2/research-workspace").json()
        assert context["fields"]["ridge"] == ["years", "horizon", "regularization"]
        payload = {"slot": "active", "base_plan_key": "0" * 24, "study": Study().model_dump()}
        assert client.post("/api/v2/research-workspace/run", json=payload).status_code == 409
        payload["base_plan_key"] = context["plan_key"]
        response = client.post("/api/v2/research-workspace/run", json=payload)
        assert response.status_code == 202, response.text
        job = client.get(f"/api/v2/jobs/{response.json()['id']}").json()
        listing = client.get('/api/v2/jobs?kind=portfolio_research').json()
        assert listing['items'][0]['id'] == job['id']
        assert job["request"]["investment"] == context["settings"]["plan"]
        after = client.get("/api/v2/investment").json()
        assert before["settings"] == after["settings"] and before["account"] == after["account"]
        assert client.post("/api/v2/research-workspace/run", json=payload).status_code == 400


def test_intraday_plan_not_silently_converted_and_full_runtime_used():
    from ai_stock_bot.v2.jobs import external_runtime
    with pytest.raises(ValueError):
        JobRequest(kind="portfolio_research", investment=Plan(research_interval="60m"), study=Study())
    request = JobRequest(kind="portfolio_research", investment=Plan(), study=Study(recipes=["cnn20"]))
    assert external_runtime(request, model_python="gpu-python") == "gpu-python"
