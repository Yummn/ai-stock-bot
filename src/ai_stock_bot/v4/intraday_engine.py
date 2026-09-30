from __future__ import annotations

import copy
import datetime as dt
import math
import statistics

from ..v2.execution import Account
from .portfolio import risk_for
from .schema import ASSETS
from .intraday_quality import continuous


def minutes(plan):
    return 30 if plan.research_interval == "30m" else 60


def lookback(plan):
    return 8 * 60 // minutes(plan)


def signal(rows, plan, holding=False, equity=None, forecast=None):
    """Completed bars only; momentum is a filter, not a predicted return."""
    count = lookback(plan)
    if len(rows) < count:
        return {"weight": 0., "reason": "盘中信号预热中"}
    recent = rows[-count:]
    closes = [float(row["close"]) for row in recent]
    scale = 60 // minutes(plan)
    short = statistics.mean(closes[-3 * scale:])
    long = statistics.mean(closes)
    momentum = closes[-1] / closes[-1 - 4 * scale] - 1
    base = min(plan.max_exposure, plan.max_weight)
    ceiling = base * plan.research_multiple
    if plan.intraday_strategy == "legacy":
        buy = short > long * 1.0015 and momentum > .002
        return {"weight": base if buy else 0., "reason": "原始均线动量信号", "momentum": momentum}
    if not continuous(rows[-count:], plan.research_interval):
        return {"weight": 0., "reason": "连续交易时段缺少 K 线，暂停新增信号"}
    notional = max(100., (equity or plan.starting_cash) * ceiling)
    roundtrip = 2 * (max(plan.commission_rate, plan.min_commission / notional)
                     + plan.slippage_bps / 10000)
    roundtrip += max(0., ceiling - 1) / max(ceiling, .01) * plan.research_annual_rate * 2 / 365
    threshold = max(.002, roundtrip * plan.intraday_cost_buffer)
    # A wider exit band avoids reversing an entry on tiny price oscillations.
    buy = (short >= long * .9985 and momentum >= -threshold / 2 if holding else
           short > long * 1.0015 and momentum > threshold)
    model_report = None
    if plan.intraday_strategy == "breakout":
        count = 20 * scale
        if len(rows) < count + 1 or not continuous(rows[-count-1:], plan.research_interval):
            return {"weight": 0., "reason": "通道窗口不足或缺失 K 线"}
        channel = rows[-count-1:-1]
        upper = max(float(r["high"]) for r in channel)
        lower = min(float(r["low"]) for r in channel[-8 * scale:])
        buy = closes[-1] >= lower if holding else closes[-1] > upper and momentum > threshold
    elif plan.intraday_strategy == "ml_edge":
        if forecast is None:
            from .intraday_forecast import IntradayForecast
            forecast = IntradayForecast(plan)
        model_report = forecast.at(rows)
        prediction = model_report["gross_prediction"]
        lower = prediction - model_report["disagreement"] if prediction is not None else None
        buy = lower is not None and lower > (0. if holding else roundtrip * plan.intraday_cost_buffer)
    returns = [b / a - 1 for a, b in zip(closes, closes[1:])]
    true_ranges = [max(float(row["high"]) - float(row["low"]),
                       abs(float(row["high"]) - previous),
                       abs(float(row["low"]) - previous)) / previous
                   for previous, row in zip(closes, recent[1:])]
    daily_move = max(statistics.pstdev(returns), statistics.mean(true_ranges)) * math.sqrt(240 / minutes(plan))
    cap = min(ceiling, plan.intraday_risk_budget / max(daily_move, .0001))
    weight = cap / plan.research_multiple if buy else 0.
    reason = ("盘中模型净优势通过" if buy else "模型净优势不足或训练样本不足") if model_report else (
        "通道突破持有" if buy else "通道突破未确认") if plan.intraday_strategy == "breakout" else (
        "趋势保持，按波动限仓" if holding and buy else "动量超过成本门槛，按波动限仓" if buy else "动量或趋势未通过成本过滤")
    return {"weight": weight, "momentum": momentum, "cost_floor": roundtrip,
            "entry_threshold": threshold, "daily_move": daily_move,
            "gross_target": cap if buy else 0.,
            "reason": reason, "model": model_report}


