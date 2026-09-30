import datetime as dt
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ai_stock_bot.v2.api import create_app
from ai_stock_bot.v2.backtest import run_backtest
from ai_stock_bot.v2.estimators import probabilities
from ai_stock_bot.v2.execution import Account
from ai_stock_bot.v2.features import features, training_samples
from ai_stock_bot.v2.market import Market
from ai_stock_bot.v2.schema import AppConfig, JobRequest, MODEL_NAMES, Risk
from ai_stock_bot.v2.store import ConflictError, Store


@pytest.fixture
def histories():
    dates = pd.bdate_range("2015-01-01", "2025-12-31")
    result = {}
    for index, symbol in enumerate(["510300.SH", "510500.SH", "512100.SH"]):
        rng = np.random.default_rng(50 + index)
        returns = rng.normal(.00025, .011, len(dates))
        close = (3 + index) * np.exp(np.cumsum(returns))
        opening = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, .002, len(dates)))
        result[symbol] = pd.DataFrame({"open": opening, "high": np.maximum(opening, close) * 1.005, "low": np.minimum(opening, close) * .995, "close": close, "adjusted": close, "volume": rng.integers(100000, 900000, len(dates)), "source": "test fixture"}, index=dates)
    return result


@pytest.fixture
def config():
    c = AppConfig()
    c.symbols = ["510300.SH", "510500.SH"]
    c.training_symbols = ["512100.SH"]
    return c


def test_profile_and_model_settings_survive_restart(tmp_path, config):
    store = Store(tmp_path)
    config.models["random_forest"]["aggressive"].params["n_estimators"] = 137
    config.models["cnn"]["conservative"].training_years = 10
    store.save_settings(config, 1)
    restored = Store(tmp_path).settings()
    assert restored["config"]["models"]["random_forest"]["aggressive"]["params"]["n_estimators"] == 137
    assert restored["config"]["models"]["random_forest"]["balanced"]["params"]["n_estimators"] == 80
    assert restored["config"]["models"]["cnn"]["conservative"]["training_years"] == 10
    with pytest.raises(ConflictError):
        store.save_settings(config, 1)


def test_training_window_never_changes_test_prices(tmp_path, histories, config):
    config.local["balanced"].buy_score = 0
    results = []
    for years in (1, 3, 5, 10):
        config.local["balanced"].training_years = years
        results.append(run_backtest(Store(tmp_path), JobRequest(year=2022, config=config), histories=histories))
    assert len({r["data_hash"] for r in results}) == 1
    assert len({(r["start"], r["end"], r["bars"]) for r in results}) == 1
    assert all(r["start"].startswith("2022") and r["end"].startswith("2022") for r in results)


def test_features_do_not_look_ahead(histories):
    frame = histories["510300.SH"]
    cutoff = pd.Timestamp("2022-06-01")
    original = features(frame).loc[:cutoff]
    changed = frame.copy()
    changed.loc[changed.index > cutoff, "adjusted"] *= 10
    pd.testing.assert_frame_equal(original, features(changed).loc[:cutoff])


def test_recent_market_cache_is_refreshed_even_if_dates_are_covered(tmp_path, histories, monkeypatch):
    from ai_stock_bot.v2 import market as module
    monkeypatch.setattr(module, "today", lambda: dt.date(2025,12,31))
    market = Market(Store(tmp_path))
    market.write("510300.SH", histories["510300.SH"], "fixture")
    with market.store.connect() as c:
        c.execute("UPDATE datasets SET updated=0")
    calls = []
    def download(symbol, start, end):
        calls.append(symbol)
        return histories[symbol]
    monkeypatch.setattr(market, "_yahoo", download)
    market.ensure("510300.SH", dt.date(2024,1,1), dt.date(2025,12,31))
    assert calls == ["510300.SH"]
    market.ensure("510300.SH", dt.date(2024,1,1), dt.date(2025,12,31))
    assert len(calls) == 1


def test_training_samples_are_cut_off_with_complete_labels(histories, config):
    config.engine = "ml"
    config.preset().horizon = 5
    cutoff = pd.Timestamp("2022-01-01")
    x, y, dates, _ = training_samples(histories, config, config.preset(), cutoff, None, "mixed", config.profile)
    altered = {s: f.copy() for s, f in histories.items()}
    for frame in altered.values():
        frame.loc[frame.index >= cutoff, "adjusted"] *= 100
    a, b, c, _ = training_samples(altered, config, config.preset(), cutoff, None, "mixed", config.profile)
    np.testing.assert_array_equal(x, a)
    np.testing.assert_array_equal(y, b)
    np.testing.assert_array_equal(dates, c)
    assert pd.Timestamp(dates[-1]) <= pd.Timestamp("2021-12-24")
    assert pd.Timestamp(dates[-1]) >= pd.Timestamp("2021-01-01")


