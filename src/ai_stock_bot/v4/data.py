from __future__ import annotations

import datetime as dt
import time

import numpy as np
import pandas as pd
import requests

from ..v2.market import Market, digest, today
from .schema import ASSETS
from .actions import HALTS, units, add_dividends


def retry_download(call):
    for attempt in range(3):
        try:
            return call()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


class PortfolioMarket(Market):
    """Refresh a complete source-consistent series, never stitch price bases."""
    def __init__(self, store):
        super().__init__(store, namespace="investment_")
        with store.connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS investment_bars(symbol TEXT NOT NULL,date TEXT NOT NULL,open REAL,high REAL,low REAL,close REAL NOT NULL,volume REAL NOT NULL,source TEXT NOT NULL,adjusted REAL NOT NULL,PRIMARY KEY(symbol,date))")
            c.execute("CREATE TABLE IF NOT EXISTS investment_datasets(symbol TEXT PRIMARY KEY,source TEXT NOT NULL,updated REAL NOT NULL)")
            if not c.execute("SELECT 1 FROM meta WHERE key='investment_market_initialized'").fetchone():
                c.execute("INSERT OR IGNORE INTO investment_bars SELECT * FROM bars")
                c.execute("INSERT OR IGNORE INTO investment_datasets SELECT * FROM datasets")
                c.execute("INSERT OR IGNORE INTO meta VALUES('investment_market_initialized','true')")

    def ensure(self, symbol, start, end, notify=lambda _: None, refresh=False):
        old = self.read(symbol)
        stamp = self.store.meta(f"portfolio_fetch:{symbol}", {})
        covered = not old.empty and old.index.min().date() <= start + dt.timedelta(days=14) and old.index.max().date() >= end - dt.timedelta(days=7)
        covered = covered or bool(not old.empty and stamp.get("start", "9999") <= str(start) and stamp.get("end", "") >= str(end))
        fresh = end < today() - dt.timedelta(days=14) or time.time() - stamp.get("at", 0) < 3600
        if not refresh and not old.empty and end >= today()-dt.timedelta(days=14) and old.index[-1].date() < end and audit({symbol:old},[symbol])["ok"]:
            if self.refresh_tail(symbol,old,end):
                return self.read(symbol,start,end)
        if not refresh and covered and fresh and audit({symbol: old}, [symbol])["ok"]:
            return self.read(symbol, start, end)
        first = min(start, old.index.min().date()) if not old.empty else start
        last = max(end, old.index.max().date()) if not old.empty else end
        notify(f"完整更新 {ASSETS[symbol]['name']} 行情")
        import akshare as ak
        source = "AKShare Eastmoney OHLC / adjusted"
        try:
            raw = retry_download(lambda: self._akshare(symbol, first, last))
            adjusted = retry_download(lambda: ak.fund_etf_hist_em(symbol=symbol.split('.')[0], period="daily", start_date=first.strftime('%Y%m%d'), end_date=last.strftime('%Y%m%d'), adjust="qfq"))
            adjusted.index = pd.to_datetime(adjusted["日期"])
            raw["adjusted"] = pd.to_numeric(adjusted["收盘"], errors="coerce")
        except Exception:
            source = "AKShare Tencent OHLC / adjusted"
            def download(adjust):
                frame = ak.stock_zh_a_hist_tx(symbol="sh" + symbol.split('.')[0], start_date=first.strftime('%Y%m%d'), end_date=last.strftime('%Y%m%d'), adjust=adjust, timeout=15)
                frame.index = pd.to_datetime(frame.pop("date"))
                frame = frame.loc[~frame.index.duplicated(keep="last")]
                frame["volume"] = frame.pop("amount") * 100
                return frame
            raw = retry_download(lambda: download(""))
            raw["adjusted"] = retry_download(lambda: download("qfq"))["close"]
        raw = raw.loc[raw.index <= pd.Timestamp(last)].sort_index()
        raw["source"] = source
        checked = audit({symbol: raw}, [symbol])
        if not checked["ok"] or len(raw) < 20:
            raise ValueError("完整行情下载未通过检查：" + "；".join(checked["blockers"]))
        # Require preservation of already cached dates before replacing a source.
        if not old.empty and not set(old.loc[old.volume > 0].index).issubset(set(raw.index)):
            raise ValueError("新来源缺少原有历史日期，保留缓存并暂停更新")
        self.write(symbol, raw, source)
        absent = set(old.index) - set(raw.index)
        if absent:
            with self.store.connect() as c:
                c.executemany("DELETE FROM investment_bars WHERE symbol=? AND date=? AND volume=0", [(symbol, str(d.date())) for d in absent])
        self.store.set_meta(f"portfolio_fetch:{symbol}", {"at": time.time(), "start": str(first), "end": str(last)})
        return self.read(symbol, start, end)

    def refresh_tail(self, symbol, old, end):
        """Append raw quotes only after overlapping price-basis checks."""
        try:
            recent = self._yahoo(symbol, old.index[-1].date()-dt.timedelta(days=30), end)
            recent = recent.dropna(subset=['open','high','low','close','adjusted','volume'])
            if recent.empty or recent.index[-1] <= old.index[-1]:
                return False
            overlap = old.index.intersection(recent.index)
            if len(overlap) < 3:
                return False
            # A split or incompatible quote basis must not silently cross the join.
            raw_error = (old.loc[overlap,'close'] / recent.loc[overlap,'close'] - 1).abs()
            if raw_error.max() > .003:
                return False
            ratios = old.loc[overlap,'adjusted'] / recent.loc[overlap,'adjusted']
            factor = float(ratios.median())
            if ((ratios/factor)-1).abs().max() > .005:
                return False
            tail = recent.loc[recent.index > old.index[-1]].copy()
            tail['adjusted'] *= factor
            tail['source'] = 'AKShare + Yahoo aligned OHLC / adjusted'
            if not audit({symbol:tail},[symbol])['ok']:
                return False
            combined = pd.concat([old,tail])
            self.write(symbol,combined,'AKShare + Yahoo aligned OHLC / adjusted')
            self.store.set_meta(f'portfolio_join:{symbol}',{'at':time.time(),'overlap_start':str(overlap[0].date()),'overlap_end':str(overlap[-1].date()),'adjusted_scale':factor,'raw_max_relative_error':float(raw_error.max())})
            self.store.set_meta(f'portfolio_fetch:{symbol}',{'at':time.time(),'start':str(old.index[0].date()),'end':str(end)})
            return True
        except Exception:
            return False


