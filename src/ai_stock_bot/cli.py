from __future__ import annotations

import argparse
import time

from .config import load_settings
from .engine import TradingEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-assisted paper trading bot")
    parser.add_argument("--once", action="store_true", help="Run one trading cycle and exit")
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between cycles")
    args = parser.parse_args()

    settings = load_settings()
    engine = TradingEngine(settings)

    if args.once:
        engine.run_once()
        return

    while True:
        engine.run_once()
        time.sleep(args.interval)
