from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: int
    close: float
    volume: int


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    last_price: float
    previous_close: float
    change_pct: float
    sma_5: float
    sma_20: float
    volume: int
    momentum_5: float = 0.0
    momentum_20: float = 0.0
    rsi_14: float = 50.0
    volatility_20: float = 0.0
    volume_ratio_20: float = 1.0


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: Side
    confidence: float
    reason: str


@dataclass(frozen=True)
class Order:
    symbol: str
    side: Side
    quantity: int
    limit_price: float
    reason: str


@dataclass
class Position:
    symbol: str
    quantity: int
    average_price: float


@dataclass
class Account:
    cash: float
    equity: float
    day_start_equity: float
