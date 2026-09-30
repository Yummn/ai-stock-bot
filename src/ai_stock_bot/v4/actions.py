"""Verified corporate actions for the supported universe, not inferred trades."""
import pandas as pd
import re
import time
from io import StringIO
import requests

# Fund manager's 2022 annual report, pp. 32 and 45-46; split result 2022-01-14.
# https://money.finance.sina.com.cn/fund/go.php/vAkFundInfo_JJGKXX/q/2623181.phtml
SPLITS = {
    "513100.SH": {"2022-01-13": 5},
    # https://www.sse.com.cn/disclosure/fund/announcement/c/2015-04-15/510500_20150415_2.pdf
    # https://www.95579.com/main/a/20220825/28502.shtml (2022: effective after 26th close)
    "510500.SH": {"2015-04-14": .28032483, "2022-08-29": 1.14539},
    # https://vip.stock.finance.sina.com.cn/fund/go.php/vAkFundInfo_JJGKXX/q/2519374.phtml
    "512890.SH": {"2021-10-22": 2},
}
HALTS = {"513100.SH": {"2022-01-13"}, "512890.SH": {"2021-10-22"}, "510500.SH": {"2015-04-14"}}


def applicable_splits(symbol, frame):
    # Yahoo normalizes historical OHLC for splits already; do not count twice.
    if "source" in frame and frame.source.astype(str).str.startswith("Yahoo").all():
        return {}
    return SPLITS.get(symbol, {})


def units(symbol, frame):
    factor = pd.Series(1., index=frame.index)
    for date, ratio in applicable_splits(symbol, frame).items():
        factor.loc[factor.index >= pd.Timestamp(date)] *= ratio
    return factor


def add_dividends(store, symbol, frame, refresh=False):
    cached = store.meta(f"portfolio_dividends:{symbol}")
    if not cached or refresh and time.time() - cached['fetched_at'] > 3600:
        url = f"https://fundf10.eastmoney.com/fhsp_{symbol.split('.')[0]}.html"
        response = requests.get(url, timeout=(5, 18))
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text))
        table = next((t for t in tables if '除息日' in t.columns and '分红发放日' in t.columns), None)
        if table is None:
            raise ValueError(f"{symbol} 分红接口结构变化，暂停验证")
        events = []
        for _, row in table.iterrows():
            if '暂无分红' in str(row.iloc[0]) or '暂无分红' in str(row.get('除息日')):
                continue
            ex = pd.Timestamp(row['除息日']); pay = pd.Timestamp(row['分红发放日'])
            match = re.search(r'现金\s*([\d.]+)', str(row['每10份分红']))
            if not match or pd.isna(ex) or pd.isna(pay) or pay < ex:
                raise ValueError(f"{symbol} 分红字段无法可靠解析，暂停验证")
            events.append({'date':str(ex.date()), 'pay_date':str(pay.date()), 'cash':float(match[1])/10})
        cached = {'events':events,'source':url,'fetched_at':time.time()}
        store.set_meta(f"portfolio_dividends:{symbol}", cached)
    frame = frame.copy()
    frame['dividend'] = 0.
    frame['pay_date'] = ''
    for event in cached['events']:
        date=pd.Timestamp(event['date'])
        if date in frame.index:
            frame.loc[date,'dividend']=event['cash']
            frame.loc[date,'pay_date']=event['pay_date']
    return frame
