from __future__ import annotations

import math

from .schema import Risk


T0 = {"518880.SH", "159934.SZ", "511260.SH", "513100.SH", "513500.SH"}


class Account:
    def __init__(self, risk: Risk, restored=None, *, max_gross_multiple=1):
        self.risk = risk
        self.max_gross_multiple = max_gross_multiple
        restored = restored or {}
        self.cash = float(restored.get("cash", risk.starting_cash))
        self.positions = restored.get("positions", {})
        self.trades = restored.get("trades", [])
        self.fees = float(restored.get("fees", 0))
        self.last_signal_date = restored.get("last_signal_date")
        self.settlement_date = restored.get("settlement_date", self.last_signal_date)

    def equity(self):
        return self.cash + sum(p["quantity"] * p["price"] for p in self.positions.values())

    def new_day(self, date=None):
        if date is not None:
            date = str(date)
            if self.settlement_date and date <= self.settlement_date:
                return
            self.settlement_date = date
        for p in self.positions.values():
            p["available"] = p["quantity"]

    def mark(self, symbol, price):
        if symbol in self.positions:
            self.positions[symbol]["price"] = float(price)

    def trade(self, symbol, side, price, date, reason, profile, quantity=None, day_start=None, allow_add=False):
        self.new_day(date)
        if not math.isfinite(price) or price <= 0:
            return None, "无有效成交价"
        p = self.positions.get(symbol)
        r = self.risk
        fill = float(price) * (1 + r.slippage_bps / 10000 * (1 if side == "buy" else -1))
        if side == "buy":
            if day_start and self.equity() <= day_start * (1 - r.max_daily_loss_pct):
                return None, "当日亏损限制，停止新增买入"
            if p and not allow_add:
                return None, "当前方案不重复加仓"
            equity = self.equity()
            gross = sum(position["quantity"] * position["price"] for position in self.positions.values())
            remaining = max(0, equity * r.max_position_pct * self.max_gross_multiple - (p["quantity"] * price if p else 0))
            buying_power = (self.cash if self.max_gross_multiple == 1 else
                            max(0, equity * self.max_gross_multiple - gross))
            budget = min(r.max_trade_cash, buying_power, remaining)
            max_qty = min(int(budget / (fill * (1 + r.commission_rate))), int(max(0, budget - r.min_commission) / fill), r.max_order_shares)
            wanted = min(max_qty, quantity) if quantity is not None else max_qty
            qty = int(wanted // 100 * 100)
            if qty <= 0:
                return None, "金额或仓位不足一个买入单位（100份）"
            fee = max(r.min_commission, qty * fill * r.commission_rate)
            cost = qty * fill + fee
            if not p:
                p = {"quantity": 0, "average_price": 0., "available": 0, "price": price}
                self.positions[symbol] = p
            p["average_price"] = (p["average_price"] * p["quantity"] + cost) / (p["quantity"] + qty)
            p["quantity"] += qty
            if symbol in T0:
                p["available"] += qty
            self.cash -= cost
        else:
            if not p:
                return None, "没有持仓"
            qty = min(p.get("available", 0), r.max_order_shares, quantity if quantity is not None else p["quantity"])
            qty = int(qty)
            if qty <= 0:
                return None, "当日买入尚不可卖（T+1）"
            fee = max(r.min_commission, qty * fill * r.commission_rate)
            self.cash += qty * fill - fee
            p["quantity"] -= qty
            p["available"] -= qty
            if p["quantity"] == 0:
                del self.positions[symbol]
        self.fees += fee
        row = {"date": str(date), "symbol": symbol, "side": side, "quantity": qty, "price": fill, "fee": fee, "reason": reason, "profile": profile}
        self.trades.append(row)
        return row, "已成交"

    def stop_reason(self, symbol, price):
        p = self.positions.get(symbol)
        if not p:
            return None
        change = price / p["average_price"] - 1
        return "止损" if change <= -self.risk.stop_loss_pct else "止盈" if change >= self.risk.take_profit_pct else None

    def dump(self):
        return {"cash": self.cash, "equity": self.equity(), "positions": self.positions, "trades": self.trades, "fees": self.fees, "last_signal_date": self.last_signal_date, "settlement_date": self.settlement_date}

    def decision_context(self, as_of):
        positions = {}
        equity = self.equity()
        for symbol, p in self.positions.items():
            opened = next((t["date"] for t in reversed(self.trades) if t["symbol"] == symbol and t["side"] == "buy" and t["date"] <= str(as_of)), None)
            positions[symbol] = {**p, "last_buy_date": opened, "weight": p["quantity"] * p["price"] / equity if equity else 0., "unrealized_return": p["price"] / p["average_price"] - 1 if p["average_price"] else 0.}
        return {"as_of": str(as_of), "cash": self.cash, "equity": equity, "positions": positions}
