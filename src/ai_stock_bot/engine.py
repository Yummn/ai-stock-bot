from __future__ import annotations

from .ai import CodexSignalProvider, HeuristicSignalProvider, OpenAISignalProvider, SignalProvider
from .broker import AlpacaBroker, Broker, PaperBroker, PTradeBroker, QmtBroker
from .config import Settings
from .market_data import AkShareMarketData, FallbackMarketData, MarketData, TushareMarketData, YahooMarketData
from .models import MarketSnapshot
from .risk import RiskManager


class TradingEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.market_data = self._build_market_data(settings)
        self.broker = self._build_broker(settings)
        self.ai = self._build_ai(settings)
        self.risk = RiskManager(
            settings.max_position_pct,
            settings.max_trade_cash,
            settings.max_order_shares,
            settings.max_daily_loss_pct,
            settings.stop_loss_pct,
            settings.take_profit_pct,
        )

    def run_once(self) -> None:
        snapshots = self._load_snapshots()
        signals = {signal.symbol: signal for signal in self.ai.decide(snapshots)}

        for snapshot in snapshots:
            self.broker.mark_price(snapshot.symbol, snapshot.last_price)
            signal = signals.get(snapshot.symbol)
            if signal is None:
                continue

            position = self.broker.get_position(snapshot.symbol)
            account = self.broker.get_account()
            order = self.risk.build_order(account, snapshot, signal, position)
            print(
                f"{snapshot.symbol}: price={snapshot.last_price:.2f}, "
                f"change={snapshot.change_pct:.2%}, signal={signal.side.value}, "
                f"confidence={signal.confidence:.2f}"
            )
            if order is not None:
                self.broker.submit_order(order)

        account = self.broker.get_account()
        print(f"ACCOUNT cash={account.cash:.2f}, equity={account.equity:.2f}")

    def _load_snapshots(self) -> list[MarketSnapshot]:
        snapshots: list[MarketSnapshot] = []
        for symbol in self.settings.symbols:
            try:
                snapshots.append(self.market_data.snapshot(symbol))
            except Exception as exc:
                print(f"SKIP {symbol}: {exc}")
        if not snapshots:
            raise RuntimeError("No valid market snapshots loaded.")
        return snapshots

    def _build_market_data(self, settings: Settings) -> MarketData:
        names = [name.strip().lower() for name in settings.market_data.split(",") if name.strip()]
        providers: list[tuple[str, MarketData]] = []
        for name in names:
            if name == "yahoo":
                providers.append((name, YahooMarketData()))
            elif name == "akshare":
                providers.append((name, AkShareMarketData()))
            elif name == "tushare":
                if not settings.tushare_token:
                    raise ValueError("TUSHARE_TOKEN is required when MARKET_DATA includes tushare")
                providers.append((name, TushareMarketData(settings.tushare_token)))
            else:
                raise ValueError(f"Unsupported MARKET_DATA provider: {name}")
        return FallbackMarketData(providers)

    def _build_broker(self, settings: Settings) -> Broker:
        if settings.broker == "paper":
            if not settings.dry_run:
                raise ValueError("BROKER=paper requires DRY_RUN=true")
            return PaperBroker(settings.starting_cash)

        if settings.broker == "alpaca":
            if not settings.alpaca_api_key or not settings.alpaca_secret_key:
                raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY are required when BROKER=alpaca")

            is_live_money = not settings.dry_run and not settings.alpaca_paper
            if settings.dry_run and not settings.alpaca_paper:
                raise ValueError("DRY_RUN=true cannot be used with ALPACA_PAPER=false")
            if is_live_money and settings.live_trading_ack != "I_UNDERSTAND_REAL_MONEY_RISK":
                raise ValueError(
                    "Live trading is blocked. Set LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY_RISK "
                    "only after you understand real money orders will be submitted."
                )

            return AlpacaBroker(
                settings.alpaca_api_key,
                settings.alpaca_secret_key,
                paper=settings.alpaca_paper,
                order_type=settings.order_type,
            )

        if settings.broker == "qmt":
            if settings.dry_run:
                raise ValueError(
                    "BROKER=qmt sends orders to MiniQMT. Use BROKER=paper for local dry run, "
                    "or set DRY_RUN=false with LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY_RISK."
                )
            self._require_live_ack(settings)
            if not settings.qmt_path or not settings.qmt_account_id:
                raise ValueError("QMT_PATH and QMT_ACCOUNT_ID are required when BROKER=qmt")
            return QmtBroker(
                settings.qmt_path,
                settings.qmt_account_id,
                settings.qmt_account_type,
                settings.qmt_session_id,
                settings.order_type,
            )

        if settings.broker == "ptrade":
            if settings.dry_run:
                raise ValueError(
                    "BROKER=ptrade calls PTrade order APIs. Use BROKER=paper for local dry run, "
                    "or set DRY_RUN=false with LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY_RISK."
                )
            self._require_live_ack(settings)
            return PTradeBroker(
                settings.ptrade_account_cash,
                settings.ptrade_day_start_equity,
                settings.order_type,
            )

        raise ValueError(f"Unsupported BROKER: {settings.broker}")

    def _require_live_ack(self, settings: Settings) -> None:
        if settings.live_trading_ack != "I_UNDERSTAND_REAL_MONEY_RISK":
            raise ValueError(
                "Live trading is blocked. Set LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY_RISK "
                "only after you understand real money orders will be submitted."
            )

    def _build_ai(self, settings: Settings) -> SignalProvider:
        if settings.ai_provider == "openai":
            if not settings.openai_api_key:
                raise ValueError("OPENAI_API_KEY is required when AI_PROVIDER=openai")
            return OpenAISignalProvider(settings.openai_api_key, settings.openai_model)
        if settings.ai_provider == "codex":
            if not settings.codex_bin:
                raise ValueError("CODEX_BIN is required when AI_PROVIDER=codex")
            return CodexSignalProvider(settings.codex_bin, settings.codex_model, settings.codex_timeout_seconds)
        if settings.ai_provider == "heuristic":
            return HeuristicSignalProvider()
        raise ValueError(f"Unsupported AI_PROVIDER: {settings.ai_provider}")
