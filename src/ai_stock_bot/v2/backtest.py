from __future__ import annotations

import datetime as dt
import hashlib

import numpy as np
import pandas as pd

from .estimators import probabilities
from .execution import Account
from .features import features, online_regimes
from .market import Market, digest, today
from .profile_selection import PROFILES, RecentSelector, selection_mode
from .schema import AppConfig, JobRequest, SYMBOLS
from .strategies import ai_decisions, current_profile, decide


def metrics(curve, starting):
    values = np.array([starting] + [p["equity"] for p in curve])
    dd = values / np.maximum.accumulate(values) - 1
    return {"return_pct": float(values[-1] / starting - 1), "max_drawdown": float(dd.min()), "end_equity": float(values[-1]), "start_equity": starting}


def load_histories(market, config, start, end, notify):
    if config.engine == "ai":
        from .reference import REFERENCE_PROFILES
        years = max(config.models[model][profile].training_years for model in config.models for profile in REFERENCE_PROFILES)
    else:
        years = max(p.training_years for p in config.models[config.model].values()) if config.auto_profile else config.preset().training_years
    earliest = (pd.Timestamp(start) - pd.DateOffset(years=years) - pd.Timedelta(days=100)).date()
    histories, warnings = {}, []
    universe = list(dict.fromkeys(config.symbols + (config.training_symbols if config.engine in {"ml", "ai"} else [])))
    for symbol in universe:
        try:
            frame = market.ensure(symbol, earliest, end, notify)
            if frame.empty:
                raise ValueError("请求区间内没有数据")
            histories[symbol] = frame
        except Exception as exc:
            warnings.append(f"{SYMBOLS[symbol]}：{str(exc)[:250]}")
    if not any(s in histories for s in config.symbols):
        raise ValueError("交易池没有有效行情，请先更新数据。" + " ".join(warnings[:2]))
    return histories, warnings


