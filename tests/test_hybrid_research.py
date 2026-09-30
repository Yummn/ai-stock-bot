import copy
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ai_stock_bot.v2.api import create_app
from ai_stock_bot.v2.backtest import load_histories
from ai_stock_bot.v2.reference import MLReferences
from ai_stock_bot.v2.research import validate_report, run_research, Source
from ai_stock_bot.v2.schema import AppConfig
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v2.strategies import ai_decisions
from test_decision_upgrade import frame, mock_codex
from ai_stock_bot.v2.features import features


@pytest.mark.parametrize("ai_side, alignment", [("buy", "agree"), ("hold", "override"), ("sell", "override")])
def test_ai_receives_reference_and_can_disagree(tmp_path, monkeypatch, ai_side, alignment):
    prompts = mock_codex(monkeypatch, tmp_path, {"signals": [{"symbol": "510300.SH", "side": ai_side, "confidence": .7, "reason": "reviewed ML against risk"}]})
    ref = {"status": "partial", "summary": {"available": 1}, "references": [{"status": "ok", "model": "random_forest", "profile": "balanced", "side": "buy", "opportunity_score": .82, "training_cutoff": "2021-01-01"}]}
    result = ai_decisions(AppConfig(engine="ai"), [("510300.SH", 10, features(frame()).iloc[-1])], as_of="2021-01-02", ml_references={"510300.SH": ref})["510300.SH"]
    assert result["side"] == ai_side and result["reference_alignment"] == alignment
    assert result["ml_reference"] == ref
    assert '"opportunity_score": 0.82' in prompts[0]


def test_missing_required_reference_blocks_ai_buy(tmp_path, monkeypatch):
    mock_codex(monkeypatch, tmp_path, {"signals": [{"symbol":"510300.SH", "side":"buy", "confidence":.99, "reason":"test"}]})
    result = ai_decisions(AppConfig(engine="ai"), [("510300.SH", 10, features(frame()).iloc[-1])])["510300.SH"]
    assert result["ai_proposed_side"] == "buy"
    assert result["side"] == "hold" and result["fallback"] and result["source"] == "safe_hold"


def test_partial_reference_keeps_ai_decision(tmp_path, monkeypatch):
    mock_codex(monkeypatch, tmp_path, {"signals": [{"symbol":"510300.SH", "side":"buy", "confidence":.7, "reason":"test"}]})
    config = AppConfig(engine="ai")
    evidence = {"status": "partial", "references": [{"status": "unavailable"}, {"status": "ok", "side": "hold"}]}
    result = ai_decisions(config, [("510300.SH", 10, features(frame()).iloc[-1])], ml_references={"510300.SH": evidence})["510300.SH"]
    assert result["side"] == "buy" and result["reference_alignment"] == "override"


def test_reference_fits_fifteen_profiles_once_and_uses_own_presets(tmp_path, monkeypatch):
    from ai_stock_bot.v2 import reference
    config = AppConfig(engine="ai", profile="aggressive", symbols=["510300.SH"])
    config.models["xgboost"]["aggressive"].buy_threshold = .93
    dates = frame().index
    cutoff = dates[80]
    calls = []
    def predict(store, histories, cfg, preset, observed_cutoff, symbol, profile, *args, **kwargs):
        calls.append((cfg.model, profile, observed_cutoff))
        assert cfg.engine == "ml" and profile != "custom"
        if (cfg.model, profile) == ("xgboost", "aggressive"):
            assert preset.buy_threshold == .93
        return np.tile([.2, .8], (len(dates), 1)), {"mode":"mixed", "samples":1000}
    monkeypatch.setattr(reference, "probabilities", predict)
    refs = MLReferences(Store(tmp_path), {"510300.SH":frame()}, config, cutoff, lambda m:None)
    first = refs.at("510300.SH", dates[81], "sideways")
    second = refs.at("510300.SH", dates[82], "sideways")
    assert len(calls) == 15 and all(call[2] == cutoff for call in calls)
    assert len(set((model, profile) for model, profile, _ in calls)) == 15
    assert first["status"] == "ok" and len(first["references"]) == 15
    assert first["references"][0]["opportunity_score"] == .8
    assert second["summary"]["available"] == 15
    assert config.engine == "ai"


def test_reference_failure_is_not_local_prediction(tmp_path, monkeypatch):
    from ai_stock_bot.v2 import reference
    monkeypatch.setattr(reference, "probabilities", lambda *a, **k: (None, {"mode":"local_fallback", "samples":2}))
    refs = MLReferences(Store(tmp_path), {"510300.SH":frame()}, AppConfig(engine="ai"), frame().index[-1], lambda m:None)
    result = refs.at("510300.SH", frame().index[-2], "sideways")
    assert result["status"] == "unavailable" and result["summary"]["available"] == 0
    assert all("opportunity_score" not in entry for entry in result["references"])