def test_training_timestamp_units_and_cnn_windows(histories, config):
    config.engine, config.model = "ml", "cnn"
    preset = config.preset()
    preset.params["window"] = 12
    frame = histories["510300.SH"].copy()
    frame.index = frame.index.as_unit("us")
    cutoff = pd.Timestamp("2022-01-01")
    x, y, dates, _ = training_samples({"510300.SH": frame}, config, preset, cutoff, "510300.SH", "self", "balanced")
    last = frame.index.get_loc(pd.Timestamp(dates[-1]))
    np.testing.assert_allclose(x[-1], features(frame).iloc[last - 11:last + 1].to_numpy(dtype=np.float32))
    expected = int(frame.adjusted.iloc[last + 1] / frame.adjusted.iloc[last] - 1 > .001)
    assert y[-1] == expected and pd.Timestamp(dates[-1]).year == 2021


def test_pooled_estimator_reuse_preserves_predictions(tmp_path, histories, config, monkeypatch):
    from ai_stock_bot.v2 import estimators
    config.engine = "ml"
    preset = config.preset()
    preset.training_mode = "mixed"
    preset.params["n_estimators"] = 7
    calls = []
    original = estimators.build_tree
    def build(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(estimators, "build_tree", build)
    workspace = {"samples": {}, "estimators": {}}
    cutoff = pd.Timestamp("2022-01-01")
    first, _ = probabilities(Store(tmp_path / "shared"), histories, config, preset, cutoff, "510300.SH", "balanced", lambda _: None, workspace)
    second, report = probabilities(Store(tmp_path / "shared"), histories, config, preset, cutoff, "510500.SH", "balanced", lambda _: None, workspace)
    assert len(calls) == 1 and report["fit_reused"]
    standalone, _ = probabilities(Store(tmp_path / "separate"), histories, config, preset, cutoff, "510500.SH", "balanced", lambda _: None)
    np.testing.assert_allclose(second, standalone, equal_nan=True)
    assert not np.array_equal(first, second, equal_nan=True)
    changed = preset.model_copy(deep=True)
    changed.params["max_depth"] += 1
    probabilities(Store(tmp_path / "shared"), histories, config, changed, cutoff, "510500.SH", "balanced", lambda _: None, workspace)
    assert len(calls) == 3


def test_orders_execute_on_next_day_open(tmp_path, histories, config):
    config.symbols = ["510300.SH"]
    config.local["balanced"].buy_score = 0
    config.risk.slippage_bps = 0
    config.risk.commission_rate = 0
    result = run_backtest(Store(tmp_path), JobRequest(year=2022, config=config), histories=histories)
    first = result["trades"][0]
    assert first["date"] > result["start"]
    assert first["price"] == histories["510300.SH"].loc[first["date"], "open"]
    assert result["curve"][0]["equity"] == config.risk.starting_cash


def test_daily_loss_block_does_not_prevent_exit():
    risk = Risk(starting_cash=10000, max_trade_cash=10000, max_position_pct=1, max_order_shares=10000, stop_loss_pct=.1, commission_rate=0, slippage_bps=0)
    account = Account(risk)
    account.trade("510300.SH", "buy", 10, "2022-01-01", "test", "balanced")
    account.new_day()
    account.mark("510300.SH", 8)
    assert account.stop_reason("510300.SH", 8) == "止损"
    sell, reason = account.trade("510300.SH", "sell", 8, "2022-01-02", "止损", "balanced", day_start=10000)
    assert sell is not None
    buy, reason = account.trade("510500.SH", "buy", 1, "2022-01-02", "test", "balanced", day_start=10000)
    assert buy is None and "亏损" in reason


def test_t1_available_and_quantity_limits():
    account = Account(Risk())
    trade, _ = account.trade("510300.SH", "buy", 4, "2022-01-01", "test", "balanced", quantity=177)
    assert trade["quantity"] == 100
    sell, _ = account.trade("510300.SH", "sell", 4, "2022-01-01", "test", "balanced")
    assert sell is None
    account.new_day()
    assert account.trade("510300.SH", "sell", 4, "2022-01-02", "test", "balanced")[0]


def test_restored_account_does_not_unlock_same_day_shares():
    account = Account(Risk())
    account.trade("510300.SH", "buy", 4, "2022-01-01", "paper", "balanced")
    restored = Account(Risk(), account.dump())
    restored.new_day("2022-01-01")
    assert restored.trade("510300.SH", "sell", 4, "2022-01-01", "manual", "balanced")[0] is None
    restored.new_day("2022-01-02")
    assert restored.trade("510300.SH", "sell", 4, "2022-01-02", "manual", "balanced")[0]


@pytest.mark.parametrize("model", list(MODEL_NAMES))
def test_all_models_train_and_predict_on_cpu(tmp_path, histories, config, model):
    config.engine, config.model = "ml", model
    preset = config.preset()
    preset.training_mode = "mixed"
    preset.training_years = 1
    preset.min_samples = 30
    if model == "random_forest":
        preset.params["n_estimators"] = 5
    elif model == "hist_gradient_boosting":
        preset.params["max_iter"] = 5
    elif model == "xgboost":
        preset.params["n_estimators"], preset.params["device"] = 5, "cpu"
    else:
        preset.params["epochs"], preset.params["device"] = 2, "cpu"
    result, report = probabilities(Store(tmp_path), histories, config, preset, pd.Timestamp("2022-01-01"), "510300.SH", config.profile, lambda msg: None)
    assert result is not None
    valid = result[np.isfinite(result).all(axis=1)]
    np.testing.assert_allclose(valid.sum(axis=1), 1, atol=1e-5)
    assert report["samples"] >= preset.min_samples
    if model in {"mlp", "cnn"}:
        assert report["validation_samples"] > 0 and report["device"] == "cpu"


def test_full_five_class_probability_mapping(tmp_path, histories, config):
    config.engine = "ml"
    config.preset().params["n_estimators"] = 5
    config.preset().target = "trend5"
    result, _ = probabilities(Store(tmp_path), histories, config, config.preset(), pd.Timestamp("2022-01-01"), "510300.SH", "balanced", lambda msg: None)
    assert result.shape[1] == 5
    np.testing.assert_allclose(result[np.isfinite(result).all(axis=1)].sum(axis=1), 1)


def test_cached_predictions_are_reused_for_threshold_change(tmp_path, histories, config):
    config.engine = "ml"
    config.preset().params["n_estimators"] = 5
    store = Store(tmp_path)
    cutoff = pd.Timestamp("2022-01-01")
    first, _ = probabilities(store, histories, config, config.preset(), cutoff, "510300.SH", "balanced", lambda msg: None)
    config.preset().buy_threshold = .8
    second, report = probabilities(store, histories, config, config.preset(), cutoff, "510300.SH", "balanced", lambda msg: None)
    assert report["cached"]
    np.testing.assert_array_equal(first, second)


def test_partial_year_is_not_silently_substituted(tmp_path, histories, config):
    short = {s: f.loc["2022-06-01":"2022-06-18"] for s, f in histories.items()}
    with pytest.raises(ValueError, match="完整"):
        run_backtest(Store(tmp_path), JobRequest(year=2022, config=config), histories=short)


def test_ai_fallback_count_matches_actual_decisions(tmp_path, histories, config, monkeypatch):
    from ai_stock_bot.v2 import backtest as module
    config.engine = "ai"
    monkeypatch.setattr(module, "today", lambda:dt.date(2026,1,1))
    def fake_ai(cfg, snapshots, **kwargs):
        return {symbol:{"side":"hold","probability":.5,"profile":cfg.profile,"scene":"sideways","reason":"mock fallback","fallback":True,"score":0} for symbol,_,_ in snapshots}
    monkeypatch.setattr(module, "ai_decisions", fake_ai)
    result = run_backtest(Store(tmp_path), JobRequest(period="random14", config=config), histories=histories)
    assert result["fallback_count"] == len(result["decisions"]) == 28


def test_api_settings_versions_and_named_plan(tmp_path, config):
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        current = client.get("/api/v2/settings").json()
        payload = {"config": config.model_dump(), "revision": current["revision"]}
        assert client.put("/api/v2/settings", json=payload).status_code == 200
        assert client.put("/api/v2/settings", json=payload).status_code == 409
        plan = client.post("/api/v2/plans", json=config.model_dump()).json()
        assert client.get(f"/api/v2/plans/{plan['id']}").json()["config"] == config.model_dump()
        config.engine = "ai"
        assert client.post("/api/v2/jobs", json=JobRequest(config=config).model_dump()).status_code == 400


def test_import_validation_is_non_mutating_and_blocks_cross_site_writes(tmp_path, config):
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        assert client.post("/api/v2/settings/validate", json=config.model_dump()).status_code == 200
        numeric = config.model_dump()
        numeric["models"]["random_forest"]["balanced"]["params"]["max_depth"] = 5.0
        normalized = client.post("/api/v2/settings/validate", json=numeric).json()
        assert isinstance(normalized["config"]["models"]["random_forest"]["balanced"]["params"]["max_depth"], int)
        assert client.get("/api/v2/settings").json()["revision"] == 1
        invalid = config.model_dump()
        invalid["models"]["cnn"]["balanced"]["params"]["kernel_size"] = 3.5
        assert client.post("/api/v2/settings/validate", json=invalid).status_code == 422
        assert client.post("/api/v2/settings/validate", json={}, headers={"Origin":"http://evil.example"}).status_code == 403


def test_status_is_lightweight_and_tracks_result_changes(tmp_path, config):
    store = Store(tmp_path)
    job = store.create_job(JobRequest(config=config).model_dump())
    assert not job["has_result"] and job["result_version"] == 0
    store.update_job(job["id"], result={"lots": "x" * 10000})
    status = store.job(job["id"])
    assert status["has_result"] and status["result_version"] > 0
    assert "result" not in status and "request" not in status


def test_remote_access_is_read_only_until_authenticated(tmp_path, config):
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        key = client.get("/api/v2/access/key").json()["key"]
        headers = {"CF-Connecting-IP": "203.0.113.5"}
        assert not client.get("/api/v2/access", headers=headers).json()["write_allowed"]
        assert client.get("/api/v2/access/key", headers=headers).status_code == 403
        assert client.put("/api/v2/settings", json={"config":config.model_dump(),"revision":1}, headers=headers).status_code == 401
        assert client.post("/api/v2/access/login", json={"key":"wrong"}, headers=headers).status_code == 401
        assert client.post("/api/v2/access/login", json={"key":key}, headers=headers).status_code == 200
        assert client.get("/api/v2/access", headers=headers).json()["write_allowed"]
        assert client.put("/api/v2/settings", json={"config":config.model_dump(),"revision":1}, headers=headers).status_code == 200


@pytest.mark.parametrize('host', ['attacker.example', 'localhost.attacker.example', 'stock.autoaistock.top'])
def test_loopback_connection_with_external_host_never_gets_local_privileges(tmp_path, config, host):
    with TestClient(create_app(tmp_path, start_manager=False), client=('127.0.0.1', 12345),
                    base_url='http://localhost') as client:
        key_response = client.get('/api/v2/access/key')
        key = key_response.json()['key']
        assert key_response.headers['cache-control'] == 'no-store'
        headers = {'host': host, 'origin': f'http://{host}'}
        assert client.get('/api/v2/access/key', headers=headers).status_code == 403
        assert client.get('/api/v2/access', headers=headers).json() == {'remote': True, 'write_allowed': False}
        assert client.put('/api/v2/settings', headers=headers,
                          json={'config': config.model_dump(), 'revision': 1}).status_code == 401
        assert client.post('/api/v2/access/login', headers=headers, json={'key': key}).status_code == 200
        assert client.get('/api/v2/access', headers=headers).json()['write_allowed']


@pytest.mark.parametrize('host', ['localhost', '127.0.0.1', '[::1]'])
def test_loopback_hosts_keep_local_access(tmp_path, host):
    with TestClient(create_app(tmp_path, start_manager=False), client=('127.0.0.1', 12345),
                    base_url=f'http://{host}') as client:
        assert client.get('/api/v2/access').json() == {'remote': False, 'write_allowed': True}


def test_codex_adapter_uses_correct_arguments_and_per_symbol_profiles(monkeypatch, histories, config, tmp_path):
    import json
    from types import SimpleNamespace
    from ai_stock_bot import ai, config as legacy_config
    from ai_stock_bot.v2.strategies import ai_decisions
    binary = tmp_path / "codex.exe"
    binary.touch()
    settings = SimpleNamespace(ai_provider="codex", openai_api_key="", codex_bin=str(binary), codex_model="test-model", codex_timeout_seconds=91)
    monkeypatch.setattr(legacy_config, "load_settings", lambda: settings)
    calls = []
    def fake_run(command, **kwargs):
        assert command[command.index("--model") + 1] == "test-model"
        assert kwargs["timeout"] == 91
        calls.append(kwargs["input"])
        items = json.loads(kwargs["input"].splitlines()[-1])
        payload = {"signals":[{"symbol":r["symbol"],"side":"hold","confidence":.5,"reason":"AI smoke"} for r in items]}
        return SimpleNamespace(returncode=0, stdout=json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(payload)}}), stderr="")
    monkeypatch.setattr(ai.subprocess, "run", fake_run)
    config.engine, config.auto_profile = "ai", True
    rows = []
    for symbol, scene in zip(histories, ("bull", "bear", "sideways")):
        row = features(histories[symbol]).iloc[-1].copy()
        row.momentum20 = .1 if scene == "bull" else -.1 if scene == "bear" else 0.
        row.trend, row.drawdown = .02, -.01
        rows.append((symbol, 5, row))
    result = ai_decisions(config, rows)
    assert [result[s]["profile"] for s in histories] == ["aggressive", "conservative", "balanced"]
    assert len(calls) == 3 and all(r["side"] == "hold" for r in result.values())
    assert config.ai["aggressive"].ai_prompt in calls[0]


