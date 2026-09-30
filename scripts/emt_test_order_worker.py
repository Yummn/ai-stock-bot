"""Submit one manually confirmed 100-share ETF buy to the EMT test server only."""

from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
from emt_python_readonly_probe import LOG_PREFIX, load_sdk, suppress_login_output


TEST_IP = "61.152.230.41"
TEST_PORT = "19088"
TEST_ACCOUNT = "REDACTED_LOCAL_ACCOUNT"
ALLOWED_SYMBOLS = {"510300.SH", "510500.SH", "512890.SH", "518880.SH",
                   "511260.SH", "513100.SH"}
PHASE = "startup"


def checked_order():
    if (sys.version_info[:2] != (3, 10)
            or os.getenv("EMT_TEST_ENV") != "simulation"
            or os.getenv("EMT_TEST_IP") != TEST_IP
            or os.getenv("EMT_TEST_PORT") != TEST_PORT
            or os.getenv("EMT_TEST_ACCOUNT") != TEST_ACCOUNT):
        raise RuntimeError("Test environment mismatch")
    symbol = os.getenv("EMT_TEST_SYMBOL", "")
    price_text = os.getenv("EMT_TEST_PRICE", "")
    try:
        price = Decimal(price_text)
    except Exception:
        raise RuntimeError("Invalid test order") from None
    if (symbol not in ALLOWED_SYMBOLS or not price.is_finite() or price <= 0
            or price > 10 or price.as_tuple().exponent < -3):
        raise RuntimeError("Invalid test order")
    return symbol, price


def main():
    global PHASE
    symbol, price = checked_order()
    PHASE = "sdk_load"
    trader_api = load_sdk()

    class TestOrderApi(trader_api):
        def __init__(self):
            super().__init__()
            self.reported = threading.Event()
            self.order_id = 0
            self.report = None
            self.disconnected = False

        def _record(self, data, error=None):
            if not data or int(data.get("order_emt_id") or 0) != self.order_id:
                return
            self.report = {
                "order_status": int(data.get("order_status") or 0),
                "quantity_traded": int(data.get("qty_traded") or 0),
                "quantity_left": int(data.get("qty_left") or 0),
                "error_id": int((error or {}).get("error_id") or 0),
            }
            self.reported.set()

        def onOrderEvent(self, data, error, session):
            self._record(data, error)

        def onQueryOrder(self, data, error, reqid, last, session):
            self._record(data, error)
            if last:
                self.reported.set()

        def onDisconnected(self, reason):
            self.disconnected = True
            self.reported.set()

    LOG_PREFIX.parent.mkdir(parents=True, exist_ok=True)
    api = TestOrderApi()
    session = 0
    try:
        PHASE = "sdk_create"
        api.createTraderApi(122, str(LOG_PREFIX), 4)
        PHASE = "login"
        with suppress_login_output():
            session = api.login(TEST_IP, int(TEST_PORT), TEST_ACCOUNT,
                                os.environ["EMT_TEST_PASSWORD"], 1, "")
        if not session:
            raise RuntimeError("Test login failed")
        order = {
            "ticker": symbol.split(".")[0],
            "market": 2,
            "side": 1,
            "price_type": 1,
            "price": float(price),
            "quantity": 100,
            "business_type": 0,
            "position_effect": 0,
            "ep_id": 0,
        }
        PHASE = "submit"
        api.order_id = int(api.insertOrder(order, session) or 0)
        if not api.order_id:
            raise RuntimeError("Test order request rejected")
        PHASE = "report"
        if not api.reported.wait(5) and not api.disconnected:
            if api.queryOrderByEMTID(api.order_id, session, 1) == 0:
                api.reported.wait(8)
        print(json.dumps({
            "environment": "simulation",
            "account_masked": "*" * (len(TEST_ACCOUNT) - 4) + TEST_ACCOUNT[-4:],
            "symbol": symbol,
            "side": "buy",
            "quantity": 100,
            "price": str(price),
            "order_id": str(api.order_id),
            "state": "reported" if api.report else "unknown_after_submit",
            "report": api.report,
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
        known = {"Test environment mismatch", "Invalid test order", "Test login failed",
                 "Test order request rejected"}
        detail = str(exc) if isinstance(exc, RuntimeError) and str(exc) in known else type(exc).__name__
        print(f"EMT test order failed: {PHASE}:{detail}", file=sys.stderr)
        raise SystemExit(1)