def test_one_model_failure_preserves_other_references(tmp_path, monkeypatch):
    from ai_stock_bot.v2 import reference

    dates = frame().index
    def predict(store, histories, cfg, preset, cutoff, symbol, profile, *args, **kwargs):
        if cfg.model == "cnn":
            raise RuntimeError("training failed")
        return np.tile([.4, .6], (len(dates), 1)), {"mode": "mixed", "samples": 200}
    monkeypatch.setattr(reference, "probabilities", predict)
    refs = MLReferences(Store(tmp_path), {"510300.SH": frame()}, AppConfig(engine="ai"), dates[80], lambda m: None)
    result = refs.at("510300.SH", dates[81], "sideways")
    assert result["status"] == "partial"
    assert result["summary"]["available"] == 12 and result["summary"]["unavailable"] == 3
    assert all(row["status"] == "unavailable" for row in result["references"] if row["model"] == "cnn")
    assert all(row["status"] == "ok" for row in result["references"] if row["model"] != "cnn")


def test_ai_loads_reference_training_pool_and_window():
    config = AppConfig(engine="ai", symbols=["510300.SH"], training_symbols=["510500.SH"])
    config.models["cnn"]["conservative"].training_years = 10
    calls = []
    def ensure(symbol, start, end, notify):
        calls.append((symbol, start))
        return frame()
    load_histories(SimpleNamespace(ensure=ensure), config, dt.date(2025,1,1), dt.date(2025,12,31), lambda m:None)
    assert len(calls) == 2 and all(start.year == 2014 for _, start in calls)


def test_paper_uses_signal_date_as_training_cutoff(tmp_path, monkeypatch):
    from ai_stock_bot.v2 import paper, reference

    history = frame()
    observed = {}
    monkeypatch.setattr(paper, "load_histories", lambda *args: ({"510300.SH": history}, []))
    class FakeReferences:
        def __init__(self, store, histories, config, cutoff, progress, symbols):
            observed["cutoff"] = cutoff
        def at(self, symbol, date, scene):
            return {"status": "partial", "references": [{"status": "ok", "side": "hold"}]}
    monkeypatch.setattr(reference, "MLReferences", FakeReferences)
    monkeypatch.setattr(paper, "ai_decisions", lambda *args, **kwargs: {"510300.SH": {"symbol": "510300.SH", "side": "hold", "rank_score": 0., "fallback": False, "profile": "balanced", "reason": "test"}})
    paper.run_paper(Store(tmp_path), AppConfig(engine="ai", symbols=["510300.SH"], training_symbols=[]), lambda *args: None)
    assert observed["cutoff"] == history.index[-2]


def payload():
    return dict(summary="近期报道概括", market_outlook="条件性推断", topics=[dict(name="公开市场", evidence="公告事实", analysis="可能影响", risks="不确定", related_symbols=["510300.SH"], source_ids=["one"])], sources=[dict(id="one",title="公告一",url="https://www.sse.com.cn/notice",publisher="交易所",published_at="2026-09-27"),dict(id="two",title="公告二",url="https://www.pbc.gov.cn/news",publisher="央行",published_at=None)], uncertainties=["日期不明的资料只作背景"])


def test_research_requires_real_search_and_consistent_sources():
    now = dt.datetime(2026,9,28, tzinfo=dt.timezone.utc)
    with pytest.raises(ValueError, match="实际联网"):
        validate_report(payload(), [], now, ["510300.SH"])
    result = validate_report(payload(), [{"type":"web_search"}], now, ["510300.SH"])
    assert result["status"] == "ok" and result["topics"][0]["current_evidence"]
    bad = payload()
    bad["topics"][0]["source_ids"] = ["invented"]
    with pytest.raises(ValueError):
        validate_report(bad, [1], now, ["510300.SH"])
    bad = payload()
    bad["sources"][0]["published_at"] = "2027-01-01"
    with pytest.raises(ValueError, match="晚于"):
        validate_report(bad, [1], now, ["510300.SH"])


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://127.0.0.1:8768", "https://localhost/a", "http://192.168.1.1/", "https://user:secret@example.com"])
def test_research_rejects_unsafe_links(url):
    source = payload()["sources"][0]
    source["url"] = url
    with pytest.raises(ValueError):
        Source.model_validate(source)


