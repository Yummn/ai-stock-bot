"""Offline SDK check or read-only EMT simulation API connection check."""

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import os
from pathlib import Path
import sys
import threading


ROOT = Path(__file__).resolve().parents[1]
SDK_LIB = ROOT / ".tools" / "emt_api_python" / "emt_api_python" / "lib" / "windows"
LOG_PREFIX = Path(
    os.getenv(
        "EMT_PROBE_LOG_PREFIX",
        str(ROOT / ".tools" / "emt_api_python" / "probe_state" / "emt"),
    )
)
_dll_directory = None


@contextmanager
def suppress_login_output():
    """The vendor SDK may print the account and password during login."""
    sys.stdout.flush()
    sys.stderr.flush()
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    try:
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            with redirect_stdout(sink), redirect_stderr(sink):
                yield
    finally:
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)


def load_sdk():
    global _dll_directory
    if not SDK_LIB.is_dir():
        raise RuntimeError("EMT Python SDK is missing from .tools/emt_api_python")
    _dll_directory = os.add_dll_directory(str(SDK_LIB))
    sys.path.insert(0, str(SDK_LIB))
    import vnemtquote  # noqa: F401
    from vnemttrader import TraderApi

    return TraderApi


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "probe"))
    args = parser.parse_args()

    if sys.version_info[:2] != (3, 10):
        parser.error("This SDK requires Python 3.10")

    if args.mode == "probe":
        if os.getenv("EMT_TEST_ENV") != "simulation":
            parser.error("Set EMT_TEST_ENV=simulation for the test API only")
        ip = os.getenv("EMT_TEST_IP", "")
        account = os.getenv("EMT_TEST_ACCOUNT", "")
        try:
            port = int(os.getenv("EMT_TEST_PORT", ""))
        except ValueError:
            port = 0
        if not ip or not account or not 1 <= port <= 65535:
            parser.error("Set broker-provided EMT_TEST_IP, EMT_TEST_PORT, and EMT_TEST_ACCOUNT")

    TraderApi = load_sdk()

    class AssetProbe(TraderApi):
        def __init__(self):
            super().__init__()
            self.done = threading.Event()
            self.success = False

        def onQueryAsset(self, data, error, reqid, last, session):
            if error and error.get("error_id"):
                print(f"Asset query failed, code {error['error_id']}")
            elif data:
                self.success = True
            if last:
                self.done.set()

        def onDisconnected(self, reason):
            print(f"Disconnected, code {reason}")
            self.done.set()

    LOG_PREFIX.parent.mkdir(parents=True, exist_ok=True)
    api = AssetProbe()
    session = 0
    try:
        api.createTraderApi(121, str(LOG_PREFIX), 4)
        print(f"EMT API version: {api.getApiVersion()}")
        print("Trader and quote extensions loaded")
        if args.mode == "check":
            return 0

        with suppress_login_output():
            session = api.login(
                ip,
                port,
                account,
                os.getenv("EMT_TEST_PASSWORD", ""),
                1,
                os.getenv("EMT_TEST_LOCAL_IP", ""),
            )
        if not session:
            error = api.getApiLastError()
            print(f"Simulation API login failed, code {error.get('error_id') if error else -1}")
            return 1

        print("Simulation API login succeeded. Querying asset status only.")
        if api.queryAsset(session, 1) != 0:
            error = api.getApiLastError()
            print(f"Asset query request failed, code {error.get('error_id') if error else -1}")
            return 1
        if not api.done.wait(10) or not api.success:
            print("Asset query did not succeed")
            return 1
        print("Asset query succeeded")
        return 0
    finally:
        if session:
            api.logout(session)
        api.release()


if __name__ == "__main__":
    raise SystemExit(main())
