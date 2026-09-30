from __future__ import annotations

import copy
import math

import numpy as np
import pandas as pd

from ..v2.execution import Account
from ..v2.schema import Risk
from .forecast import Forecasts
from .schema import ASSETS, Plan, fingerprint
from .actions import applicable_splits
from .allocation import exposure_budget, trend_features
from .cost_allocation import cost_allocation


def risk_for(plan):
    return Risk(starting_cash=plan.starting_cash, max_trade_cash=plan.starting_cash * 100,
                max_order_shares=10**10, max_position_pct=1, stop_loss_pct=1, take_profit_pct=1000,
                max_daily_loss_pct=1, commission_rate=plan.commission_rate,
                min_commission=plan.min_commission, slippage_bps=plan.slippage_bps)


def capped_weights(candidates, exposure, cap):
    remaining = list(candidates)
    result, budget = {}, exposure
    def preference(row):
        return row["preference"] if "preference" in row else 1 / max(row["volatility"], .05)
    while remaining and budget > 1e-10:
        total = sum(preference(row) for row in remaining)
        provisional = {r["symbol"]: budget * preference(r) / total for r in remaining}
        limited = [r for r in remaining if provisional[r["symbol"]] > cap]
        if not limited:
            result.update(provisional)
            break
        for row in limited:
            result[row["symbol"]] = cap
            budget -= cap
            remaining.remove(row)
    return result


def hold_weights(plan):
    candidates = []
    for group, fraction in plan.allocation.items():
        symbols = [symbol for symbol in plan.symbols if ASSETS[symbol]["group"] == group]
        if symbols and fraction > 0:
            candidates.extend({"symbol": symbol, "preference": fraction / len(symbols)} for symbol in symbols)
    return capped_weights(candidates, plan.max_exposure, plan.max_weight)


