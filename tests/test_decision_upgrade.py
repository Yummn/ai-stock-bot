import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from ai_stock_bot.v2.execution import Account
from ai_stock_bot.v2.features import features, label_returns, online_regimes, training_samples
from ai_stock_bot.v2.schema import AppConfig
from ai_stock_bot.v2.strategies import ai_decisions, decide


def frame():
    dates = pd.bdate_range("2020-01-01", periods=110)
    close = 10 + np.sin(np.arange(110) / 3)
    return pd.DataFrame(dict(open=close * 1.02, close=close, adjusted=close, volume=np.full(110, 1000)), index=dates)


def test_net_label_matches_actual_round_trip():
    config = AppConfig(engine="ml")
    preset = config.preset()
    preset.label_basis = "next_open_net"
    preset.horizon = 3
    data = frame()
    r = label_returns(data, preset, config.risk)
    account = Account(config.risk)
    buy, _ = account.trade("510300.SH", "buy", data.open.iloc[51], "2021-01-01", "test", "balanced", quantity=100)
    sell, _ = account.trade("510300.SH", "sell", data.open.iloc[54], "2021-01-05", "test", "balanced", quantity=100)
    cost = buy["price"] * 100 + buy["fee"]
    received = sell["price"] * 100 - sell["fee"]
    assert r.iloc[50] == pytest.approx(received / cost - 1)
    assert r.iloc[-4:].isna().all()


def test_net_label_ignores_same_day_close_and_rejects_bad_opens():
    config = AppConfig(engine="ml")
    preset = config.preset()
    preset.label_basis = "next_open_net"
    data = frame()
    before = label_returns(data, preset, config.risk)
    data["adjusted"] *= 100
    pd.testing.assert_series_equal(before, label_returns(data, preset, config.risk))
    data.iloc[51, data.columns.get_loc("open")] = 0
    assert pd.isna(label_returns(data, preset, config.risk).iloc[50])


def test_net_training_excludes_unfinished_labels():
    config = AppConfig(engine="ml")
    config.preset().label_basis = "next_open_net"
    config.preset().horizon = 5
    data = frame()
    cutoff = data.index[90]
    first = training_samples({"510300.SH": data}, config, config.preset(), cutoff, None, "mixed", "balanced")
    data.loc[cutoff:, "open"] *= 100
    second = training_samples({"510300.SH": data}, config, config.preset(), cutoff, None, "mixed", "balanced")
    for a, b in zip(first[:3], second[:3]):
        np.testing.assert_array_equal(a, b)
    assert pd.Timestamp(first[2][-1]) == data.index[83]


def mock_codex(monkeypatch, tmp_path, payload=None, exit_code=0):
    from ai_stock_bot import ai, config as legacy_config
    binary = tmp_path / "codex.exe"
    binary.touch()
    monkeypatch.setattr(legacy_config, "load_settings", lambda: SimpleNamespace(ai_provider="codex", codex_bin=str(binary), codex_model="test", codex_timeout_seconds=10))
    prompts = []

    def run(command, **kwargs):
        prompts.append(kwargs["input"])
        return SimpleNamespace(returncode=exit_code, stderr="private error text", stdout=json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(payload)}}))
    monkeypatch.setattr(ai.subprocess, "run", run)
    return prompts


@pytest.mark.parametrize("payload", [
    {"signals": [{"symbol": "510300.SH", "side": "buy", "confidence": 1.2, "reason": "bad"}]},
    {"signals": [{"symbol": "510300.SH", "side": "buy", "confidence": float("nan"), "reason": "bad"}]},
    {"signals": [{"symbol": "510300.SH", "side": "short", "confidence": .8, "reason": "bad"}]},
    {"signals": [{"symbol": "unknown", "side": "buy", "confidence": .8, "reason": "bad"}]},
    {"signals": []},
    {},
])
def test_invalid_ai_is_safe_hold(monkeypatch, tmp_path, payload):
    mock_codex(monkeypatch, tmp_path, payload)
    result = ai_decisions(AppConfig(engine="ai"), [("510300.SH", 10, features(frame()).iloc[-1])])["510300.SH"]
    assert result["side"] == "hold"
    assert result["fallback"] and result["source"] == "safe_hold"
    assert result["confidence"] is None and result["opportunity_score"] is None
    assert "private error text" not in json.dumps(result)