def audit(histories, symbols, start=None, end=None):
    rows, blockers = [], []
    calendar = sorted(set().union(*(set(f.index) for s, f in histories.items() if s in symbols)))
    for symbol in symbols:
        frame = histories.get(symbol)
        issues = []
        if frame is None or frame.empty:
            rows.append({"symbol": symbol, "name": ASSETS[symbol]["name"], "bars": 0, "issues": ["没有行情"], "ok": False})
            blockers.append(f"{symbol} 没有行情")
            continue
        if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
            issues.append("行情日期重复或倒序")
        required = ["open", "high", "low", "close", "adjusted", "volume"]
        values = frame[required].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (frame[["open", "high", "low", "close", "adjusted"]] <= 0).any().any() or (frame.volume < 0).any():
            issues.append("价格或成交量无效")
        if (frame.high < frame[["open", "close", "low"]].max(axis=1)).any() or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any():
            issues.append("最高最低价不一致")
        sources = sorted(str(s) for s in frame.source.unique()) if "source" in frame else ["fixture"]
        raw = any("/ raw" in s for s in sources)
        adjusted = any("/ adjusted" in s for s in sources)
        if raw and adjusted:
            issues.append("同一序列混用原始价与复权价来源，请完整更新行情")
        split_neutral = frame.close * units(symbol, frame)
        if ((split_neutral.pct_change().abs() > .35) & (frame.adjusted.pct_change().abs() < .10)).any():
            issues.append("疑似未登记的份额折算，暂停计算")
        if (frame.index > pd.Timestamp(today())).any():
            issues.append("存在未来日期")
        selected = frame.loc[start:end] if start or end else frame
        if start and end:
            if selected.empty or selected.index[0] > pd.Timestamp(start) + pd.Timedelta(days=14) or selected.index[-1] < pd.Timestamp(end) - pd.Timedelta(days=14):
                issues.append("未覆盖完整测试区间")
            expected = [d for d in calendar if pd.Timestamp(start) <= d <= pd.Timestamp(end)]
            known_halts = {pd.Timestamp(d) for d in HALTS.get(symbol, set())}
            missing = len(set(expected) - set(selected.index) - known_halts)
            if missing:
                issues.append(f"相对交易池日历缺失 {missing} 个交易日")
        row = {"symbol": symbol, "name": ASSETS[symbol]["name"], "bars": len(frame),
               "start": str(frame.index[0].date()), "end": str(frame.index[-1].date()),
               "sources": sources, "hash": digest(frame), "issues": issues, "ok": not issues}
        rows.append(row)
        blockers.extend(f"{ASSETS[symbol]['name']}：{issue}" for issue in issues)
    return {"ok": not blockers, "rows": rows, "blockers": blockers,
            "limitations": ["现金分红按除息日计入应收、发放日到账；未计现金利息", "当前交易池并非历史全市场，结果不能推广为全市场表现", "日线无法还原盘中所有成交路径；跨境ETF溢价与临时停牌需单独核查", "供应商可能修订历史行情，冻结快照仅保证本次结果可复现"]}


