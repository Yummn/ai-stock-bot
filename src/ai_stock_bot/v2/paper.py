from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .backtest import load_histories
from .estimators import probabilities
from .execution import Account
from .features import features, online_regimes
from .market import Market, today
from .profile_selection import PROFILES, RecentSelector, selection_mode
from .strategies import ai_decisions, current_profile, decide


def run_paper(store, config, progress):
    end = today() - dt.timedelta(days=1)
    lookback = 120 if selection_mode(config) == "recent" else 40
    histories, warnings = load_histories(Market(store), config, end - dt.timedelta(days=lookback), end, lambda msg: progress(5, msg))
    available = [s for s in config.symbols if s in histories and len(histories[s].dropna(subset=["open"])) >= 30]
    if not available:
        raise ValueError("至少需要 30 根包含开盘价的已收盘行情")
    latest = max(histories[s].index[-1] for s in available)
    account = Account(config.risk, store.meta("account"))
    if account.last_signal_date and str(latest.date()) <= account.last_signal_date:
        return {"account": account.dump(), "decisions": store.meta("last_decisions", []), "warnings": warnings + ["该交易日已经运行；不会重复买卖。"], "date": str(latest.date())}
    account.new_day(str(latest.date()))
    day_start = account.equity()
    signals = []
    mode = selection_mode(config)
    for symbol in available:
        frame = histories[symbol]
        if frame.index[-1] != latest:
            warnings.append(f"{symbol} 行情日期不同，本轮跳过")
            continue
        decision_date = frame.index[-2]
        f = features(frame).loc[decision_date]
        if not np.isfinite(f).all():
            continue
        scene = online_regimes(features(frame.loc[:decision_date]))[-1]
        recent_returns, selection_reason = None, None
        if mode == "recent":
            selector = RecentSelector(config, [symbol])
            warmup = frame.index[max(0, len(frame) - 62):-2]
            if len(warmup) == 60:
                past_features = features(frame)
                past_scenes = pd.Series(online_regimes(past_features), index=frame.index)
                workspace = {"samples": {}, "estimators": {}}
                past_outputs = {}
                for candidate in PROFILES:
                    past_outputs[candidate], _ = probabilities(
                        store, histories, config, config.preset(candidate), warmup[0], symbol,
                        candidate, lambda msg: progress(15, msg), workspace=workspace)
                workspace.clear()
                for day in warmup:
                    index = frame.index.get_loc(day)
                    selector.observe(symbol, day, frame.loc[day], past_features.loc[day], past_scenes.loc[day],
                                     {p: value[index] if value is not None else None for p, value in past_outputs.items()})
            profile, selection_reason, recent_returns = selector.choose(config, symbol, scene)
        else:
            profile = current_profile(config, scene)
        vector = None
        if config.engine == "ml":
            prediction, report = probabilities(store, histories, config, config.preset(profile), decision_date, symbol, profile, lambda msg: progress(20, msg))
            if prediction is not None:
                vector = prediction[-2]
        signal = decide(f, vector, config, profile, scene, fallback=config.engine == "ml" and vector is None)
        if selection_reason:
            signal["reason"] = f"{selection_reason}；{signal['reason']}"
        signal.update(symbol=symbol, date=str(decision_date.date()), profile_selection=mode,
                      profile_selection_reason=selection_reason, recent_profile_returns=recent_returns)
        signals.append(signal)
    if config.engine == "ai":
        from .reference import MLReferences
        cutoff = max(pd.Timestamp(s["date"]) for s in signals)
        references = MLReferences(store, histories, config, cutoff, lambda msg: progress(20, msg), symbols=[s["symbol"] for s in signals])
        reference_rows = {s["symbol"]: references.at(s["symbol"], pd.Timestamp(s["date"]), s["scene"]) for s in signals}
        for s in available:
            if histories[s].index[-1] == latest:
                account.mark(s, histories[s].iloc[-2].close)
        as_of = str(max(histories[s].index[-2] for s in available if histories[s].index[-1] == latest).date())
        signals = list(ai_decisions(config, [(s, float(histories[s].iloc[-2].close), features(histories[s]).iloc[-2]) for s in reference_rows], account_context=account.decision_context(as_of), as_of=as_of, ml_references=reference_rows).values())
        for signal in signals:
            signal["date"] = as_of
        if any(s["fallback"] for s in signals):
            warnings.append("部分 AI 决策不可用，已安全观望；未切换成本地买卖，独立风控仍生效。")
    for symbol in available:
        if histories[symbol].index[-1] == latest:
            account.mark(symbol, histories[symbol].iloc[-1].open)
    for signal in signals:
        signal["risk_exit"] = account.stop_reason(signal["symbol"], histories[signal["symbol"]].iloc[-1].open)
    bought = 0
    for signal in sorted(signals, key=lambda s: (not s["risk_exit"] and s["side"] == "buy", -s["rank_score"])):
        symbol = signal["symbol"]
        row = histories[symbol].iloc[-1]
        account.mark(symbol, row.open)
        forced = signal.pop("risk_exit")
        side = "sell" if forced else signal["side"]
        if side == "hold":
            signal["execution"] = "观望"
            continue
        preset = config.preset(signal["profile"])
        if side == "buy" and bought >= preset.max_buys:
            signal["execution"] = "买入信号数量达到上限"
            continue
        trade, note = account.trade(symbol, side, float(row.open), str(latest.date()), forced or signal["reason"], signal["profile"], day_start=day_start, allow_add=preset.allow_add)
        signal["execution"] = f"{forced}卖出：{note}" if forced else note
        if trade and side == "buy":
            bought += 1
    for symbol in available:
        account.mark(symbol, histories[symbol].iloc[-1].close)
    account.last_signal_date = str(latest.date())
    store.set_meta("account", account.dump())
    store.set_meta("last_decisions", signals)
    from .evaluation import strategy_key
    store.set_meta("last_decisions_strategy_key", strategy_key(config))
    return {"account": account.dump(), "decisions": signals, "warnings": warnings, "date": str(latest.date()), "note": "日线模拟：上一交易日收盘判断，最近已收盘交易日开盘成交"}
