from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False


@dataclass(frozen=True)
class Settings:
    market_data: str
    broker: str
    ai_provider: str
    openai_api_key: str | None
    openai_model: str
    codex_bin: str | None
    codex_model: str | None
    codex_timeout_seconds: int
    paper_db_path: str
    tushare_token: str | None
    alpaca_api_key: str | None
    alpaca_secret_key: str | None
    alpaca_paper: bool
    qmt_path: str | None
    qmt_account_id: str | None
    qmt_account_type: str
    qmt_session_id: int
    ptrade_account_cash: float
    ptrade_day_start_equity: float
    order_type: str
    live_trading_ack: str | None
    symbols: list[str]
    starting_cash: float
    max_trade_cash: float
    max_order_shares: int
    max_position_pct: float
    max_daily_loss_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    dry_run: bool


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def load_settings() -> Settings:
    load_dotenv()
    symbols = [
        item.strip().upper()
        for item in os.getenv(
            "SYMBOLS",
            "510300.SH,510500.SH,512100.SH,588000.SH,159915.SZ,512880.SH,512890.SH,518880.SH,511260.SH,513100.SH",
        ).split(",")
        if item.strip()
    ]
    return Settings(
        market_data=os.getenv("MARKET_DATA", "yahoo").strip().lower(),
        broker=os.getenv("BROKER", "paper").strip().lower(),
        ai_provider=os.getenv("AI_PROVIDER", "heuristic").strip().lower(),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        codex_bin=os.getenv("CODEX_BIN") or None,
        codex_model=os.getenv("CODEX_MODEL") or None,
        codex_timeout_seconds=_int_env("CODEX_TIMEOUT_SECONDS", 90),
        paper_db_path=os.getenv("PAPER_DB_PATH", "data/paper_trading.sqlite3"),
        tushare_token=os.getenv("TUSHARE_TOKEN") or None,
        alpaca_api_key=os.getenv("ALPACA_API_KEY") or None,
        alpaca_secret_key=os.getenv("ALPACA_SECRET_KEY") or None,
        alpaca_paper=os.getenv("ALPACA_PAPER", "true").strip().lower() != "false",
        qmt_path=os.getenv("QMT_PATH") or None,
        qmt_account_id=os.getenv("QMT_ACCOUNT_ID") or None,
        qmt_account_type=os.getenv("QMT_ACCOUNT_TYPE", "STOCK").strip().upper(),
        qmt_session_id=_int_env("QMT_SESSION_ID", 123456),
        ptrade_account_cash=_float_env("PTRADE_ACCOUNT_CASH", 100000.0),
        ptrade_day_start_equity=_float_env("PTRADE_DAY_START_EQUITY", 100000.0),
        order_type=os.getenv("ORDER_TYPE", "limit").strip().lower(),
        live_trading_ack=os.getenv("LIVE_TRADING_ACK") or None,
        symbols=symbols,
        starting_cash=_float_env("STARTING_CASH", 100000.0),
        max_trade_cash=_float_env("MAX_TRADE_CASH", 10000.0),
        max_order_shares=_int_env("MAX_ORDER_SHARES", 2500),
        max_position_pct=_float_env("MAX_POSITION_PCT", 0.50),
        max_daily_loss_pct=_float_env("MAX_DAILY_LOSS_PCT", 0.02),
        stop_loss_pct=_float_env("STOP_LOSS_PCT", 0.20),
        take_profit_pct=_float_env("TAKE_PROFIT_PCT", 0.50),
        dry_run=os.getenv("DRY_RUN", "true").strip().lower() != "false",
    )