def cached_histories(store, plan, start=None, end=None):
    market = PortfolioMarket(store)
    return {s: market.read(s, start, end) for s in plan.symbols}


def load_histories(store, plan, start, end, progress, refresh=False):
    earliest = (pd.Timestamp(start) - pd.DateOffset(years=plan.training_years) - pd.Timedelta(days=200)).date()
    market = PortfolioMarket(store)
    histories = {}
    for index, symbol in enumerate(plan.symbols):
        progress(2 + 8 * index / len(plan.symbols), f"检查 {ASSETS[symbol]['name']} 行情")
        histories[symbol] = market.ensure(symbol, earliest, end, refresh=refresh)
        histories[symbol] = retry_download(lambda: add_dividends(store, symbol, histories[symbol], refresh=end >= today() - dt.timedelta(days=14)))
    checked = audit(histories, plan.symbols, start, end)
    if not checked["ok"] and not refresh:
        return load_histories(store, plan, start, end, progress, refresh=True)
    if not checked["ok"]:
        raise ValueError("行情检查未通过：" + "；".join(checked["blockers"]))
    return histories, checked


def intraday_table(store):
    with store.connect() as c:
        c.execute("CREATE TABLE IF NOT EXISTS intraday_bars(symbol TEXT,stamp TEXT,interval TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT,PRIMARY KEY(symbol,stamp,interval))")


EXECUTION_INTERVAL = "60m-exec"


def execution_interval(interval):
    return "30m-exec" if interval == "30m" else EXECUTION_INTERVAL


def halfhour_yahoo(symbol):
    from ..v2.market import TZ
    response=requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.replace('.SH','.SS')}",params={'range':'60d','interval':'30m'},headers={'User-Agent':'Mozilla/5.0'},timeout=(5,18))
    response.raise_for_status()
    result=response.json()['chart']['result'][0]
    frame=pd.DataFrame(result['indicators']['quote'][0])
    starts=pd.to_datetime(result['timestamp'],unit='s',utc=True).tz_convert(TZ).tz_localize(None)
    frame['start']=starts
    clocks = {'09:30', '10:00', '10:30', '11:00', '13:00', '13:30', '14:00', '14:30'}
    frame = frame[frame.start.dt.strftime('%H:%M').isin(clocks)].copy()
    frame = frame.dropna(subset=['open', 'high', 'low', 'close', 'volume'])
    frame = frame[(frame.volume > 0) & (frame.open > 0) & (frame.close > 0)]
    frame['stamp'] = frame.start + pd.Timedelta(minutes=30)
    return frame.sort_values('stamp').drop_duplicates('stamp').reset_index(drop=True)


