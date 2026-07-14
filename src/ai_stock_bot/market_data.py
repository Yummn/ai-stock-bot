from __future__ import annotations

import datetime as dt
from statistics import mean
from math import sqrt
from typing import Protocol

import requests

from .models import Bar, MarketSnapshot
from .symbols import normalize_symbol, to_cn_symbol, to_yahoo_symbol


class MarketData(Protocol):
    def history(self, symbol: str) -> list[Bar]:
        ...

    def history_intraday(self, symbol: str, interval_minutes: int = 30, lookback_days: int = 60) -> list[Bar]:
        ...

    def snapshot(self, symbol: str) -> MarketSnapshot:
        ...


def _snapshot_from_bars(symbol: str, bars: list[Bar]) -> MarketSnapshot:
    if len(bars) < 20:
        raise ValueError(f"Need at least 20 valid bars for {symbol}, got {len(bars)}")
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    last = closes[-1]
    previous = closes[-2]
    returns_20: list[float] = []
    for pos in range(max(1, len(closes) - 19), len(closes)):
        previous_close = closes[pos - 1]
        if previous_close > 0:
            returns_20.append((closes[pos] - previous_close) / previous_close)
    avg_return = mean(returns_20) if returns_20 else 0.0
    volatility_20 = sqrt(mean([(item - avg_return) ** 2 for item in returns_20])) if returns_20 else 0.0
    avg_volume_20 = mean([item for item in volumes[-20:] if item > 0] or [1])
    return MarketSnapshot(
        symbol=normalize_symbol(symbol),
        last_price=last,
        previous_close=previous,
        change_pct=(last - previous) / previous,
        sma_5=mean(closes[-5:]),
        sma_20=mean(closes[-20:]),
        volume=bars[-1].volume,
        momentum_5=(last - closes[-6]) / closes[-6] if len(closes) >= 6 and closes[-6] > 0 else 0.0,
        momentum_20=(last - closes[-20]) / closes[-20] if closes[-20] > 0 else 0.0,
        rsi_14=_rsi(closes, 14),
        volatility_20=volatility_20,
        volume_ratio_20=bars[-1].volume / avg_volume_20 if avg_volume_20 > 0 else 1.0,
    )


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for index in range(len(closes) - period, len(closes)):
        change = closes[index] - closes[index - 1]
        if change >= 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))
    avg_gain = mean(gains)
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