def test_incomplete_candidates_cannot_be_applied(tmp_path):
    from ai_stock_bot.v2.learning import summarize
    trials = [{"profile": "aggressive", "score": .9, "complete": False, "round": 1}, {"profile": "aggressive", "score": .1, "complete": True, "round": 1}]
    assert summarize(trials)["best"]["aggressive"]["score"] == .1


def test_learning_history_is_paged_and_champions_ignore_incomplete_trials(tmp_path, config):
    store = Store(tmp_path)
    job = store.create_job(JobRequest(kind="learn", config=config).model_dump())
    for index in range(40):
        row = {"profile":"aggressive","score":index / 100,"complete":index != 39,"round":index // 3 + 1,"label":f"candidate {index}","config":config.model_dump(),"fingerprint":str(index)}
        store.save_trial(job["id"], str(index), row)
    report = store.learning_summary(job["id"])
    assert report["total"] == 40 and report["best"]["aggressive"]["label"] == "candidate 38"
    assert "trials" not in report
    assert store.learning_summary(job["id"], before_round=2)["best"]["aggressive"]["label"] == "candidate 2"
    first, last = store.trial_page(job["id"], 15), store.trial_page(job["id"], 15, 30)
    assert first["total"] == 40 and len(first["items"]) == 15 and len(last["items"]) == 10
    assert all("config" not in item for item in first["items"])


def test_apply_suggestion_saves_only_the_target_model_and_profile(tmp_path, config):
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        store = client.app.state.store
        before = store.settings()["config"]
        config.engine = "ml"
        config.models["random_forest"]["aggressive"].buy_threshold = .61
        job = store.create_job(JobRequest(kind="learn", config=config).model_dump())
        store.update_job(job["id"], result={"best":{"aggressive":{"complete":True,"config":config.model_dump()}}})
        applied = client.post(f"/api/v2/jobs/{job['id']}/apply/aggressive").json()
        assert applied["revision"] == 2 and applied["config"]["profile"] == "aggressive"
        assert applied["config"]["models"]["random_forest"]["aggressive"]["buy_threshold"] == .61
        assert applied["config"]["models"]["cnn"] == before["models"]["cnn"]
        assert applied["config"]["models"]["random_forest"]["balanced"] == before["models"]["random_forest"]["balanced"]
        assert client.get("/api/v2/settings").json() == applied


def test_running_job_does_not_lock_page_and_can_stop(tmp_path, config):
    with TestClient(create_app(tmp_path)) as client:
        submitted = client.post("/api/v2/jobs", json=JobRequest(kind="refresh", config=config).model_dump())
        assert submitted.status_code == 202
        job_id = submitted.json()["id"]
        started = time.monotonic()
        assert client.get("/api/v2/overview").status_code == 200
        assert time.monotonic() - started < 1
        assert client.post(f"/api/v2/jobs/{job_id}/stop").json()["status"] == "stopped"
        assert client.get(f"/api/v2/jobs/{job_id}").json()["request"]["config"] == config.model_dump()


def test_import_legacy_is_read_only_and_one_time(tmp_path):
    import json
    import sqlite3
    legacy = tmp_path / "old.sqlite"
    with sqlite3.connect(legacy) as c:
        c.execute("CREATE TABLE app_state(key TEXT,value_json TEXT)")
        c.execute("INSERT INTO app_state VALUES('latest',?)", (json.dumps({"config": {"max_trade_cash": 9876}, "account": {"cash": 12345}}),))
    original = legacy.read_bytes()
    store = Store(tmp_path / "new")
    store.import_legacy(legacy)
    assert legacy.read_bytes() == original
    assert store.settings()["config"]["risk"]["max_trade_cash"] == 9876
    assert store.meta("account")["cash"] == 12345
    store.import_legacy(legacy)
    assert store.settings()["revision"] == 1
