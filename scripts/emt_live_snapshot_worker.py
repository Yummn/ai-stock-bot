"""One-shot live EMT asset, position and open-order query; never submits orders."""

import ipaddress
import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from emt_python_readonly_probe import LOG_PREFIX, load_sdk, suppress_login_output


PHASE = "startup"
TEST_ACCOUNTS = {
    "REDACTED_LOCAL_ACCOUNT", "REDACTED_LOCAL_ACCOUNT", "REDACTED_LOCAL_ACCOUNT",
    "REDACTED_LOCAL_ACCOUNT", "REDACTED_LOCAL_ACCOUNT", "REDACTED_LOCAL_ACCOUNT", "REDACTED_LOCAL_ACCOUNT",
}


def connection():
    if sys.version_info[:2] != (3, 10) or os.getenv("EMT_LIVE_READONLY") != "1":
        raise RuntimeError("Live read-only environment unavailable")
    host = os.getenv("EMT_LIVE_IP", "")
    account = os.getenv("EMT_LIVE_ACCOUNT", "")
    try:
        ip = ipaddress.IPv4Address(host)
        port = int(os.environ["EMT_LIVE_PORT"])
    except (ipaddress.AddressValueError, KeyError, TypeError, ValueError):
        raise RuntimeError("Live connection details invalid") from None
    if (str(ip) == "61.152.230.41" or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_unspecified or account in TEST_ACCOUNTS
            or not 1 <= port <= 65535):
        raise RuntimeError("Live connection details invalid")
    return str(ip), port, account


def main():
    global PHASE
    host, port, account = connection()
    PHASE = "sdk_load"
    trader_api = load_sdk()

    class ReadOnlyApi(trader_api):
        def __init__(self):
            super().__init__()
            self.asset_done = threading.Event()
            self.positions_done = threading.Event()
            self.orders_done = threading.Event()
            self.asset = None
            self.positions = []
            self.open_orders = []
            self.error = None
            self.page_count = 0
            self.next_reference = 0

        def onQueryAsset(self, data, error, reqid, last, session):
            if error and error.get("error_id"):
                self.error = "Asset query failed"
            elif data:
                self.asset = {
                    "equity": float(data["total_asset"]),
                    "cash": float(data["buying_power"]),
                }
            if last:
                self.asset_done.set()

        def onQueryPositionByPage(self, data, req_count, sequence, reference, reqid, last, session):
            if data and data.get("ticker"):
                self.positions.append({
                    "ticker": str(data["ticker"]),
                    "name": str(data.get("ticker_name") or ""),
                    "quantity": int(data.get("total_qty") or 0),
                    "sellable": int(data.get("sellable_qty") or 0),
                    "average_price": float(data.get("avg_price") or 0),
                })
            if last:
                self.page_count = int(sequence)
                self.next_reference = int(reference)
                self.positions_done.set()

        def onQueryOrder(self, data, error, reqid, last, session):
            if error and error.get("error_id"):
                self.error = "Open-order query failed"
            elif data and data.get("order_emt_id"):
                if len(self.open_orders) >= 200:
                    self.error = "Too many open orders"
                else:
                    self.open_orders.append({
                        "order_id": str(data["order_emt_id"]),
                        "ticker": str(data.get("ticker") or ""),
                        "side": int(data.get("side") or 0),
                        "quantity": int(data.get("quantity") or 0),
                        "quantity_traded": int(data.get("qty_traded") or 0),
                        "price": float(data.get("price") or 0),
                        "status": int(data.get("order_status") or 0),
                    })
            if last:
                self.orders_done.set()

        def onDisconnected(self, reason):
            self.error = "Live server disconnected"
            self.asset_done.set()
            self.positions_done.set()
            self.orders_done.set()

    LOG_PREFIX.parent.mkdir(parents=True, exist_ok=True)
    api = ReadOnlyApi()
    session = 0
    try:
        PHASE = "sdk_create"
        api.createTraderApi(123, str(LOG_PREFIX), 4)
        PHASE = "login"
        with suppress_login_output():
            session = api.login(host, port, account, "", 1, "")
        if not session:
            raise RuntimeError("Live read-only login failed")
        PHASE = "asset"
        if api.queryAsset(session, 1) != 0 or not api.asset_done.wait(12) or api.error or not api.asset:
            raise RuntimeError(api.error or "Asset query failed")

        PHASE = "positions"
        reference = 0
        for page in range(20):
            api.positions_done.clear()
            request = {"req_count": 100, "reference": reference, "reserved": 0}
            if api.queryPositionByPage(request, session, page + 2) != 0:
                raise RuntimeError("Position query failed")
            if not api.positions_done.wait(12) or api.error:
                raise RuntimeError(api.error or "Position query timed out")
            if api.page_count < 100:
                break
            if api.next_reference <= reference:
                raise RuntimeError("Position pagination did not advance")
            reference = api.next_reference
        else:
            raise RuntimeError("Position query exceeded 2000 records")

        PHASE = "open_orders"
        if api.queryUnfinishedOrders(session, 22) != 0:
            raise RuntimeError("Open-order query failed")
        if not api.orders_done.wait(12) or api.error:
            raise RuntimeError(api.error or "Open-order query timed out")
        print(json.dumps({
            "environment": "live_readonly",
            "provider": "东方财富 EMT",
            "account_masked": "*" * max(len(account) - 4, 0) + account[-4:],
            "last_sync": int(time.time()),
            **api.asset,
            "positions": api.positions,
            "open_orders": api.open_orders,
            "orders_enabled": False,
        }, ensure_ascii=False))
        return 0
    finally:
        if session:
            api.logout(session)
        api.release()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        known = {"Live read-only environment unavailable", "Live connection details invalid",
                 "Live read-only login failed", "Asset query failed", "Position query failed",
                 "Position query timed out", "Position pagination did not advance",
                 "Position query exceeded 2000 records", "Open-order query failed",
                 "Open-order query timed out", "Too many open orders", "Live server disconnected"}
        detail = str(exc) if isinstance(exc, RuntimeError) and str(exc) in known else type(exc).__name__
        print(f"Read-only EMT live snapshot failed: {PHASE}:{detail}", file=sys.stderr)
        raise SystemExit(1)
