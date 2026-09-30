from __future__ import annotations

import datetime as dt
import hashlib
import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from .schema import SYMBOLS
from .store import Store

TZ = dt.timezone(dt.timedelta(hours=8))


def today():
    return dt.datetime.now(TZ).date()


def digest(frame: pd.DataFrame):
    return hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()[:20]


def regime(frame: pd.DataFrame):
    values = frame["adjusted"].dropna()
    if len(values) < 20:
        return "sideways"
    change = values.iloc[-1] / values.iloc[0] - 1
    drawdown = (values / values.cummax() - 1).min()
    return "bull" if change >= .12 and drawdown > -.18 else "bear" if change <= -.06 or drawdown <= -.22 else "sideways"


class Market:
    def __init__(self, store: Store, namespace=""):
        if namespace not in {"", "investment_"}:
            raise ValueError("Unknown market namespace")
        self.store = store
        self.bars_table = namespace + "bars"
        self.datasets_table = namespace + "datasets"

    def read(self, symbol, start=None, end=None):
        where, params = ["symbol=?"], [symbol]
        for column, value, op in [("date", start, ">="), ("date", end, "<=")]:
            if value:
                where.append(f"{column}{op}?")
                params.append(str(value))
        with self.store.connect() as c:
            rows = [dict(r) for r in c.execute(f"SELECT * FROM {self.bars_table} WHERE {' AND '.join(where)} ORDER BY date", params)]
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "source", "adjusted"], index=pd.DatetimeIndex([], name="date"))
        frame = pd.DataFrame(rows)
        frame["date"] = pd.to_datetime(frame["date"])
        return frame.drop(columns="symbol").set_index("date")

    def write(self, symbol, frame, source):
        numeric = frame[["open", "high", "low", "close", "volume", "adjusted"]].apply(pd.to_numeric, errors="coerce")
        numeric = numeric.replace([np.inf, -np.inf], np.nan).dropna()
        numeric = numeric[(numeric.close > 0) & (numeric.adjusted > 0) & (numeric.open > 0) & (numeric.volume >= 0)]
        numeric = numeric[(numeric.high >= numeric[["open", "close", "low"]].max(axis=1)) & (numeric.low <= numeric[["open", "close", "high"]].min(axis=1))]
        numeric = numeric[~numeric.index.duplicated(keep="last")].sort_index()
        if len(numeric) < 20:
            raise ValueError(f"{SYMBOLS.get(symbol,symbol)} 没有足够的有效 OHLC 行情")
        rows = [(symbol, str(date.date()), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume), source, float(r.adjusted)) for date, r in numeric.iterrows()]
        with self.store.connect() as c:
            c.executemany(f"INSERT INTO {self.bars_table} VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,date) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,source=excluded.source,adjusted=excluded.adjusted", rows)
            c.execute(f"INSERT INTO {self.datasets_table} VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET source=excluded.source,updated=excluded.updated", (symbol, source, time.time()))

    def ensure(self, symbol, start, end, notify=lambda text: None, refresh=False):
        frame = self.read(symbol)
        with self.store.connect() as c:
            meta = c.execute(f"SELECT updated FROM {self.datasets_table} WHERE symbol=?", (symbol,)).fetchone()
        valid = frame.dropna(subset=["open", "high", "low"])
        requested = self.store.meta(f"fetch_range:{symbol}", {})
        recent_download = meta and time.time() - meta[0] < 3600 and requested.get("start", "9999") <= str(start) and requested.get("end", "") >= str(end)
        covered = len(valid) >= 20 and valid.index.min().date() <= start + dt.timedelta(days=14) and valid.index.max().date() >= end - dt.timedelta(days=8)
        current_range = end >= today() - dt.timedelta(days=14)
        fresh = not current_range or meta and time.time() - meta[0] < 3600 and requested.get("end", "") >= str(end)
        if not refresh and (covered and fresh or recent_download and len(valid) >= 20):
            return self.read(symbol, start, end)
        notify(f"获取 {SYMBOLS.get(symbol,symbol)} 历史行情")
        errors = []
        for source, getter in [("Yahoo OHLC / adjusted", self._yahoo), ("AKShare ETF / raw", self._akshare)]:
            try:
                downloaded = getter(symbol, start, end)
                self.write(symbol, downloaded, source)
                self.store.set_meta(f"fetch_range:{symbol}", {"start": str(start), "end": str(end)})
                return self.read(symbol, start, end)
            except Exception as exc:
                errors.append(f"{source}: {type(exc).__name__}: {str(exc)[:140]}")
        if covered:
            notify(f"{symbol} 下载失败，继续使用已有缓存")
            return self.read(symbol, start, end)
        raise RuntimeError(" / ".join(errors))

    def _yahoo(self, symbol, start, end):
        ticker = symbol.replace(".SH", ".SS")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        p1 = int(dt.datetime.combine(start, dt.time(), TZ).timestamp())
        p2 = int(dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), TZ).timestamp())
        response = requests.get(url, params={"period1": p1, "period2": p2, "interval": "1d", "events": "div,splits"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=(5, 18))
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        index = pd.to_datetime(result["timestamp"], unit="s", utc=True).tz_convert(TZ).tz_localize(None).normalize()
        frame = pd.DataFrame(quote, index=index)
        frame["adjusted"] = result["indicators"].get("adjclose", [{"adjclose": quote["close"]}])[0]["adjclose"]
        return frame

    def _akshare(self, symbol, start, end):
        import akshare as ak

        frame = ak.fund_etf_hist_em(symbol=symbol.split(".")[0], period="daily", start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"), adjust="")
        frame = frame.rename(columns={"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume"})
        frame["date"] = pd.to_datetime(frame["date"])
        frame["adjusted"] = frame["close"]
        frame["volume"] = frame["volume"] * 100
        return frame.set_index("date")

    def coverage(self, symbols):
        with self.store.connect() as c:
            rows = c.execute(f"SELECT b.symbol,min(b.date) first,max(b.date) last,count(*) bars,sum(CASE WHEN b.open IS NOT NULL THEN 1 ELSE 0 END) ohlc,d.source,d.updated FROM {self.bars_table} b LEFT JOIN {self.datasets_table} d ON b.symbol=d.symbol GROUP BY b.symbol").fetchall()
        data = {r["symbol"]: dict(r) for r in rows}
        return [{"symbol": s, "name": SYMBOLS.get(s, s), **data.get(s, {"first": None, "last": None, "bars": 0, "ohlc": 0, "source": None, "updated": None})} for s in symbols]

    def curves(self, symbols, max_points=252):
        result = []
        for s in symbols:
            frame = self.read(s).tail(max_points)
            if frame.empty:
                continue
            last = frame.iloc[-1]
            result.append({"symbol": s, "name": SYMBOLS.get(s, s), "price": float(last.close), "date": str(frame.index[-1].date()), "change": float(last.close / frame.iloc[-2].close - 1) if len(frame) > 1 else 0., "source": str(last.source), "points": [{"date": str(date.date()), "close": float(r.close)} for date, r in frame.iterrows()]})
        return result

    def labels(self, symbols, overrides):
        rows = []
        for symbol in symbols:
            frame = self.read(symbol)
            years = []
            for year, part in frame.groupby(frame.index.year):
                if len(part) < 20:
                    continue
                key = f"{symbol}:{year}"
                auto = regime(part)
                step = max(1, len(part) // 50)
                sampled = part.iloc[::step]
                if sampled.index[-1] != part.index[-1]:
                    sampled = pd.concat([sampled, part.tail(1)])
                years.append({"year": int(year), "regime": overrides.get(key, auto), "automatic": auto, "manual": key in overrides, "complete": int(year) < today().year and part.index[0].month <= 2 and part.index[-1].month >= 11, "bars": len(part), "return_pct": float(part.adjusted.iloc[-1] / part.adjusted.iloc[0] - 1), "curve": [{"date": str(date.date()), "close": float(r.close)} for date, r in sampled.iterrows()]})
            rows.append({"symbol": symbol, "name": SYMBOLS.get(symbol, symbol), "years": years})
        return rows