def hourly_yahoo(symbol):
    frame = halfhour_yahoo(symbol)
    schedule = {'09:30': '10:30', '10:00': '10:30',
                '10:30': '11:30', '11:00': '11:30',
                '13:00': '14:00', '13:30': '14:00',
                '14:00': '15:00', '14:30': '15:00'}
    frame['bucket']=frame['start'].dt.strftime('%H:%M').map(schedule)
    frame=frame.dropna(subset=['bucket','open','high','low','close','volume'])
    frame=frame[frame.volume>0]
    frame['date']=frame['start'].dt.date
    bars=[]
    for (day,bucket), group in frame.groupby(['date','bucket'], sort=True):
        group=group.sort_values('start')
        if len(group)!=2:
            continue
        bars.append({'stamp':pd.Timestamp(f'{day} {bucket}:00'),
                     'open':float(group.iloc[0].open),
                     'high':float(group.high.max()),
                     'low':float(group.low.min()),
                     'close':float(group.iloc[-1].close),
                     'volume':float(group.volume.sum())})
    return pd.DataFrame(bars,columns=['stamp','open','high','low','close','volume'])


def refresh_intraday(store, symbols, progress, interval="60m"):
    intraday_table(store)
    errors = []
    for index, symbol in enumerate(symbols):
        progress(5 + 85 * index / len(symbols), f"更新 {ASSETS[symbol]['name']} {interval} 行情")
        try:
            data = halfhour_yahoo(symbol) if interval == "30m" else hourly_yahoo(symbol)
            source = ('Yahoo exchange 30m / raw' if interval == "30m" else
                      'Yahoo 30m aggregated to exchange 60m / raw')
            now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(tzinfo=None)
            data["stamp"] = pd.to_datetime(data.stamp)
            data = data[data.stamp <= now].dropna(subset=["open", "high", "low", "close", "volume"])
            data = data[(data.open > 0) & (data.close > 0) & (data.volume > 0) & (data.high >= data[["open", "close", "low"]].max(axis=1)) & (data.low <= data[["open", "close", "high"]].min(axis=1))]
            if data.empty:
                raise ValueError("接口没有返回有效小时数据")
            with store.connect() as c:
                c.executemany("INSERT OR REPLACE INTO intraday_bars VALUES(?,?,?,?,?,?,?,?,?)", [(symbol, str(r.stamp), execution_interval(interval), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume), source) for r in data.itertuples()])
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
    if len(errors)==len(symbols):
        raise RuntimeError("小时执行行情没有可用的完整 K 线；未生成替代行情。" + "；".join(e['symbol']+': '+e['error'] for e in errors))
    return {"errors": errors, "coverage": intraday_coverage(store, interval), "purpose": "完整盘中 K 线；供验证及前向模拟使用"}


def intraday_coverage(store, interval="60m"):
    intraday_table(store)
    with store.connect() as c:
        return [dict(r) for r in c.execute("SELECT symbol,min(stamp) first,max(stamp) last,count(*) bars FROM intraday_bars WHERE interval=? GROUP BY symbol", (execution_interval(interval),))]


def intraday_points(store, symbol, interval="60m"):
    intraday_table(store)
    with store.connect() as c:
        return [dict(r) for r in reversed(c.execute("SELECT stamp AS date,close FROM intraday_bars WHERE symbol=? AND interval=? ORDER BY stamp DESC LIMIT 480", (symbol, execution_interval(interval))).fetchall())]