class IntradayEngine:
    """The same fills, costs and risk state for historical and forward paper runs."""
    def __init__(self, plan, restored=None, stress=False):
        self.plan = plan
        self.symbol = plan.research_symbol
        self.state = copy.deepcopy(restored or {})
        defaults = {"curve": [], "decisions": [], "rejections": [], "pending": None,
                    "interest_paid": 0., "slippage_paid": 0., "turnover": 0.,
                    "margin_exit": False, "margin_exit_at": None,
                    "risk_peak": plan.starting_cash, "cooldown_until": None,
                    "interest_date": None}
        for key, value in defaults.items():
            self.state.setdefault(key, value)
        if self.state["interest_date"] is None and restored:
            charged = restored.get("charged_dates") or []
            self.state["interest_date"] = max(charged) if charged else (restored.get("last_date") or "")[:10] or None
        execution = plan.model_copy(update={
            "commission_rate": plan.commission_rate * (2 if stress else 1),
            "min_commission": plan.min_commission * (2 if stress else 1),
            "slippage_bps": plan.slippage_bps * (2 if stress else 1),
        })
        self.account = Account(risk_for(execution), self.state.get("account"),
                               max_gross_multiple=plan.research_multiple)
        self.state.setdefault("mode", plan.research_interval)
        self.forecast = None
        if plan.intraday_strategy == "ml_edge":
            from .intraday_forecast import IntradayForecast
            self.forecast = IntradayForecast(plan)

    def decision(self, rows):
        return signal(rows, self.plan, bool(self.account.positions), self.account.equity(), self.forecast)

    def gross(self):
        return sum(p["quantity"] * p["price"] for p in self.account.positions.values())

    def accrue(self, through):
        previous = self.state.get("interest_date")
        if previous is None:
            self.state["interest_date"] = str(through)
            return
        days = (through - dt.date.fromisoformat(previous)).days
        if days > 0:
            # Calendar-day financing includes weekends, even during offline gaps.
            charge = max(0., -self.account.cash) * ((1 + self.plan.research_annual_rate / 365) ** days - 1)
            self.account.cash -= charge
            self.state["interest_paid"] += charge
            self.state["interest_date"] = str(through)

    def margin_check(self, stamp):
        gross, equity = self.gross(), self.account.equity()
        if gross and (equity <= 0 or equity / gross < self.plan.research_maintenance_ratio):
            self.state["margin_exit"] = True
            self.state["margin_exit_at"] = self.state["margin_exit_at"] or stamp
        return self.state["margin_exit"] or equity <= 0

    def step(self, row, prior_rows, *, make_decision=True, issued_at=None, record=True, hold=False):
        stamp = row["stamp"]
        opening_time = dt.datetime.fromisoformat(stamp) - dt.timedelta(minutes=minutes(self.plan))
        day = dt.date.fromisoformat(stamp[:10])
        self.accrue(day - dt.timedelta(days=1))
        self.account.new_day(str(day))
        self.account.mark(self.symbol, float(row["open"]))
        forced = self.margin_check(stamp)
        pending = self.state.get("pending")
        if pending and opening_time >= dt.datetime.fromisoformat(pending["generated_at"]):
            self.execute(row, prior_rows, pending, forced)
            self.state["pending"] = None
        self.account.mark(self.symbol, float(row["close"]))
        if stamp[11:16] == "15:00":
            self.accrue(day)
        forced = self.margin_check(stamp)
        equity = self.account.equity()
        self.state["risk_peak"] = max(self.state["risk_peak"], equity)
        cooldown = self.state.get("cooldown_until")
        if cooldown and stamp >= cooldown:
            self.state["cooldown_until"] = None
            self.state["risk_peak"] = equity
        if (not hold and not self.state["cooldown_until"] and
                equity <= self.state["risk_peak"] * (1 - self.plan.drawdown_pause)):
            # Cooldown is measured in observed sessions, not assumed weekend bars.
            self.state["cooldown_until"] = "9999"
            self.state["cooldown_dates"] = []
        if self.state.get("cooldown_until") == "9999":
            dates = self.state.setdefault("cooldown_dates", [])
            if not dates or dates[-1] != str(day):
                dates.append(str(day))
            if len(dates) > self.plan.cooldown_sessions:
                self.state["cooldown_until"] = None
                self.state["risk_peak"] = equity
        if make_decision:
            decision = ({"weight": min(self.plan.max_exposure, self.plan.max_weight), "reason": "同预算持有"}
                        if hold else self.decision(prior_rows + [row]))
            if forced or self.state["cooldown_until"]:
                decision = {"weight": 0., "reason": "维保不足或回撤暂停，等待下一可成交开盘"}
            # Preserve an already-issued order until its eligible candle arrives.
            # Otherwise a delayed feed continually replaces it before it can fill.
            risk_exit = forced or self.state["cooldown_until"]
            if self.state["pending"] is None or risk_exit and self.state["pending"].get("weight", 0) > 0:
                self.state["pending"] = {**decision, "buy": decision["weight"] > 0, "stamp": stamp,
                                          "hold": hold, "generated_at": issued_at or stamp}
            active = self.state["pending"]
            self.state["decisions"].append({"date": stamp, "weights": {self.symbol: active["weight"]},
                                            "status": "intraday", "reason": active["reason"],
                                            "model": decision.get("model")})
        self.state["last_date"] = stamp
        if record:
            self.state["curve"].append({"date": stamp, "equity": equity, "cash": self.account.cash,
                                        "exposure": self.gross() / equity if equity > 0 else 0.})
        self.dump()

    def execute(self, row, prior_rows, pending, forced):
        if not prior_rows:
            return
        opening = float(row["open"])
        equity = self.account.equity()
        position = self.account.positions.get(self.symbol, {}).get("quantity", 0)
        if pending.get("hold") and position and not forced:
            return
        weight = pending.get("weight", min(self.plan.max_exposure, self.plan.max_weight) if pending.get("buy") else 0.)
        if forced:
            weight = 0.
        risk = self.account.risk
        fill = opening * (1 + risk.slippage_bps / 10000)
        target_cash = max(0., equity) * weight * self.plan.research_multiple
        wanted = min(target_cash / (fill * (1 + risk.commission_rate)), max(0., target_cash - risk.min_commission) / fill)
        target = math.floor(wanted / 100) * 100
        delta = target - position
        # Keep existing positions until exit unless reducing risk materially.
        if position and weight > 0 and (delta >= 0 or abs(delta * opening) < max(0, equity) * self.plan.drift_band):
            return
        if not delta:
            return
        side = "buy" if delta > 0 else "sell"
        cap = math.floor(float(prior_rows[-1]["volume"]) * self.plan.participation / 100) * 100
        previous_days = [r for r in prior_rows if r["stamp"][:10] < row["stamp"][:10]]
        reference = float(previous_days[-1]["close"]) if previous_days else None
        blocked = (reference is not None and (opening >= round(reference * (1 + ASSETS[self.symbol]["limit"]), 3) - .0005
                   if side == "buy" else opening <= round(reference * (1 - ASSETS[self.symbol]["limit"]), 3) + .0005))
        if cap < 100 or float(row["volume"]) <= 0 or blocked:
            self.state["rejections"].append({"date": row["stamp"], "symbol": self.symbol,
                                            "reason": "涨跌停或成交量不足，未假定成交"})
            return
        trade, reason = self.account.trade(self.symbol, side, opening, row["stamp"][:10],
                                          "模拟维保减仓" if forced else pending.get("reason", "盘中信号"),
                                          "intraday", quantity=min(abs(delta), cap), allow_add=True)
        if trade:
            trade.update(stamp=row["stamp"], signal_date=pending["stamp"], forced=bool(forced))
            self.state["slippage_paid"] += abs(trade["price"] - opening) * trade["quantity"]
            self.state["turnover"] += trade["quantity"] * trade["price"]
        else:
            self.state["rejections"].append({"date": row["stamp"], "symbol": self.symbol, "reason": reason})

    def dump(self):
        self.state["account"] = {**self.account.dump(), "interest_paid": self.state["interest_paid"],
                                 "borrowed": max(0., -self.account.cash), "gross_exposure": self.gross()}
        return self.state
