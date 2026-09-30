from __future__ import annotations

from collections import deque
import datetime as dt

import numpy as np

from .execution import Account
from .strategies import current_profile, decide


PROFILES = ("aggressive", "balanced", "conservative")
RECENT_SESSIONS = 60


def selection_mode(config):
    if config.profile_selection == "recent" and config.engine == "ml":
        return "recent"
    if config.auto_profile or config.profile_selection == "trend":
        return "trend"
    return "fixed"


def preview_profiles(store, config):
    from .features import features, online_regimes
    from .market import Market, today
    from .schema import SYMBOLS

    mode = selection_mode(config)
    if mode == "fixed":
        return {"mode": mode, "rows": []}
    if mode == "recent":
        from .evaluation import strategy_key

        matched = store.meta("last_decisions_strategy_key") == strategy_key(config)
        signals = {row["symbol"]: row for row in store.meta("last_decisions", [])} if matched else {}
        rows = []
        for symbol in config.symbols:
            signal = signals.get(symbol)
            ready = signal and signal.get("profile_selection") == "recent"
            rows.append({"symbol": symbol, "name": SYMBOLS[symbol], "status": "ready" if ready else "pending",
                         "profile": signal["profile"] if ready else None,
                         "reason": signal.get("profile_selection_reason") if ready else "这组方案还没有近三个月模拟成绩",
                         "date": signal.get("date") if ready else None})
        return {"mode": mode, "source": "last_paper", "rows": rows}

    market = Market(store)
    rows = []
    end = today() - dt.timedelta(days=1)
    for symbol in config.symbols:
        frame = market.read(symbol, end=end).tail(90)
        row = {"symbol": symbol, "name": SYMBOLS[symbol], "status": "missing", "profile": None,
               "reason": "缓存行情不足，先更新行情", "date": None}
        if len(frame) >= 21:
            feature_frame = features(frame)
            f = feature_frame.iloc[-1]
            if np.isfinite(f).all():
                scene = online_regimes(feature_frame)[-1]
                profile = {"bull": "aggressive", "sideways": "balanced", "bear": "conservative"}[scene]
                momentum = f"{f.momentum20:+.1%}"
                if scene == "bull":
                    reason = f"近20日涨幅 {momentum}，5日均线高于20日均线"
                elif scene == "bear":
                    causes = []
                    if f.momentum20 < -.04:
                        causes.append(f"近20日涨幅 {momentum}")
                    if f.drawdown < -.12:
                        causes.append(f"距近60日高点回落 {f.drawdown:.1%}")
                    reason = "；".join(causes)
                else:
                    reason = f"近20日涨幅 {momentum}，未触发激进或保守条件"
                row.update(status="ready", profile=profile, reason=reason, date=str(frame.index[-1].date()))
        rows.append(row)
    return {"mode": mode, "source": "cached_market", "rows": rows}


class ShadowProfile:
    def __init__(self, config, symbol, profile):
        self.config, self.symbol, self.profile = config, symbol, profile
        self.account = Account(config.risk)
        self.pending = None
        self.history = deque([(config.risk.starting_cash, 0)], maxlen=RECENT_SESSIONS + 1)
        self.valid_history = deque(maxlen=RECENT_SESSIONS)

    def observe(self, date, row, features, scene, vector):
        day = str(date.date())
        self.account.new_day(day)
        day_start = self.account.equity()
        self.account.mark(self.symbol, row.open)
        forced = self.account.stop_reason(self.symbol, float(row.open))
        if forced or self.pending and self.pending["side"] == "sell":
            self.account.trade(self.symbol, "sell", float(row.open), day,
                               forced or self.pending["reason"], self.profile)
        elif self.pending and self.pending["side"] == "buy":
            preset = self.config.preset(self.profile)
            self.account.trade(self.symbol, "buy", float(row.open), day,
                               self.pending["reason"], self.profile, day_start=day_start,
                               allow_add=preset.allow_add)
        self.account.mark(self.symbol, row.close)
        feature_valid = bool(np.isfinite(features).all())
        self.valid_history.append(bool(feature_valid and vector is not None and np.isfinite(vector).all()))
        self.pending = (decide(features, vector, self.config, self.profile, scene,
                               fallback=self.config.engine == "ml" and vector is None)
                        if feature_valid else None)
        self.history.append((self.account.equity(), len(self.account.trades)))

    def recent_return(self):
        if len(self.history) < RECENT_SESSIONS + 1 or len(self.valid_history) < RECENT_SESSIONS or not all(self.valid_history):
            return None
        first_equity, first_trades = self.history[0]
        last_equity, last_trades = self.history[-1]
        if last_trades == first_trades and not self.account.positions:
            return None
        return last_equity / first_equity - 1


class RecentSelector:
    def __init__(self, config, symbols):
        self.trackers = {(symbol, profile): ShadowProfile(config, symbol, profile)
                         for symbol in symbols for profile in PROFILES}

    def observe(self, symbol, date, row, feature, scene, vectors):
        for profile in PROFILES:
            self.trackers[symbol, profile].observe(date, row, feature, scene, vectors.get(profile))

    def choose(self, config, symbol, scene):
        returns = {profile: self.trackers[symbol, profile].recent_return() for profile in PROFILES}
        baseline = current_profile(config, scene)
        if returns["aggressive"] is None:
            return baseline, "近三个月激进档有效模拟记录不足，按价格走势选择", returns
        if returns["aggressive"] >= 0:
            return "aggressive", "近三个月激进档模拟净收益非负", returns
        if returns["balanced"] is None:
            return baseline, "近三个月均衡档有效模拟记录不足，按价格走势选择", returns
        if returns["balanced"] >= 0:
            return "balanced", "激进档模拟亏损，均衡档模拟净收益非负", returns
        return "conservative", "激进与均衡档近三个月模拟均亏损", returns
