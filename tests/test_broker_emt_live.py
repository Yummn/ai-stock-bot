import ast
import importlib.util
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ai_stock_bot.v2.api import create_app
from ai_stock_bot.v4 import broker_emt, broker_emt_live, routes


PROFILE = {
    "environment": "live_readonly", "broker_approved": True,
    "account": "600000000001", "host": "61.152.230.42", "port": 19089,
}


def write_profile(tmp_path, monkeypatch, changes=None):
    path = tmp_path / "emt-live-readonly.json"
    path.write_text(json.dumps({**PROFILE, **(changes or {})}), encoding="utf-8")
    monkeypatch.setattr(broker_emt_live, "CONFIG_FILE", path)
    return path


def test_live_readiness_is_unconfigured_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(broker_emt_live, "CONFIG_FILE", tmp_path / "absent.json")
    result = broker_emt_live.readiness()
    assert result["configured"] is False
    assert result["orders_enabled"] is False
    assert result["account_masked"] is None


@pytest.mark.parametrize("changes", [
    {"account": broker_emt.SIMULATION_ORDINARY_ACCOUNT},
    {"host": broker_emt.SIMULATION_IP},
    {"host": "127.0.0.1"},
    {"broker_approved": False},
    {"password": "should-not-be-saved"},
    {"port": 0},
])
def test_live_profile_rejects_unsafe_configuration(tmp_path, monkeypatch, changes):
    write_profile(tmp_path, monkeypatch, changes)
    with pytest.raises(ValueError):
        broker_emt_live._profile()
    assert broker_emt_live.readiness()["configured"] is False


def test_live_readiness_masks_account(tmp_path, monkeypatch):
    write_profile(tmp_path, monkeypatch)
    result = broker_emt_live.readiness()
    assert result["configured"] is True
    assert result["account_masked"] == "********0001"
    assert result["orders_enabled"] is False


def test_live_snapshot_uses_separate_worker_and_no_password(tmp_path, monkeypatch):
    write_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(broker_emt, "available", lambda: True)
    monkeypatch.setenv("EMT_TEST_PASSWORD", "test-secret")
    monkeypatch.setenv("EMT_LIVE_PASSWORD", "live-secret")
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs["env"]
        seen["prefix"] = kwargs["env"]["EMT_PROBE_LOG_PREFIX"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "environment": "live_readonly", "account_masked": "********0001",
            "orders_enabled": False, "equity": 1000, "cash": 800,
            "positions": [], "open_orders": [],
        }))

    monkeypatch.setattr(broker_emt_live.subprocess, "run", fake_run)
    response = broker_emt_live.snapshot()
    assert response["cash"] == 800
    assert seen["command"][-1] == str(broker_emt_live.WORKER)
    assert seen["env"]["EMT_LIVE_READONLY"] == "1"
    assert "EMT_TEST_PASSWORD" not in seen["env"]
    assert "EMT_LIVE_PASSWORD" not in seen["env"]
    assert not broker_emt_live.Path(seen["prefix"]).parent.exists()


def test_live_snapshot_rejects_wrong_worker_environment(tmp_path, monkeypatch):
    write_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(broker_emt, "available", lambda: True)
    monkeypatch.setattr(broker_emt_live.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps({
            "environment": "simulation", "account_masked": "********0001",
            "orders_enabled": False, "positions": [], "open_orders": [],
        })))
    with pytest.raises(ValueError, match="环境校验"):
        broker_emt_live.snapshot()


def test_live_routes_are_local_only(tmp_path, monkeypatch):
    monkeypatch.setattr(broker_emt_live, "snapshot", lambda: pytest.fail("Remote live query"))
    app = create_app(tmp_path, start_manager=False)
    with TestClient(app) as client:
        assert client.get("/api/v2/investment/broker/live-readiness").status_code == 403
        assert client.post("/api/v2/investment/broker/live-snapshot").status_code == 403


def test_live_snapshot_route_checks_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_local_broker_request", lambda request: True)
    monkeypatch.setattr(broker_emt_live, "snapshot", lambda: pytest.fail("Cross-origin live query"))
    app = create_app(tmp_path, start_manager=False)
    with TestClient(app) as client:
        response = client.post("/api/v2/investment/broker/live-snapshot",
                               headers={"origin": "https://untrusted.example"})
        assert response.status_code == 403


def test_live_worker_only_uses_query_methods(tmp_path, monkeypatch, capsys):
    source = broker_emt_live.WORKER.read_text(encoding="utf-8")
    calls = {node.func.attr for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls.intersection({"insertOrder", "cancelOrder", "fundTransfer"})

    spec = importlib.util.spec_from_file_location("emt_live_snapshot_worker_test", broker_emt_live.WORKER)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker, "connection", lambda: (PROFILE["host"], PROFILE["port"], PROFILE["account"]))
    monkeypatch.setattr(worker, "LOG_PREFIX", tmp_path / "emt")

    class FakeTrader:
        def createTraderApi(self, client_id, prefix, level):
            assert client_id == 123

        def login(self, host, port, account, password, protocol, local_ip):
            assert password == ""
            return 99

        def queryAsset(self, session, reqid):
            self.onQueryAsset({"total_asset": 1000, "buying_power": 800}, {}, reqid, True, session)
            return 0

        def queryPositionByPage(self, request, session, reqid):
            self.onQueryPositionByPage({}, 100, 0, 0, reqid, True, session)
            return 0

        def queryUnfinishedOrders(self, session, reqid):
            self.onQueryOrder({}, {}, reqid, True, session)
            return 0

        def logout(self, session):
            pass

        def release(self):
            pass

    monkeypatch.setattr(worker, "load_sdk", lambda: FakeTrader)
    assert worker.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["environment"] == "live_readonly"
    assert result["open_orders"] == []
    assert result["orders_enabled"] is False
