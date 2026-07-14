from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Protocol

from .models import Account, Order, Position, Side
from .symbols import to_cn_symbol


class Broker(Protocol):
    def mark_price(self, symbol: str, price: float) -> None:
        ...

    def get_account(self) -> Account:
        ...

    def get_position(self, symbol: str) -> Position | None:
        ...

    def submit_order(self, order: Order) -> None:
        ...


class PaperBroker:
    def __init__(self, starting_cash: float, log_orders: bool = True) -> None:
        self.cash = starting_cash
        self.day_start_equity = starting_cash
        self.positions: dict[str, Position] = {}
        self.last_prices: dict[str, float] = {}
        self.log_orders = log_orders

    def mark_price(self, symbol: str, price: float) -> None:
        self.last_prices[symbol] = price

    def get_account(self) -> Account:
        market_value = sum(
            position.quantity * self.last_prices.get(symbol, position.average_price)
            for symbol, position in self.positions.items()
        )
        equity = self.cash + market_value
        return Account(cash=self.cash, equity=equity, day_start_equity=self.day_start_equity)

    def get_position(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)

    def submit_order(self, order: Order) -> None:
        if order.quantity <= 0:
            return
        price = order.limit_price
        if order.side == Side.BUY:
            cost = price * order.quantity
            if cost > self.cash:
                raise ValueError(f"Not enough paper cash for {order.symbol}: need {cost:.2f}, have {self.cash:.2f}")
            current = self.positions.get(order.symbol)
            if current is None:
                self.positions[order.symbol] = Position(order.symbol, order.quantity, price)
            else:
                total_qty = current.quantity + order.quantity
                current.average_price = ((current.average_price * current.quantity) + cost) / total_qty
                current.quantity = total_qty
            self.cash -= cost
        elif order.side == Side.SELL:
            current = self.positions.get(order.symbol)
            if current is None:
                return
            sell_qty = min(order.quantity, current.quantity)
            self.cash += price * sell_qty
            current.quantity -= sell_qty
            if current.quantity == 0:
                del self.positions[order.symbol]
        else:
            return

        if self.log_orders:
            print(
                f"ORDER {order.side.value.upper()} {order.symbol} x{order.quantity} "
                f"@ {price:.2f} | {order.reason}"
            )


class AlpacaBroker:
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool,
        order_type: str = "limit",
    ) -> None:
        from alpaca.trading.client import TradingClient

        if order_type not in {"limit", "market"}:
            raise ValueError("ORDER_TYPE must be 'limit' or 'market'")
        self.client = TradingClient(api_key, secret_key, paper=paper)
        self.paper = paper
        self.order_type = order_type
        self.last_prices: dict[str, float] = {}

    def mark_price(self, symbol: str, price: float) -> None:
        self.last_prices[symbol] = price

    def get_account(self) -> Account:
        raw = self.client.get_account()
        equity = float(raw.equity)
        day_start_equity = float(getattr(raw, "last_equity", None) or raw.equity)
        return Account(
            cash=float(raw.cash),
            equity=equity,
            day_start_equity=day_start_equity,
        )

    def get_position(self, symbol: str) -> Position | None:
        try:
            raw = self.client.get_open_position(symbol)
        except Exception:
            return None
        return Position(
            symbol=symbol,
            quantity=int(float(raw.qty)),
            average_price=float(raw.avg_entry_price),
        )

    def submit_order(self, order: Order) -> None:
        if order.quantity <= 0 or order.side == Side.HOLD:
            return

        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        side = OrderSide.BUY if order.side == Side.BUY else OrderSide.SELL
        if self.order_type == "market":
            request = MarketOrderRequest(
                symbol=order.symbol,
                qty=order.quantity,
                side=side,
                time_in_force=TimeInForce.DAY,
            )
        else:
            request = LimitOrderRequest(
                symbol=order.symbol,
                qty=order.quantity,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=self._round_price(order.limit_price),
            )

        submitted = self.client.submit_order(order_data=request)
        mode = "PAPER" if self.paper else "LIVE"
        print(
            f"ALPACA {mode} ORDER {order.side.value.upper()} {order.symbol} "
            f"x{order.quantity} type={self.order_type} id={submitted.id} | {order.reason}"
        )

    def _round_price(self, price: float) -> float:
        decimal_price = Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        return float(decimal_price)