def run_backtest(store, request: JobRequest, progress=lambda percent, message: None, histories=None):
    config = request.config
    if config.engine == "ai" and request.period != "random14":
        raise ValueError("AI 模式只支持 14 个交易日的短期验证")
    if request.period == "year":
        if request.year >= today().year:
            raise ValueError("自然年对比请选择已经结束的年份")
        start, end = dt.date(request.year, 1, 1), dt.date(request.year, 12, 31)
    else:
        end = today() - dt.timedelta(days=1)
        start = end - dt.timedelta(days=500)
    progress(3, "准备固定区间行情")
    warnings = []
    if histories is None:
        histories, warnings = load_histories(Market(store), config, start, end, lambda msg: progress(6, msg))
    active = {}
    for symbol in config.symbols:
        if symbol not in histories:
            continue
        frame = histories[symbol]
        candidate = frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))].dropna(subset=["open", "high", "low"])
        if len(candidate) < 14:
            warnings.append(f"{SYMBOLS[symbol]} 在测试区间数据不足，已排除")
            continue
        if request.period == "year" and (candidate.index[0].month > 2 or candidate.index[-1].month < 11):
            warnings.append(f"{SYMBOLS[symbol]} 缺少完整自然年覆盖，已排除")
            continue
        active[symbol] = candidate.index
    if not active:
        raise ValueError("请求年份没有覆盖完整的 OHLC 行情；不会自动换成其他日期。" + " ".join(warnings[:3]))
    timeline = sorted(set().union(*[set(v) for v in active.values()]))
    if request.period == "random14":
        if len(timeline) < 15:
            raise ValueError("随机测试需要至少 15 个有效交易日")
        # Seed is part of the frozen experiment, so this random window can be reproduced.
        offset = int(np.random.default_rng(config.seed).integers(0, len(timeline) - 14))
        timeline = timeline[offset:offset + 14]
    cutoff = timeline[0]
    from .reference import MLReferences
    references = MLReferences(store, histories, config, cutoff, lambda msg: progress(12, msg), symbols=active) if config.engine == "ai" else None
    feature_frames = {s: features(histories[s]) for s in active}
    scene_rows = {s: pd.Series(online_regimes(feature_frames[s]), index=feature_frames[s].index) for s in active}
    outputs, reports = {}, []
    mode = selection_mode(config)
    selector = RecentSelector(config, active) if mode == "recent" else None
    if config.engine == "ml":
        workspace = {"samples": {}, "estimators": {}}
        profiles = PROFILES if mode != "fixed" else [config.profile]
        warmup_dates = sorted(set().union(*(set(histories[s].index[histories[s].index < cutoff][-60:]) for s in active))) if selector else []
        warmup_outputs = {}
        if warmup_dates:
            for profile in profiles:
                for symbol in active:
                    result, report = probabilities(store, histories, config, config.preset(profile), warmup_dates[0], symbol, profile, lambda msg: progress(9, msg), workspace=workspace)
                    reports.append({**report, "purpose": "recent_warmup"})
                    warmup_outputs[symbol, profile] = result
            for date in warmup_dates:
                for symbol in active:
                    if date not in histories[symbol].index:
                        continue
                    frame = feature_frames[symbol]
                    scene = scene_rows[symbol].loc[date]
                    idx = histories[symbol].index.get_loc(date)
                    selector.observe(symbol, date, histories[symbol].loc[date], frame.loc[date], scene,
                                     {p: value[idx] if value is not None else None for p in profiles
                                      for value in [warmup_outputs[symbol, p]]})
            workspace.clear()
            workspace = {"samples": {}, "estimators": {}}
        for profile in profiles:
            for symbol in active:
                result, report = probabilities(store, histories, config, config.preset(profile), cutoff, symbol, profile, lambda msg: progress(12, msg), workspace=workspace)
                reports.append(report)
                outputs[(symbol, profile)] = result
        workspace.clear()
    account = Account(config.risk)
    benchmark_risk = config.risk.model_copy(update={"max_trade_cash": config.risk.starting_cash, "max_position_pct": 1., "max_order_shares": 10**9})
    benchmark = Account(benchmark_risk)
    pending, curve, hold_curve, decisions = {}, [], [], []
    fallback_count, executed_profiles = 0, {}
    first_prices = {}
    for step, date in enumerate(timeline):
        account.new_day(str(date.date()))
        benchmark.new_day(str(date.date()))
        day_start = account.equity()
        tradable = {s: histories[s].loc[date] for s in active if date in active[s]}
        for s, row in tradable.items():
            account.mark(s, row.open)
            benchmark.mark(s, row.open)
        if step == 0:
            budget = config.risk.starting_cash / len(active)
            for s, row in tradable.items():
                qty = int(budget / (row.open * (1 + config.risk.slippage_bps / 10000) * (1 + config.risk.commission_rate)) // 100 * 100)
                benchmark.trade(s, "buy", row.open, str(date.date()), "等金额买入持有基准", config.profile, quantity=qty)
                first_prices[s] = float(row.open)
        blocked = set()
        for s, row in tradable.items():
            forced = account.stop_reason(s, float(row.open))
            signal = pending.get(s)
            if forced or signal and signal["side"] == "sell":
                reason = forced or signal["reason"]
                selected = signal["profile"] if signal else config.profile
                trade, _ = account.trade(s, "sell", float(row.open), str(date.date()), reason, selected)
                if trade:
                    blocked.add(s)
        buy_signals = sorted([(s, signal) for s, signal in pending.items() if signal["side"] == "buy" and s in tradable and s not in blocked], key=lambda pair: pair[1]["rank_score"], reverse=True)
        bought = 0
        for s, signal in buy_signals:
            p = config.preset(signal["profile"])
            if bought >= p.max_buys:
                continue
            trade, rejected = account.trade(s, "buy", float(tradable[s].open), str(date.date()), signal["reason"], signal["profile"], day_start=day_start, allow_add=p.allow_add)
            if trade:
                bought += 1
                executed_profiles[signal["profile"]] = executed_profiles.get(signal["profile"], 0) + 1
        pending = {}
        for s, row in tradable.items():
            account.mark(s, row.close)
            benchmark.mark(s, row.close)
            f = feature_frames[s].loc[date]
            if not np.isfinite(f).all():
                continue
            scene = scene_rows[s].loc[date]
            if selector:
                selected, selection_reason, recent_returns = selector.choose(config, s, scene)
            else:
                selected, selection_reason, recent_returns = current_profile(config, scene), None, None
            prediction = outputs.get((s, selected))
            index = histories[s].index.get_loc(date)
            vector = prediction[index] if prediction is not None else None
            decision = decide(f, vector, config, selected, scene, fallback=config.engine == "ml" and vector is None)
            if selection_reason:
                decision["reason"] = f"{selection_reason}；{decision['reason']}"
            fallback_count += int(decision["fallback"])
            decision.update(symbol=s, date=str(date.date()), name=SYMBOLS[s], profile_selection=mode,
                            profile_selection_reason=selection_reason, recent_profile_returns=recent_returns)
            pending[s] = decision
            if config.engine != "ai":
                decisions.append(decision)
            if selector:
                selector.observe(s, date, row, f, scene,
                                 {p: value[index] if value is not None else None for p in PROFILES
                                  for value in [outputs[s, p]]})
        if config.engine == "ai":
            reference_rows = {s: references.at(s, date, scene_rows[s].loc[date]) for s in tradable if np.isfinite(feature_frames[s].loc[date]).all()}
            generated = ai_decisions(config, [(s, float(row.close), feature_frames[s].loc[date]) for s, row in tradable.items() if np.isfinite(feature_frames[s].loc[date]).all()], account_context=account.decision_context(str(date.date())), as_of=str(date.date()), ml_references=reference_rows)
            for s, value in generated.items():
                value.update(symbol=s, date=str(date.date()), name=SYMBOLS.get(s, s))
                pending[s] = value
            decisions.extend(generated.values())
            fallback_count += sum(int(value["fallback"]) for value in generated.values())
        curve.append({"date": str(date.date()), "equity": account.equity(), "cash": account.cash, "hold_equity": benchmark.equity()})
        hold_curve.append({"date": str(date.date()), "equity": benchmark.equity()})
        progress(25 + 70 * (step + 1) / len(timeline), f"模拟 {date.date()} · {step + 1}/{len(timeline)} 个交易日")
    stats = metrics(curve, config.risk.starting_cash)
    if config.engine == "ai" and fallback_count:
        warnings.append(f"{fallback_count} 次 AI 决策不可用，已安全观望；未切换成本地买卖。")
    hold_stats = metrics(hold_curve, config.risk.starting_cash)
    symbol_curves = []
    for s in active:
        frame = histories[s].loc[histories[s].index.isin(timeline)]
        symbol_curves.append({"symbol": s, "name": SYMBOLS[s], "data_hash": digest(frame), "points": [{"date": str(d.date()), "close": float(r.close)} for d, r in frame.iterrows()], "return_pct": float(frame.adjusted.iloc[-1] / frame.adjusted.iloc[0] - 1)})
    # Evaluation describes the traded basket, not a majority vote of ETF labels.
    scene = "bull" if hold_stats["return_pct"] >= .10 else "bear" if hold_stats["return_pct"] <= -.05 else "sideways"
    return {**stats, "benchmark_return": hold_stats["return_pct"], "benchmark_drawdown": hold_stats["max_drawdown"], "excess_return": stats["return_pct"] - hold_stats["return_pct"], "trade_count": len(account.trades), "fees": account.fees, "start": str(timeline[0].date()), "end": str(timeline[-1].date()), "bars": len(timeline), "year": request.year if request.period == "year" else None, "scene": scene, "engine": config.engine, "model": config.model, "profile": config.profile, "auto_profile": config.auto_profile, "profile_selection": mode, "profile_trades": executed_profiles, "curve": curve, "symbol_curves": symbol_curves, "trades": account.trades, "positions": account.positions, "decisions": decisions, "training": reports, "warnings": warnings, "fallback_count": fallback_count, "seed": config.seed, "execution": "收盘生成信号，下一交易日开盘成交；ETF 手续费与滑点；分红再投资未模拟", "data_hash": hashlib.sha256("".join(s["data_hash"] for s in symbol_curves).encode()).hexdigest()[:20]}
