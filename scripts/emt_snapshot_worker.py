"""One-shot, read-only snapshot of an EMT ordinary simulation account."""

import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from emt_python_readonly_probe import LOG_PREFIX, load_sdk, suppress_login_output


PHASE = "startup"


def main():
    global PHASE
    if sys.version_info[:2] != (3, 10) or os.getenv("EMT_TEST_ENV") != "simulation":
        raise RuntimeError("Simulation SDK runtime is unavailable")

    PHASE = "sdk_load"
    trader_api = load_sdk()

    class SnapshotApi(trader_api):
        def __init__(self):
            super().__init__()
            self.asset_done = threading.Event()
            self.positions_done = threading.Event()
            self.asset = None
            self.positions = []
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

        def onDisconnected(self, reason):
            self.error = "Simulation server disconnected"
            self.asset_done.set()
            self.positions_done.set()

    LOG_PREFIX.parent.mkdir(parents=True, exist_ok=True)
    api = SnapshotApi()
    session = 0
    try:
        PHASE = "sdk_create"
        api.createTraderApi(121, str(LOG_PREFIX), 4)
        PHASE = "login"
        with suppress_login_output():
            session = api.login(
                os.environ["EMT_TEST_IP"],
                int(os.environ["EMT_TEST_PORT"]),
                os.environ["EMT_TEST_ACCOUNT"],
                os.environ["EMT_TEST_PASSWORD"],
                1,
                "",
            )
        if not session:
            raise RuntimeError("Simulation login failed")
        PHASE = "asset"
        if api.queryAsset(session, 1) != 0 or not api.asset_done.wait(12) or api.error or not api.asset:
            raise RuntimeError(api.error or "Asset query failed or timed out")

        reference = 0
        PHASE = "positions"
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

        account = os.environ["EMT_TEST_ACCOUNT"]
        print(json.dumps({
            "environment": "simulation",
            "provider": "东方财富 EMT",
            "account_masked": "*" * max(len(account) - 4, 0) + account[-4:],
            "last_sync": int(time.time()),
            **api.asset,
            "positions": api.positions,
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
        known_errors = {
            "Simulation SDK runtime is unavailable", "Simulation login failed",
            "Asset query failed", "Asset query failed or timed out",
            "Position query failed", "Position query timed out",
            "Position pagination did not advance", "Position query exceeded 2000 records",
            "Simulation server disconnected",
        }
        detail = str(exc) if isinstance(exc, RuntimeError) and str(exc) in known_errors else type(exc).__name__
        print(f"Read-only EMT snapshot failed: {PHASE}:{detail}", file=sys.stderr)
        raise SystemExit(1)