class QmtBroker:
    def __init__(
        self,
        qmt_path: str,
        account_id: str,
        account_type: str = "STOCK",
        session_id: int = 123456,
        order_type: str = "limit",
    ) -> None:
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount

        if order_type not in {"limit", "market"}:
            raise ValueError("ORDER_TYPE must be 'limit' or 'market'")
        self.order_type = order_type
        self.trader = XtQuantTrader(qmt_path, session_id)
        self.account = StockAccount(account_id, account_type)
        self.last_prices: dict[str, float] = {}

        self.trader.start()
        result = self.trader.connect()
        if result != 0:
            raise RuntimeError(f"QMT connect failed with code: {result}")
        subscribe_result = self.trader.subscribe(self.account)
        if subscribe_result != 0:
            raise RuntimeError(f"QMT account subscribe failed with code: {subscribe_result}")

    def mark_price(self, symbol: str, price: float) -> None:
        self.last_prices[to_cn_symbol(symbol, "qmt")] = price

    def get_account(self) -> Account:
        raw = self.trader.query_stock_asset(self.account)
        if raw is None:
            raise RuntimeError("QMT query_stock_asset returned no account data")
        cash = float(getattr(raw, "cash", 0.0))
        equity = float(
            getattr(raw, "total_asset", None)
            or getattr(raw, "asset", None)
            or getattr(raw, "market_value", 0.0) + cash
        )
        return Account(cash=cash, equity=equity, day_start_equity=equity)

    def get_position(self, symbol: str) -> Position | None:
        qmt_symbol = to_cn_symbol(symbol, "qmt")
        positions = self.trader.query_stock_positions(self.account) or []
        for raw in positions:
            if str(getattr(raw, "stock_code", "")).upper() != qmt_symbol.upper():
                continue
            available_qty = int(float(getattr(raw, "can_use_volume", 0) or 0))
            total_qty = int(float(getattr(raw, "volume", 0) or 0))
            qty = available_qty if available_qty > 0 else total_qty
            if qty <= 0:
                return None
            avg_price = float(
                getattr(raw, "avg_price", None)
                or getattr(raw, "open_price", None)
                or self.last_prices.get(qmt_symbol, 0.0)
            )
            return Position(qmt_symbol, qty, avg_price)
        return None

    def submit_order(self, order: Order) -> None:
        if order.quantity <= 0 or order.side == Side.HOLD:
            return

        from xtquant import xtconstant

        qmt_symbol = to_cn_symbol(order.symbol, "qmt")
        side = xtconstant.STOCK_BUY if order.side == Side.BUY else xtconstant.STOCK_SELL
        if self.order_type == "market":
            price_type = getattr(xtconstant, "LATEST_PRICE", 5)
            price = 0.0
        else:
            price_type = getattr(xtconstant, "FIX_PRICE", 11)
            price = self._round_price(order.limit_price)

        order_id = self.trader.order_stock(
            self.account,
            qmt_symbol,
            side,
            int(order.quantity),
            price_type,
            price,
            "ai_stock_bot",
            order.reason[:120],
        )
        print(
            f"QMT ORDER {order.side.value.upper()} {qmt_symbol} x{order.quantity} "
            f"type={self.order_type} id={order_id} | {order.reason}"
        )

    def _round_price(self, price: float) -> float:
        decimal_price = Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        return float(decimal_price)


class PTradeBroker:
    """Adapter for code running inside a PTrade strategy environment."""

    def __init__(self, account_cash: float, day_start_equity: float, order_type: str = "market") -> None:
        if order_type not in {"market", "limit"}:
            raise ValueError("ORDER_TYPE must be 'limit' or 'market'")
        self.account_cash = account_cash
        self.day_start_equity = day_start_equity
        self.order_type = order_type
        self.last_prices: dict[str, float] = {}

    def mark_price(self, symbol: str, price: float) -> None:
        self.last_prices[to_cn_symbol(symbol, "ptrade")] = price

    def get_account(self) -> Account:
        portfolio = self._ptrade_global("context", required=False)
        if portfolio is not None and hasattr(portfolio, "portfolio"):
            portfolio = portfolio.portfolio
        cash = float(getattr(portfolio, "cash", self.account_cash))
        equity = float(getattr(portfolio, "total_value", cash))
        return Account(cash=cash, equity=equity, day_start_equity=self.day_start_equity)

    def get_position(self, symbol: str) -> Position | None:
        get_position = self._ptrade_global("get_position")
        ptrade_symbol = to_cn_symbol(symbol, "ptrade")
        raw = get_position(ptrade_symbol)
        qty = int(float(getattr(raw, "enable_amount", None) or getattr(raw, "amount", 0) or 0))
        if qty <= 0:
            return None
        avg_price = float(
            getattr(raw, "cost_basis", None)
            or getattr(raw, "avg_cost", None)
            or self.last_prices.get(ptrade_symbol, 0.0)
        )
        return Position(ptrade_symbol, qty, avg_price)

    def submit_order(self, order: Order) -> None:
        if order.quantity <= 0 or order.side == Side.HOLD:
            return
        ptrade_order = self._ptrade_global("order")
        ptrade_symbol = to_cn_symbol(order.symbol, "ptrade")
        signed_qty = order.quantity if order.side == Side.BUY else -order.quantity
        if self.order_type == "limit":
            style_factory = self._ptrade_global("LimitOrderStyle", required=False)
            if style_factory is not None:
                ptrade_order(ptrade_symbol, signed_qty, style=style_factory(self._round_price(order.limit_price)))
            else:
                raise RuntimeError("PTrade LimitOrderStyle is not available in this environment")
        else:
            ptrade_order(ptrade_symbol, signed_qty)
        print(f"PTRADE ORDER {order.side.value.upper()} {ptrade_symbol} x{order.quantity} | {order.reason}")

    def _round_price(self, price: float) -> float:
        decimal_price = Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        return float(decimal_price)

    def _ptrade_global(self, name: str, required: bool = True):
        import builtins
        import __main__

        value = getattr(builtins, name, None) or getattr(__main__, name, None)
        if value is None and required:
            raise RuntimeError(
                f"PTrade API '{name}' was not found. Run this adapter inside the PTrade strategy environment."
            )
        return value