class Portfolio:
    """One close-to-next-open state machine for backtests and forward simulation."""
    def __init__(self, store, plan: Plan, histories, method=None, restored=None,
                 progress=lambda *args: None, forecast_plan=None):
        self.plan, self.histories, self.method = plan, histories, method or plan.method
        if plan.allocation_policy != "legacy" and self.method not in {"ml", "rotation", "hold"}:
            raise ValueError("核心组合尚未验证此模型，请选择回归配方、趋势或持有基准")
        self.plan_key = fingerprint(plan)
        self.state = copy.deepcopy(restored or {})
        self.account = Account(risk_for(plan), self.state.get("account"),
                               max_gross_multiple=plan.research_multiple)
        self.state.setdefault("risk_peak", self.account.equity())
        self.state.setdefault("cooldown", 0)
        self.state.setdefault("period", None)
        self.state.setdefault("pending", None)
        self.state.setdefault("last_date", None)
        self.state.setdefault("curve", [])
        self.state.setdefault("decisions", [])
        self.state.setdefault("hold_plan_key", None)
        self.state.setdefault("rejections", [])
        self.state.setdefault("receivables", [])
        self.state.setdefault("dividends_paid", 0.)
        self.state.setdefault("interest_paid", 0.)
        self.state.setdefault("margin_exit", False)
        self.state.setdefault("margin_exit_at", None)
        self.splits = {s:applicable_splits(s,f) for s,f in histories.items()}
        self.actions = []
        for symbol, frame in histories.items():
            for date, ratio in self.splits[symbol].items():
                self.actions.append({'date':date,'symbol':symbol,'split':ratio})
            if 'dividend' in frame:
                for date, row in frame.loc[frame.dividend>0].iterrows():
                    self.actions.append({'date':str(date.date()),'symbol':symbol,'cash':float(row.dividend),'pay_date':str(row.pay_date)})
        self.actions.sort(key=lambda row:(row['date'], 0 if 'split' in row else 1))
        if self.method == "ml" and plan.model_recipe != "legacy":
            from .research_forecast import ResearchForecasts
            self.forecasts = ResearchForecasts(store, histories, progress, recipe=plan.model_recipe)
        else:
            self.forecasts = Forecasts(store, histories, forecast_plan or plan, progress, model=self.method) if self.method in {"ml", "ml_class", "cnn", "cnn_class"} else None
        self.signals = {}
        self.trends = {}
        self.returns = pd.DataFrame({s: f.adjusted.pct_change(fill_method=None) for s, f in histories.items()})
        for symbol, frame in histories.items():
            self.trends[symbol] = trend_features(frame)
            close = frame.adjusted
            self.signals[symbol] = pd.DataFrame({"m60": close.pct_change(60), "m120": close.pct_change(120),
                                                "above": close / close.rolling(120).mean() - 1,
                                                "volatility": close.pct_change().rolling(60).std(ddof=0) * math.sqrt(252)}, index=frame.index)

    def make_plan(self, date, force=False):
        if self.state["margin_exit"]:
            return {"date": str(date.date()), "weights": {}, "rows": [],
                    "reason": "模拟维保比例不足，按下次可成交价格减仓", "status": "margin_exit"}
        if self.method == "hold":
            return {"date": str(date.date()), "weights": hold_weights(self.plan), "rows": [],
                    "reason": "按相同板块偏好买入持有", "status": "baseline"}
        predictions, report = self.forecasts.at(date) if self.forecasts else ({}, None)
        joint = self.plan.allocation_policy != "legacy"
        rows = []
        for symbol in self.plan.symbols:
            frame = self.signals[symbol]
            position = int(frame.index.searchsorted(date, side="right")) - 1
            if position < 0:
                continue
            f = frame.iloc[position]
            ready = bool(np.isfinite(f).all())
            qualifies = ready and f.m60 > 0 and f.m120 > 0 and f.above > 0
            estimate = predictions.get(symbol)
            score = float((.6 * f.m60 + .4 * f.m120) / max(f.volatility, .05)) if ready else -1.
            reason = "趋势符合条件" if qualifies else "趋势未达到持有条件" if ready else "历史长度不足"
            strength = 1.
            if self.plan.trend_signal == "multi_horizon":
                trend = self.trends[symbol].iloc[position]
                ready = bool(np.isfinite(trend).all())
                strength = float(trend.strength) if ready else 0.
                qualifies = ready and strength >= .5
                score = float(trend.score / max(trend.volatility, .05)) if ready else -1.
                reason = f"四个趋势周期中 {int(strength * 4)} 个向上" if ready else "多周期预热不足一年"
                if self.plan.signal_policy in {"consensus", "fast_confirmed"}:
                    qualifies = qualifies and strength >= .75
                    if self.plan.signal_policy == "fast_confirmed":
                        close = self.histories[symbol].adjusted.iloc[:position+1]
                        qualifies = bool(qualifies and len(close) > 60 and
                                         close.iloc[-1] > close.iloc[-21] and close.iloc[-1] > close.tail(60).mean())
                    if not qualifies and ready:
                        reason = "多周期或近期趋势确认不足"
            if report and report["status"] == "model":
                accepted = estimate is not None and (estimate >= self.plan.classification_threshold if self.forecasts.classification else estimate > 0)
                if not joint:
                    qualifies = qualifies and accepted
                    score = estimate if estimate is not None else -1.
                if not joint and self.plan.model_recipe != "legacy" and self.method == "ml":
                    score = estimate / max(f.volatility, .05) if estimate is not None else -1.
                    if self.plan.signal_policy == "legacy":
                        strength = 1.
                    elif self.plan.signal_policy == "confirmed" and strength < .75:
                        qualifies = False
                        reason = "模型看涨但趋势确认不足三个周期"
                if joint:
                    reason += "；模型用于调整配置，不单独否决核心持仓"
                elif estimate is not None and not accepted:
                    reason = "上涨概率未达门槛" if self.forecasts.classification else "模型预测净收益非正"
            rows.append({"symbol": symbol, "name": ASSETS[symbol]["name"], "group": ASSETS[symbol]["group"],
                         "momentum": float(f.m60) if ready else None, "volatility": float(f.volatility) if ready else None,
                         "estimate": estimate, "score": score, "strength": strength,
                         "eligible": bool(qualifies), "reason": reason, "weight": 0.})
        selected, groups = [], set()
        for row in sorted(rows, key=lambda v: (-v["score"], v["symbol"])):
            if row["eligible"] and self.plan.allocation[row["group"]] <= 0:
                row["reason"] = "该板块分配偏好为零"
            elif row["eligible"] and row["group"] not in groups and len(selected) < self.plan.holdings:
                selected.append(row)
                groups.add(row["group"])
            elif row["eligible"]:
                row["reason"] = "同类资产或持仓数量已达上限"
        weights = capped_weights([{**row, "preference": self.plan.allocation[row["group"]]
                                  / max(row["volatility"], .05)}
                                  for row in selected], self.plan.max_exposure, self.plan.max_weight)
        weights = {row["symbol"]: weights[row["symbol"]] * row["strength"] for row in selected
                   if row["symbol"] in weights}
        estimates = predictions if report and report["status"] == "model" and not self.forecasts.classification else None
        if joint:
            from .core_allocation import core_allocation
            weights, budget = core_allocation(self.plan, selected, self.returns, date, self.equity(),
                self.state["risk_peak"], self.account.positions, estimates,
                getattr(self.forecasts, "horizon", self.plan.horizon))
        else:
            weights, budget = self.allocate(weights, date, estimates,
                                           getattr(self.forecasts, "horizon", self.plan.horizon))
        if self.state["cooldown"] > 0:
            weights = {}
            budget = {**budget, "target_gross": 0., "scale": 0., "reason": "回撤冷静期，目标为现金"}
        for row in rows:
            row["weight"] = weights.get(row["symbol"], 0.)
        return {"date": str(date.date()), "weights": weights, "rows": rows,
                "reason": "回撤暂停，持有现金" if self.state["cooldown"] else "按计划调仓",
                "status": report["status"] if report else "baseline", "model": report, "risk_budget": budget}

    def allocate(self, weights, date, estimates=None, horizon=None):
        cost = None
        if self.plan.execution_policy == "cost_aware":
            weights, cost = cost_allocation(self.plan, weights, self.returns, date,
                self.equity(), self.account.positions, estimates, horizon)
        weights, budget = exposure_budget(self.plan, weights, self.returns, date,
                                          self.equity(), self.state["risk_peak"])
        if cost is not None:
            budget["cost_decision"] = cost
        return weights, budget

    def review_risk(self, date):
        """Review held risk between scheduled entries; never add a position."""
        if self.plan.risk_review == "rebalance" or self.method == "hold" or not self.account.positions:
            return None
        period = str(date.date()) if self.plan.risk_review == "daily" else str(date.to_period("W"))
        if self.state.get("risk_review_period") == period:
            return None
        self.state["risk_review_period"] = period
        equity = self.equity()
        if equity <= 0:
            return None
        multiple = self.plan.research_multiple
        held = {s: p["quantity"] * p["price"] / equity / multiple
                for s, p in self.account.positions.items()}
        retained = {}
        removed = []
        for symbol, weight in held.items():
            history = self.trends[symbol].loc[:date]
            strength = history.iloc[-1].strength if len(history) else np.nan
            if np.isfinite(strength) and strength >= .5:
                retained[symbol] = weight
            else:
                removed.append(symbol)
        weights, budget = exposure_budget(self.plan, retained, self.returns, date,
                                          equity, self.state["risk_peak"])
        # A meaningful partial reduction avoids daily churn on tiny weight moves.
        if not removed and sum(held.values()) - sum(weights.values()) < self.plan.drift_band / multiple:
            return None
        return {"date": str(date.date()), "weights": weights, "rows": [],
                "reason": "持仓风险复核：趋势转弱或风险预算超限，仅减仓",
                "status": "risk_review", "reduce_only": True, "risk_budget": budget,
                "removed": removed}

    def accrue_interest(self, date):
        previous = self.state.get("interest_date") or self.state.get("last_date")
        if previous:
            days = max(0, (date - pd.Timestamp(previous)).days)
            interest = max(0., -self.account.cash) * self.plan.research_annual_rate * days / 365
            self.account.cash -= interest
            self.state["interest_paid"] += interest
        self.state["interest_date"] = str(date.date())

    def apply_actions(self, date):
        previous = self.state.get('actions_date') or str((date-pd.Timedelta(days=1)).date())
        current = str(date.date())
        # Small event calendars avoid scanning entire DataFrames on every day.
        for event in self.actions:
            symbol, day = event['symbol'], event['date']
            if not previous < day <= current or symbol not in self.account.positions:
                continue
            position = self.account.positions[symbol]
            if 'split' in event:
                ratio = event['split']
                rounding = math.floor if ratio < 1 else math.ceil
                before = position['quantity']
                position['quantity'] = rounding(before*ratio)
                position['available'] = rounding(position['available']*ratio)
                position['average_price'] *= before/position['quantity']
                position['price'] /= ratio
                self.state.setdefault('corporate_actions', []).append(event)
            else:
                amount = position['quantity']*event['cash']
                self.state['receivables'].append({'symbol':symbol,'ex_date':day,'pay_date':event['pay_date'],'amount':amount})
        for dividend in list(self.state['receivables']):
            if pd.Timestamp(dividend['pay_date']) <= date:
                self.account.cash += dividend['amount']
                self.state['dividends_paid'] += dividend['amount']
                self.state['receivables'].remove(dividend)
        self.state["actions_date"] = str(date.date())

    def equity(self):
        return self.account.equity() + sum(d['amount'] for d in self.state['receivables'])

    def execute(self, pending, date):
        self.apply_actions(date)
        if pending is None or pd.Timestamp(pending.get("execute_after", pending["date"])) >= date:
            return
        tradable = {s: f.loc[date] for s, f in self.histories.items() if date in f.index}
        for symbol, row in tradable.items():
            self.account.mark(symbol, row.open)
        equity = self.equity()
        gross = sum(p["quantity"] * p["price"] for p in self.account.positions.values())
        if equity <= 0 or gross and equity / gross < self.plan.research_maintenance_ratio:
            self.state["margin_exit"] = True
            self.state["margin_exit_at"] = self.state["margin_exit_at"] or str(date.date())
            pending = {**pending, "weights": {}, "reason": "模拟维保比例不足，开盘优先减仓"}
        desired = {s: int(max(0, equity) * self.plan.research_multiple * pending["weights"].get(s, 0.)
                          / float(r.open) // 100 * 100) for s, r in tradable.items()}
        orders = []
        for symbol, row in tradable.items():
            current = self.account.positions.get(symbol, {}).get("quantity", 0)
            if pending.get("reduce_only"):
                desired[symbol] = min(current, desired[symbol])
            if pending['weights'].get(symbol,0)>0 and desired[symbol]==0 and current==0:
                self.state['rejections'].append({'date':str(date.date()),'symbol':symbol,'reason':'目标金额不足最小买入单位（100份）'})
            delta = desired[symbol] - current
            weight_delta = abs(delta * row.open / equity) if equity > 0 else 1
            rebalance_existing = current > 0 or self.plan.execution_policy == "legacy"
            if delta == 0 or rebalance_existing and weight_delta < self.plan.drift_band and desired[symbol] > 0:
                continue
            side = "buy" if delta > 0 else "sell"
            history = self.histories[symbol]
            prior_position = int(history.index.searchsorted(date, side="left")) - 1
            if prior_position < 0:
                continue
            prior_date = history.index[prior_position]
            prior_bar = history.iloc[prior_position]
            reference = float(prior_bar.close)
            for day, ratio in self.splits[symbol].items():
                if prior_date < pd.Timestamp(day) <= date:
                    reference /= ratio
            limit = ASSETS[symbol]["limit"]
            rejected = None
            if row.volume <= 0:
                rejected = "当天没有有效成交量，不假定可成交"
            if side == "buy" and row.open >= round(reference * (1 + limit), 3) - .0005:
                rejected = "开盘接近涨停，未假定买入成功"
            if side == "sell" and row.open <= round(reference * (1 - limit), 3) + .0005:
                rejected = "开盘接近跌停，未假定卖出成功"
            volume_cap = int(float(prior_bar.volume) * self.plan.participation // 100 * 100)
            quantity = min(abs(delta), volume_cap)
            if quantity <= 0:
                rejected = "参考成交量不足"
            if rejected:
                self.state["rejections"].append({"date": str(date.date()), "symbol": symbol, "reason": rejected})
            else:
                orders.append((side, symbol, quantity, float(row.open)))
        for side, symbol, quantity, price in sorted(orders, key=lambda o: o[0] == "buy"):
            trade, reason = self.account.trade(symbol, side, price, str(date.date()), pending["reason"], self.method, quantity=quantity, allow_add=True)
            if trade:
                trade["signal_date"] = pending["date"]
            else:
                self.state["rejections"].append({"date": str(date.date()), "symbol": symbol, "reason": reason})

    def step(self, date, execute=True, *, make_decision=True):
        date = pd.Timestamp(date)
        if self.state["last_date"] and date <= pd.Timestamp(self.state["last_date"]):
            return
        # Charge the debt carried into today before today's fills repay or add borrowing.
        self.accrue_interest(date)
        self.account.new_day(str(date.date()))
        self.apply_actions(date)
        pending = self.state["pending"]
        if execute and pending and date > pd.Timestamp(pending.get("execute_after", pending["date"])):
            self.execute(pending, date)
            self.state["pending"] = None
        for symbol, frame in self.histories.items():
            if date in frame.index:
                self.account.mark(symbol, float(frame.loc[date].close))
        equity = self.equity()
        gross = sum(p["quantity"] * p["price"] for p in self.account.positions.values())
        if gross and (equity <= 0 or equity / gross < self.plan.research_maintenance_ratio):
            self.state["margin_exit"] = True
            self.state["margin_exit_at"] = self.state["margin_exit_at"] or str(date.date())
        elif not self.account.positions:
            self.state["margin_exit"] = False
        self.state["risk_peak"] = max(self.state["risk_peak"], equity)
        if self.state["cooldown"] > 0:
            self.state["cooldown"] -= 1
            if self.state["cooldown"] == 0:
                self.state["risk_peak"] = equity
                self.state["period"] = None
        if make_decision:
            self.decide(date, equity)
        self.state["curve"].append({"date": str(date.date()), "equity": equity, "cash": self.account.cash,
                                    "exposure": gross / equity if equity > 0 else 0.,
                                    "insolvent": equity <= 0, "cooldown": self.state["cooldown"]})
        self.state["last_date"] = str(date.date())
        self.state["account"] = self.account.dump()
        self.state['account']['equity'] = equity
        self.state['account']['dividends_paid'] = self.state['dividends_paid']
        self.state['account']['receivables'] = sum(d['amount'] for d in self.state['receivables'])
        self.state['account']['interest_paid'] = self.state['interest_paid']
        self.state['account']['gross_exposure'] = gross
        self.state['account']['borrowed'] = max(0., -self.account.cash)
        self.state["plan_key"] = self.plan_key

    def decide(self, date, equity):
        paused = self.method != "hold" and equity / self.state["risk_peak"] - 1 <= -self.plan.drawdown_pause
        if paused and self.state["cooldown"] == 0:
            self.state["cooldown"] = self.plan.cooldown_sessions
        period = (str(date.date()) if self.plan.rebalance == "daily" else
                  str(date.to_period("W" if self.plan.rebalance == "weekly" else "M")))
        due = (self.state["margin_exit"] or self.state["period"] != period and
               (self.method != "hold" or self.state["hold_plan_key"] != self.plan_key))
        if due or self.state["cooldown"] > 0 and self.account.positions:
            pending = self.make_plan(date)
            self.state["pending"] = pending
            self.state["decisions"].append(pending)
            self.state["period"] = period
            if self.method == "hold":
                self.state["hold_plan_key"] = self.plan_key
        else:
            review = self.review_risk(date)
            if review is not None:
                self.state["pending"] = review
                self.state["decisions"].append(review)


def statistics(state, starting):
    curve = state["curve"]
    values = np.array([starting] + [r["equity"] for r in curve])
    returns = np.divide(values[1:], values[:-1], out=np.ones(len(values) - 1),
                        where=values[:-1] != 0) - 1
    drawdown = values / np.maximum.accumulate(values) - 1
    years = ((pd.Timestamp(curve[-1]['date'])-pd.Timestamp(curve[0]['date'])).days+1)/365.25 if curve else 0
    annual = float((values[-1] / starting) ** (1 / years) - 1) if years > 0 and values[-1] > 0 else -1. if years > 0 else 0.
    vol = float(np.std(returns) * np.sqrt(252)) if len(returns) else 0.
    per_year = []
    previous = starting
    for year in sorted({r["date"][:4] for r in curve}):
        rows = [r for r in curve if r["date"].startswith(year)]
        v = np.array([previous] + [r["equity"] for r in rows])
        per_year.append({"year": int(year), "return_pct": float(v[-1] / previous - 1),
                         "max_drawdown": float((v / np.maximum.accumulate(v) - 1).min())})
        previous = float(v[-1])
    return {"return_pct": float(values[-1] / starting - 1), "annualized_return": annual,
            "max_drawdown": float(drawdown.min()), "volatility": vol,
            "sharpe": float(np.mean(returns) * 252 / vol) if vol > 0 else None,
            "trade_count": len(state["account"]["trades"]), "fees": state["account"]["fees"],
            "end_equity": float(values[-1]), "mean_exposure": float(np.mean([r["exposure"] for r in curve])) if curve else 0.,
            "dividends_paid": state.get('dividends_paid',0.),
            "interest_paid": state.get('interest_paid',0.),
            "insolvent": any(row.get("insolvent", False) for row in curve),
            "margin_exit_at": state.get('margin_exit_at'),
            "per_year": per_year}


def simulate(store, plan, histories, start, end, method=None, progress=lambda *args: None,
             forecast_plan=None):
    symbols = plan.symbols
    common = sorted(set.union(*(set(histories[s].index) for s in symbols)))
    dates = [d for d in common if pd.Timestamp(start) <= d <= pd.Timestamp(end)]
    previous = [d for d in common if d < pd.Timestamp(start)]
    if not dates or not previous:
        raise ValueError("缺少测试区间或决策预热行情")
    engine = Portfolio(store, plan, histories, method, progress=progress,
                       forecast_plan=forecast_plan)
    # A close before the test interval may create the first next-open order.
    engine.state["pending"] = engine.make_plan(previous[-1])
    if engine.method == "hold":
        engine.state["decisions"].append(engine.state["pending"])
        engine.state["hold_plan_key"] = engine.plan_key
    for index, date in enumerate(dates):
        engine.step(date)
        if index % 25 == 0:
            progress(20 + 75 * (index + 1) / len(dates), f"组合回放 {date.date()}")
    return {"method": engine.method, **statistics(engine.state, plan.starting_cash),
            "start": str(dates[0].date()), "end": str(dates[-1].date()), "curve": engine.state["curve"],
            "trades": engine.account.trades, "positions": engine.account.positions,
            "decisions": engine.state["decisions"], "rejections": engine.state["rejections"],
            "training": engine.forecasts.reports if engine.forecasts else []}
