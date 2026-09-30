from __future__ import annotations

import datetime as dt
import math


def daily_leverage_scenarios(curve, starting_cash, annual_borrow_rate, maintenance_ratio=.30):
    """Estimate daily-reset exposure with interest only on borrowed notional."""
    if not curve or not math.isfinite(starting_cash) or starting_cash <= 0:
        raise ValueError("缺少有效的基础资产曲线")
    if not math.isfinite(annual_borrow_rate) or not 0 <= annual_borrow_rate <= .3:
        raise ValueError("假设年化融资成本应在 0% 至 30% 之间")
    if not math.isfinite(maintenance_ratio) or not 0 <= maintenance_ratio <= .8:
        raise ValueError("假设维持担保比例应在 0% 至 80% 之间")

    returns = []
    previous_equity = starting_cash
    previous_date = None
    for point in curve:
        date = dt.date.fromisoformat(str(point["date"])[:10])
        equity = float(point["equity"])
        exposure = float(point.get("exposure", float("nan")))
        if (not math.isfinite(equity) or equity <= 0 or not math.isfinite(exposure)
                or not 0 <= exposure <= 1 or (previous_date and date <= previous_date)):
            raise ValueError("基础资产曲线日期、净值或持仓比例无效")
        returns.append((str(date), equity / previous_equity - 1, exposure))
        previous_equity, previous_date = equity, date

    scenarios = []
    for multiple in (1, 2, 3):
        equity = peak = starting_cash
        drawdown = worst_day = 0.0
        wiped_out_on = None
        margin_exit_on = None
        shortfall = financing_paid = borrowed_sum = borrowed_peak = 0.0
        points = []
        for date, base_return, exposure in returns:
            if wiped_out_on is None and margin_exit_on is None:
                opening = equity
                gross_fraction = multiple * exposure
                borrowed_fraction = max(0., gross_fraction - 1.)
                borrowed_sum += borrowed_fraction
                borrowed_peak = max(borrowed_peak, borrowed_fraction)
                interest = opening * borrowed_fraction * annual_borrow_rate / 252
                financing_paid += interest
                pnl = opening * multiple * base_return
                closing = opening + pnl - interest
                worst_day = min(worst_day, max(-1., closing / opening - 1.))
                if closing <= 0:
                    shortfall = max(0., -closing)
                    equity = 0.0
                    wiped_out_on = date
                else:
                    equity = closing
                    gross_close = max(0., opening * gross_fraction + pnl)
                    if (borrowed_fraction > 0 and gross_close > 0
                            and equity / gross_close < maintenance_ratio):
                        # Daily bars have no executable liquidation quote; cash is held from this close.
                        margin_exit_on = date
                peak = max(peak, equity)
                drawdown = min(drawdown, equity / peak - 1)
            points.append({"date": date, "equity": equity})
        scenarios.append({"multiple": multiple, "end_equity": equity,
                          "return_pct": equity / starting_cash - 1,
                          "max_drawdown": drawdown, "worst_day": worst_day,
                          "wiped_out_on": wiped_out_on, "margin_exit_on": margin_exit_on,
                          "shortfall": shortfall, "financing_paid": financing_paid,
                          "mean_borrow_fraction": borrowed_sum / len(returns),
                          "peak_borrow_fraction": borrowed_peak, "curve": points})
    return scenarios