def test_undated_research_is_not_claimed_fresh():
    raw = payload()
    for source in raw["sources"]:
        source["published_at"] = None
    result = validate_report(raw, [1], dt.datetime.now(dt.timezone.utc), ["510300.SH"])
    assert result["status"] == "limited" and not result["topics"][0]["current_evidence"]


def test_failed_research_keeps_last_report(tmp_path, monkeypatch):
    from ai_stock_bot.v2 import ai_transport
    store = Store(tmp_path)
    store.set_meta("market_research", {"summary":"previous"})
    monkeypatch.setattr(ai_transport, "codex_json", lambda *a, **k: (payload(), []))
    from ai_stock_bot import config as legacy
    monkeypatch.setattr(legacy, "load_settings", lambda: SimpleNamespace(ai_provider="codex", codex_model=None))
    with pytest.raises(ValueError):
        run_research(store, AppConfig(), lambda *a:None)
    assert store.meta("market_research")["summary"] == "previous"


def test_research_api_persists_and_requires_remote_write_access(tmp_path):
    with TestClient(create_app(tmp_path, start_manager=False)) as client:
        assert "market_research" in client.get("/api/v2/health").json()["capabilities"]
        assert client.get("/api/v2/research").json()["report"] is None
        assert client.post("/api/v2/jobs", json={"kind":"research"}, headers={"cf-connecting-ip":"8.8.8.8"}).status_code == 401
        assert client.post("/api/v2/jobs", json={"kind":"research"}).status_code == 202
        assert client.post("/api/v2/jobs", json={"kind":"research"}).status_code == 400
        assert client.get("/api/v2/jobs?kind=research").json()["total"] == 1


def test_signal_transport_disables_web_and_local_tools(tmp_path, monkeypatch):
    from ai_stock_bot.v2.ai_transport import codex_json
    import subprocess
    binary = tmp_path / "codex.exe"
    binary.touch()
    def run(command, **kwargs):
        assert 'web_search="disabled"' in command and "shell_tool" in command and "plugins" in command
        assert kwargs["cwd"] != str(Path.cwd())
        return SimpleNamespace(returncode=0, stdout=json.dumps({"type":"item.completed", "item":{"type":"agent_message", "text":'{"signals":[]}'}}))
    monkeypatch.setattr(subprocess, "run", run)
    codex_json(SimpleNamespace(codex_bin=str(binary), codex_model=None, codex_timeout_seconds=20), "test")


def test_openai_research_uses_web_tool_not_plain_completion(tmp_path, monkeypatch):
    import openai
    from ai_stock_bot import config as legacy
    raw = payload()
    raw["sources"][0]["published_at"] = dt.date.today().isoformat()
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(raw), output=[SimpleNamespace(type="web_search_call", model_dump=lambda: {"type":"web_search_call"})])
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)))
    monkeypatch.setattr(legacy, "load_settings", lambda: SimpleNamespace(ai_provider="openai", openai_api_key="test", openai_model="test-model"))
    store = Store(tmp_path)
    result = run_research(store, AppConfig(), lambda *a:None)
    assert calls[0]["tools"] == [{"type":"web_search"}]
    assert result["provider"] == "openai" and store.meta("market_research") == result
    assert result["historical_execution"] is False


def test_legacy_single_reference_settings_are_removed_on_load(tmp_path):
    config = AppConfig(engine="ai")
    config.models["cnn"]["aggressive"].buy_threshold = .81
    store = Store(tmp_path)
    store.save_settings(config, 1)
    legacy = store.settings()["config"]
    legacy["ai"]["aggressive"].update(reference_model="cnn", use_ml_reference=False)
    store.set_meta("config", legacy)
    loaded = Store(tmp_path).settings()["config"]
    assert "reference_model" not in loaded["ai"]["aggressive"]
    assert "use_ml_reference" not in loaded["ai"]["aggressive"]
    assert loaded["models"]["cnn"]["aggressive"]["buy_threshold"] == .81


def test_stop_cleans_up_owned_worker_descendants():
    import os
    import subprocess
    import sys
    import psutil
    from ai_stock_bot.v2.processes import terminate_worker_tree
    # Only fresh test-owned processes are touched; no application servers.
    code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"
    process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        child_pid = int(process.stdout.readline().strip())
        terminate_worker_tree(process.pid)
        process.wait(timeout=8)
        assert not psutil.pid_exists(child_pid)
        with pytest.raises(ValueError):
            terminate_worker_tree(os.getpid())
    finally:
        if process.poll() is None:
            terminate_worker_tree(process.pid)
        process.wait(timeout=8)
        process.stdout.close()
