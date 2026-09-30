import importlib.util
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ai_stock_bot.v2.api import create_app
from ai_stock_bot.v4 import broker_emt
from ai_stock_bot.v4 import routes
from ai_stock_bot.v4.routes import _local_broker_request


def test_broker_route_is_local_only(tmp_path, monkeypatch):
    monkeypatch.setattr(broker_emt, "snapshot", lambda *args: pytest.fail("Remote request reached broker"))
    app = create_app(tmp_path, start_manager=False)
    with TestClient(app) as client:
        response = client.post("/api/v2/investment/broker/snapshot",
                               json={"account": "test-account", "password": "not-a-real-password"})
        assert response.status_code == 403
        assert client.get("/api/v2/investment/broker/availability").json() == {
            "local_only": True, "available": False, "manual_test_order_available": False,
        }
        response = client.post("/api/v2/investment/broker/test-buy", json={
            "account": broker_emt.SIMULATION_ORDINARY_ACCOUNT,
            "password": "dummy", "symbol": "510300.SH", "price": "5.000",
            "request_id": str(uuid4()), "confirmation": "提交仿真测试委托",
        })
        assert response.status_code == 403


@pytest.mark.parametrize("host,client,forwarded,allowed", [
    ("127.0.0.1:8771", "127.0.0.1", None, True),
    ("localhost:8771", "::1", None, True),
    ("stock.example", "127.0.0.1", None, False),
    ("127.0.0.1:8771", "127.0.0.1", "1.2.3.4", False),
    ("127.0.0.1:8771", "8.8.8.8", None, False),
])
def test_local_broker_gate(host, client, forwarded, allowed):
    headers = {"host": host}
    if forwarded:
        headers["cf-connecting-ip"] = forwarded
    request = SimpleNamespace(headers=headers, client=SimpleNamespace(host=client))
    assert _local_broker_request(request) is allowed


def test_snapshot_uses_temporary_secret_and_simulation_endpoint(monkeypatch):
    monkeypatch.setattr(broker_emt, "available", lambda: True)
    seen = {}

    def fake_run(command, **kwargs):
        env = kwargs["env"]
        seen["command"] = command
        seen["env"] = {key: env[key] for key in
                       ("EMT_TEST_ENV", "EMT_TEST_IP", "EMT_TEST_PORT", "EMT_TEST_ACCOUNT", "EMT_TEST_PASSWORD")}
        seen["log_prefix"] = env["EMT_PROBE_LOG_PREFIX"]
        return SimpleNamespace(returncode=0, stdout="SDK startup notice\n" + json.dumps({
            "environment": "simulation", "orders_enabled": False,
            "equity": 1000, "cash": 1000, "positions": [],
        }))

    monkeypatch.setattr(broker_emt.subprocess, "run", fake_run)
    result = broker_emt.snapshot("test-account", "not-a-real-password")
    assert result["equity"] == 1000
    assert "not-a-real-password" not in " ".join(seen["command"])
    assert seen["env"]["EMT_TEST_ENV"] == "simulation"
    assert seen["env"]["EMT_TEST_PASSWORD"] == "not-a-real-password"
    assert seen["env"]["EMT_TEST_IP"] == broker_emt.SIMULATION_IP
    assert not broker_emt.Path(seen["log_prefix"]).parent.exists()


def test_snapshot_rejects_non_simulation_worker_response(monkeypatch):
    monkeypatch.setattr(broker_emt, "available", lambda: True)
    monkeypatch.setattr(broker_emt.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout='{"environment":"live","orders_enabled":false}'))
    with pytest.raises(ValueError, match="仿真只读校验"):
        broker_emt.snapshot("test-account", "not-a-real-password")


def test_order_rejects_other_accounts_and_unbounded_orders(monkeypatch):
    monkeypatch.setattr(broker_emt, "order_available", lambda: True)
    monkeypatch.setattr(broker_emt.subprocess, "run", lambda *args, **kwargs: pytest.fail("Order sent"))
    for account, symbol, price in [
        ("real-account", "510300.SH", "5"),
        (broker_emt.SIMULATION_ORDINARY_ACCOUNT, "600000.SH", "5"),
        (broker_emt.SIMULATION_ORDINARY_ACCOUNT, "510300.SH", "10.001"),
        (broker_emt.SIMULATION_ORDINARY_ACCOUNT, "510300.SH", "1.0001"),
    ]:
        with pytest.raises(ValueError):
            broker_emt.submit_test_buy(account, "dummy", symbol, price, str(uuid4()))


