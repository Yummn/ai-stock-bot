from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")

    print("QMT preflight")
    try:
        from xtquant.xttrader import XtQuantTrader  # noqa: F401
        from xtquant.xttype import StockAccount  # noqa: F401
        from xtquant import xtconstant  # noqa: F401
    except Exception as exc:
        print(f"[FAIL] xtquant import failed: {exc}")
        return 1
    print("[OK] xtquant import works")

    qmt_path = os.getenv("QMT_PATH", "").strip()
    account_id = os.getenv("QMT_ACCOUNT_ID", "").strip()
    if not qmt_path:
        print("[FAIL] QMT_PATH is empty in .env")
        print("       Set it to your MiniQMT userdata_mini directory.")
        return 1

    path = Path(qmt_path)
    if not path.exists():
        print(f"[FAIL] QMT_PATH does not exist: {path}")
        return 1
    print(f"[OK] QMT_PATH exists: {path}")

    if not account_id:
        print("[FAIL] QMT_ACCOUNT_ID is empty in .env")
        return 1
    print("[OK] QMT_ACCOUNT_ID is set")

    queue_file = path / "up_queue_xtquant"
    if queue_file.exists():
        print("[OK] up_queue_xtquant exists; function trading permission is likely enabled")
    else:
        print("[WARN] up_queue_xtquant is missing")
        print("       Start and log in to MiniQMT first.")
        print("       If it is still missing, ask your broker to enable function trading permission.")

    print("Preflight finished. If MiniQMT is running, try ai-stock-bot with BROKER=qmt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
