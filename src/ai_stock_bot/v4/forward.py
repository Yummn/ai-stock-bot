from __future__ import annotations

import datetime as dt

import pandas as pd

from .data import audit


def checked_daily(histories, plan, now):
    end = now.date() if now.hour >= 16 else now.date() - dt.timedelta(days=1)
    frames = {s: histories[s].loc[:str(end)].copy() for s in plan.symbols}
    checked = audit(frames, plan.symbols)
    if not checked['ok']:
        raise ValueError('行情检查未通过：' + '；'.join(checked['blockers']))
    dates = [f.index[-1] for f in frames.values()]
    if len(set(dates)) != 1:
        raise ValueError('交易池最新行情日期不一致，暂停生成计划')
    latest = dates[0]
    if (now.date() - latest.date()).days > 7:
        raise ValueError(f'缓存只更新至 {latest.date()}，请先更新行情')
    return frames, checked, latest


def advance_daily(engine, latest, now):
    """Replay saved orders only; issue new decisions at this actual receipt, never in a gap."""
    previous = engine.state.get('last_date')
    if not previous:
        dates = [latest]
    else:
        dates = sorted({d for frame in engine.histories.values() for d in frame.index
                        if pd.Timestamp(previous) < d <= latest})
    for date in dates:
        before = len(engine.account.trades)
        engine.step(date, execute=bool(previous), make_decision=date == latest)
        engine.state['curve'][-1].update(observed_at=now.isoformat(),
                                        reconstructed=date.date() < now.date())
        for trade in engine.account.trades[before:]:
            trade['recorded_at'] = now.isoformat()
            trade['fill_source'] = 'daily_open_replay'
    pending = engine.state.get('pending')
    if pending and 'generated_at' not in pending:
        pending.update(generated_at=now.isoformat(), execute_after=str(now.date()))
    return len(dates)