class YahooMarketData:
    def __init__(self, timeout_seconds: int = 15, daily_range: str = "5y") -> None:
        self.timeout_seconds = timeout_seconds
        self.daily_range = daily_range

    def history(self, symbol: str, range_: str | None = None, interval: str = "1d") -> list[Bar]:
        yahoo_symbol = to_yahoo_symbol(symbol)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
        response = requests.get(
            url,
            params={"range": range_ or self.daily_range, "interval": interval},
            timeout=self.timeout_seconds,
            headers={"User-Agent": "ai-stock-bot/0.1"},
        )
        response.raise_for_status()
        payload = response.json()
        result = payload["chart"]["result"]
        if not result:
            raise ValueError(f"No market data returned for {symbol}")

        item = result[0]
        timestamps = item.get("timestamp") or []
        quote = item["indicators"]["quote"][0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        bars: list[Bar] = []
        for ts, close, volume in zip(timestamps, closes, volumes):
            if close is None:
                continue
            bars.append(Bar(symbol=symbol, timestamp=int(ts), close=float(close), volume=int(volume or 0)))
        if len(bars) < 20:
            raise ValueError(f"Need at least 20 valid bars for {symbol}, got {len(bars)}")
        return bars

    def snapshot(self, symbol: str) -> MarketSnapshot:
        bars = self.history(symbol)
        return _snapshot_from_bars(symbol, bars)

    def history_intraday(self, symbol: str, interval_minutes: int = 30, lookback_days: int = 60) -> list[Bar]:
        yahoo_symbol = to_yahoo_symbol(symbol)
        interval = _yahoo_interval(interval_minutes)
        range_ = "60d" if interval_minutes >= 15 else "30d"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
        response = requests.get(
            url,
            params={"range": range_, "interval": interval},
            timeout=self.timeout_seconds,
            headers={"User-Agent": "ai-stock-bot/0.1"},
        )
        response.raise_for_status()
        payload = response.json()
        result = payload["chart"]["result"]
        if not result:
            raise ValueError(f"No intraday market data returned for {symbol}")

        cutoff = int((dt.datetime.now() - dt.timedelta(days=lookback_days)).timestamp())
        item = result[0]
        timestamps = item.get("timestamp") or []
        quote = item["indicators"]["quote"][0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        bars: list[Bar] = []
        for ts, close, volume in zip(timestamps, closes, volumes):
            if close is None or int(ts) < cutoff:
                continue
            bars.append(Bar(symbol=symbol, timestamp=int(ts), close=float(close), volume=int(volume or 0)))
        if len(bars) < 20:
            raise ValueError(f"Need at least 20 valid intraday bars for {symbol}, got {len(bars)}")
        if len(bars) < 20:
            raise ValueError(f"Need at least 20 valid bars for {symbol}, got {len(bars)}")
        if len(bars) < 20:
            raise ValueError(f"Need at least 20 valid bars for {symbol}, got {len(bars)}")
        return bars


class AkShareMarketData:
    def __init__(self, lookback_days: int = 420, adjust: str = "qfq") -> None:
        self.lookback_days = lookback_days
        self.adjust = adjust

    def history(self, symbol: str) -> list[Bar]:
        import akshare as ak

        end = dt.date.today()
        start = end - dt.timedelta(days=self.lookback_days)
        ak_symbol = to_cn_symbol(symbol, "akshare")
        df = ak.stock_zh_a_hist(
            symbol=ak_symbol,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=self.adjust,
        )
        bars: list[Bar] = []
        for _, row in df.iterrows():
            close = row.get("收盘")
            date_value = row.get("日期")
            if close is None:
                continue
            date_obj = dt.datetime.strptime(str(date_value), "%Y-%m-%d")
            bars.append(
                Bar(
                    symbol=normalize_symbol(symbol),
                    timestamp=int(date_obj.timestamp()),
                    close=float(close),
                    volume=int(float(row.get("成交量", 0) or 0)),
                )
            )
        if len(bars) < 20:
            raise ValueError(f"Need at least 20 valid bars for {symbol}, got {len(bars)}")
        return bars

    def snapshot(self, symbol: str) -> MarketSnapshot:
        return _snapshot_from_bars(symbol, self.history(symbol))

    def history_intraday(self, symbol: str, interval_minutes: int = 30, lookback_days: int = 60) -> list[Bar]:
        import akshare as ak

        end = dt.datetime.now()
        start = end - dt.timedelta(days=lookback_days)
        ak_symbol = to_cn_symbol(symbol, "akshare")
        df = ak.stock_zh_a_hist_min_em(
            symbol=ak_symbol,
            period=str(_akshare_interval(interval_minutes)),
            start_date=start.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=end.strftime("%Y-%m-%d %H:%M:%S"),
            adjust=self.adjust,
        )
        bars: list[Bar] = []
        for _, row in df.iterrows():
            close = row.get("收盘")
            time_value = row.get("时间")
            if close is None or time_value is None:
                continue
            date_obj = dt.datetime.strptime(str(time_value), "%Y-%m-%d %H:%M:%S")
            bars.append(
                Bar(
                    symbol=normalize_symbol(symbol),
                    timestamp=int(date_obj.timestamp()),
                    close=float(close),
                    volume=int(float(row.get("成交量", 0) or 0)),
                )
            )
        if len(bars) < 20:
            raise ValueError(f"Need at least 20 valid intraday bars for {symbol}, got {len(bars)}")
        return bars


class TushareMarketData:
    def __init__(self, token: str, lookback_days: int = 420) -> None:
        self.token = token
        self.lookback_days = lookback_days

    def history(self, symbol: str) -> list[Bar]:
        import tushare as ts

        end = dt.date.today()
        start = end - dt.timedelta(days=self.lookback_days)
        pro = ts.pro_api(self.token)
        ts_code = to_cn_symbol(symbol, "tushare")
        df = pro.daily(
            ts_code=ts_code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df.empty:
            raise ValueError(f"No Tushare daily data returned for {ts_code}")
        df = df.sort_values("trade_date")
        bars: list[Bar] = []
        for _, row in df.iterrows():
            date_obj = dt.datetime.strptime(str(row["trade_date"]), "%Y%m%d")
            bars.append(
                Bar(
                    symbol=normalize_symbol(symbol),
                    timestamp=int(date_obj.timestamp()),
                    close=float(row["close"]),
                    volume=int(float(row.get("vol", 0) or 0)),
                )
            )
        if len(bars) < 20:
            raise ValueError(f"Need at least 20 valid bars for {symbol}, got {len(bars)}")
        return bars

    def snapshot(self, symbol: str) -> MarketSnapshot:
        return _snapshot_from_bars(symbol, self.history(symbol))

    def history_intraday(self, symbol: str, interval_minutes: int = 30, lookback_days: int = 60) -> list[Bar]:
        raise NotImplementedError("Tushare intraday data is not configured in this demo.")


class FallbackMarketData:
    def __init__(self, providers: list[tuple[str, MarketData]]) -> None:
        if not providers:
            raise ValueError("At least one market data provider is required")
        self.providers = providers

    def snapshot(self, symbol: str) -> MarketSnapshot:
        return _snapshot_from_bars(symbol, self.history(symbol))

    def history(self, symbol: str) -> list[Bar]:
        errors: list[str] = []
        for name, provider in self.providers:
            try:
                return provider.history(symbol)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        raise RuntimeError(f"All market data providers failed for {symbol}: {' | '.join(errors)}")

    def history_intraday(self, symbol: str, interval_minutes: int = 30, lookback_days: int = 60) -> list[Bar]:
        errors: list[str] = []
        for name, provider in self.providers:
            try:
                return provider.history_intraday(symbol, interval_minutes, lookback_days)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        raise RuntimeError(f"All intraday market data providers failed for {symbol}: {' | '.join(errors)}")


def _akshare_interval(interval_minutes: int) -> int:
    if interval_minutes <= 1:
        return 1
    if interval_minutes <= 5:
        return 5
    if interval_minutes <= 15:
        return 15
    if interval_minutes <= 30:
        return 30
    return 60


def _yahoo_interval(interval_minutes: int) -> str:
    if interval_minutes <= 1:
        return "1m"
    if interval_minutes <= 5:
        return "5m"
    if interval_minutes <= 15:
        return "15m"
    if interval_minutes <= 30:
        return "30m"
    return "60m"