def test_order_uses_only_test_endpoint_and_deduplicates(monkeypatch):
    monkeypatch.setattr(broker_emt, "order_available", lambda: True)
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "environment": "simulation", "symbol": "510300.SH", "side": "buy",
            "quantity": 100, "price": "5.000", "order_id": "12345678901234567",
            "state": "reported", "report": None,
        }))

    monkeypatch.setattr(broker_emt.subprocess, "run", fake_run)
    request_id = str(uuid4())
    result = broker_emt.submit_test_buy(broker_emt.SIMULATION_ORDINARY_ACCOUNT,
                                        "dummy-password", "510300.SH", "5.000", request_id)
    assert result["order_id"] == "12345678901234567"
    assert seen["env"]["EMT_TEST_ENV"] == "simulation"
    assert (seen["env"]["EMT_TEST_IP"], seen["env"]["EMT_TEST_PORT"]) == (
        broker_emt.SIMULATION_IP, broker_emt.SIMULATION_PORT)
    assert seen["env"]["EMT_TEST_ACCOUNT"] == broker_emt.SIMULATION_ORDINARY_ACCOUNT
    assert "dummy-password" not in " ".join(seen["command"])
    with pytest.raises(ValueError, match="已经尝试提交"):
        broker_emt.submit_test_buy(broker_emt.SIMULATION_ORDINARY_ACCOUNT,
                                   "dummy-password", "510300.SH", "5.000", request_id)


def test_order_rejects_non_simulation_response(monkeypatch):
    monkeypatch.setattr(broker_emt, "order_available", lambda: True)
    monkeypatch.setattr(broker_emt.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout='{"environment":"live","order_id":"123",'
                                  '"symbol":"510300.SH","side":"buy",'
                                  '"quantity":100,"price":"5"}'))
    with pytest.raises(ValueError, match="无法核验"):
        broker_emt.submit_test_buy(broker_emt.SIMULATION_ORDINARY_ACCOUNT,
                                   "dummy", "510300.SH", "5", str(uuid4()))


def test_order_route_requires_matching_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_local_broker_request", lambda request: True)
    monkeypatch.setattr(broker_emt, "submit_test_buy", lambda *args: pytest.fail("Order sent"))
    app = create_app(tmp_path, start_manager=False)
    with TestClient(app) as client:
        response = client.post("/api/v2/investment/broker/test-buy",
                               headers={"origin": "https://untrusted.example"}, json={
            "account": broker_emt.SIMULATION_ORDINARY_ACCOUNT,
            "password": "dummy", "symbol": "510300.SH", "price": "5",
            "request_id": str(uuid4()), "confirmation": "提交仿真测试委托",
        })
        assert response.status_code == 403


def test_order_route_passes_confirmed_request_without_persisting_password(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_local_broker_request", lambda request: True)
    seen = {}

    def fake_submit(*args):
        seen["args"] = args
        return {"environment": "simulation", "order_id": "123", "state": "unknown_after_submit"}

    monkeypatch.setattr(broker_emt, "submit_test_buy", fake_submit)
    app = create_app(tmp_path, start_manager=False)
    request_id = str(uuid4())
    with TestClient(app) as client:
        response = client.post("/api/v2/investment/broker/test-buy", json={
            "account": broker_emt.SIMULATION_ORDINARY_ACCOUNT,
            "password": "dummy-password", "symbol": "510300.SH", "price": "5.123",
            "request_id": request_id, "confirmation": "提交仿真测试委托",
        })
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["order_id"] == "123"
        assert seen["args"][1] == "dummy-password"
        assert str(seen["args"][3]) == "5.123"
        assert seen["args"][4] == request_id


def test_order_worker_builds_one_test_limit_buy(tmp_path, monkeypatch, capsys):
    script = broker_emt.ORDER_WORKER
    spec = importlib.util.spec_from_file_location("emt_test_order_worker_test", script)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker, "checked_order", lambda: ("510300.SH", worker.Decimal("5.123")))
    monkeypatch.setattr(worker, "LOG_PREFIX", tmp_path / "emt")
    monkeypatch.setenv("EMT_TEST_PASSWORD", "dummy")
    sent = {}

    class FakeTrader:
        def createTraderApi(self, client_id, log_prefix, level):
            assert client_id == 122

        def login(self, ip, port, account, password, protocol, local_ip):
            assert (ip, str(port), account) == (
                broker_emt.SIMULATION_IP, broker_emt.SIMULATION_PORT,
                broker_emt.SIMULATION_ORDINARY_ACCOUNT)
            return 99

        def insertOrder(self, order, session):
            sent.update(order)
            self.order_id = 456
            self.onOrderEvent({"order_emt_id": 456, "order_status": 1,
                               "qty_traded": 0, "qty_left": 100}, {"error_id": 0}, session)
            return 456

        def logout(self, session):
            pass

        def release(self):
            pass

    monkeypatch.setattr(worker, "load_sdk", lambda: FakeTrader)
    assert worker.main() == 0
    response = json.loads(capsys.readouterr().out)
    assert response["order_id"] == "456"
    assert response["state"] == "reported"
    assert response["environment"] == "simulation"
    assert sent == {"ticker": "510300", "market": 2, "side": 1,
                    "price_type": 1, "price": 5.123, "quantity": 100,
                    "business_type": 0, "position_effect": 0, "ep_id": 0}
