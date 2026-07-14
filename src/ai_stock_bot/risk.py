from __future__ import annotations

from .models import Account, MarketSnapshot, Order, Position, Side, Signal


class RiskManager:
    def __init__(
        self,
        max_position_pct: float,
        max_trade_cash: float,
        max_order_shares: int,
        max_daily_loss_pct: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        allow_add_position: bool = False,
    ) -> None:
        self.max_position_pct = max_position_pct
        self.max_trade_cash = max_trade_cash
        self.max_order_shares = max_order_shares
        self.max_daily_loss_pct = max_daily_loss_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.allow_add_position = allow_add_position

    def build_order(
        self,
        account: Account,
        snapshot: MarketSnapshot,
        signal: Signal,
        position: Position | None,
    ) -> Order | None:
        if account.equity <= account.day_start_equity * (1 - self.max_daily_loss_pct):
            return None

        forced = self._forced_exit(snapshot, position)
        if forced is not None:
            return forced

        if signal.confidence < 0.60 or signal.side == Side.HOLD:
            return None

        if signal.side == Side.SELL:
            if position is None:
                return None
            quantity = min(position.quantity, self.max_order_shares)
            return Order(snapshot.symbol, Side.SELL, quantity, snapshot.last_price, signal.reason)

        if position is not None and position.quantity > 0 and not self.allow_add_position:
            return None

        max_dollars = account.equity * self.max_position_pct
        current_dollars = 0.0 if position is None else position.quantity * snapshot.last_price
        available_dollars = max(
            0.0,
            min(max_dollars - current_dollars, account.cash, self.max_trade_cash),
        )
        quantity = min(int(available_dollars // snapshot.last_price), self.max_order_shares)
        if quantity <= 0:
            return None
        return Order(snapshot.symbol, Side.BUY, quantity, snapshot.last_price, signal.reason)

    def _forced_exit(self, snapshot: MarketSnapshot, position: Position | None) -> Order | None:
        if position is None or position.quantity <= 0:
            return None
        pnl_pct = (snapshot.last_price - position.average_price) / position.average_price
        if pnl_pct <= -self.stop_loss_pct:
            return Order(
                snapshot.symbol,
                Side.SELL,
                min(position.quantity, self.max_order_shares),
                snapshot.last_price,
                f"Stop loss triggered: {pnl_pct:.2%}",
            )
        if pnl_pct >= self.take_profit_pct:
            return Order(
                snapshot.symbol,
                Side.SELL,
                min(position.quantity, self.max_order_shares),
                snapshot.last_price,
                f"Take profit triggered: {pnl_pct:.2%}",
            )
        return None