def test_ai_context_audit_and_confidence(monkeypatch, tmp_path):
    payload = {"signals": [{"symbol": "510300.SH", "side": "sell", "confidence": .9, "reason": "lower exposure"}]}
    prompts = mock_codex(monkeypatch, tmp_path, payload)
    config = AppConfig(engine="ai", auto_profile=True)
    account = Account(config.risk)
    account.trade("510300.SH", "buy", 10, "2021-01-01", "test", "balanced", quantity=100)
    context = account.decision_context("2021-01-02")
    row = features(frame()).iloc[-1]
    result = ai_decisions(config, [("510300.SH", 10, row)], context, "2021-01-02", ml_references={"510300.SH": {"status":"partial", "references":[{"status":"ok", "model":"random_forest", "profile":"balanced", "side":"sell"}]}})["510300.SH"]
    assert str(context["cash"]) in prompts[0] and '"average_price"' in prompts[0]
    assert result["source"] == "codex" and not result["fallback"]
    assert result["confidence"] == .9 and result["opportunity_score"] is None
    assert result["scene"] == online_regimes(pd.DataFrame([row]))[0]
    assert result["audit"]["input"]["account"] == context
    assert len(result["audit"]["prompt_hash"]) == 64


def test_ai_failure_still_allows_independent_stop(monkeypatch, tmp_path):
    mock_codex(monkeypatch, tmp_path, exit_code=1)
    config = AppConfig(engine="ai")
    account = Account(config.risk)
    account.trade("510300.SH", "buy", 10, "2021-01-01", "test", "balanced", quantity=100)
    result = ai_decisions(config, [("510300.SH", 7, features(frame()).iloc[-1])])["510300.SH"]
    assert result["side"] == "hold" and result["fallback"]
    assert account.stop_reason("510300.SH", 7) == "止损"


def test_ml_score_is_not_action_confidence():
    result = decide(features(frame()).iloc[-1], np.array([.1, .9]), AppConfig(engine="ml"), "balanced", "sideways")
    assert result["opportunity_score"] == .9 and result["confidence"] is None
    assert result["source"] == "random_forest"


def test_nonfinite_prediction_is_labeled_as_local_fallback():
    result = decide(features(frame()).iloc[-1], np.array([np.nan, np.nan]), AppConfig(engine="ml"), "balanced", "sideways")
    assert result["source"] == "local" and result["fallback"]
    assert result["opportunity_score"] is None


def test_old_configs_and_default_snapshots_preserve_label_meaning():
    config = AppConfig().model_dump()
    for presets in config["models"].values():
        for preset in presets.values():
            preset.pop("label_basis")
    restored = AppConfig.model_validate(config)
    assert all(p.label_basis == "close_return" for group in restored.models.values() for p in group.values())


def test_neural_annual_process_returns_result_and_cleans_up(tmp_path):
    from ai_stock_bot.v2.isolated import run_backtest
    from ai_stock_bot.v2.schema import JobRequest
    from ai_stock_bot.v2.store import Store
    dates = pd.bdate_range("2019-01-01", "2020-12-31")
    close = 10 + np.sin(np.arange(len(dates)) / 4)
    data = pd.DataFrame(dict(open=close, high=close * 1.01, low=close * .99, close=close, adjusted=close, volume=1000, source="fixture"), index=dates)
    config = AppConfig(engine="ml", model="mlp", symbols=["510300.SH"], training_symbols=[])
    config.preset().params.update(epochs=2, layer_size=8, layer_count=1, device="cpu")
    result = run_backtest(Store(tmp_path), JobRequest(year=2020, config=config), histories={"510300.SH": data})
    assert result["fallback_count"] == 0 and result["year"] == 2020
    assert not list((tmp_path / "workers").iterdir())


def _exit_worker(*args):
    import os
    os._exit(42)


def test_native_failure_retries_once_and_never_returns_partial_success(tmp_path, monkeypatch):
    from ai_stock_bot.v2 import isolated
    from ai_stock_bot.v2.schema import JobRequest
    from ai_stock_bot.v2.store import Store
    monkeypatch.setattr(isolated, "_annual_worker", _exit_worker)
    messages = []
    with pytest.raises(RuntimeError, match="42"):
        isolated.run_backtest(Store(tmp_path), JobRequest(config=AppConfig(engine="ml", model="cnn")), progress=lambda _, m: messages.append(m))
    assert len(messages) == 1
    assert not list((tmp_path / "workers").iterdir())
