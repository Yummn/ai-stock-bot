from __future__ import annotations

import argparse
import datetime as dt
import gc
import json
import mimetypes
import multiprocessing
import queue as queue_module
import random
import uuid
import threading
import time
import traceback
from dataclasses import asdict, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .ai import (
    CodexSignalProvider,
    HeuristicSignalProvider,
    MachineLearningSignalProvider,
    OpenAISignalProvider,
    SignalProvider,
    classify_year_regime_bars,
    strategy_profile,
    strategy_profile_with_overrides,
)
from .broker import PaperBroker
from .config import Settings, load_settings
from .market_data import (
    AkShareMarketData,
    FallbackMarketData,
    MarketData,
    TushareMarketData,
    YahooMarketData,
    _snapshot_from_bars,
)
from .models import Bar, MarketSnapshot, Order, Position, Side, Signal
from .risk import RiskManager
from .storage import TradingStorage


def _auto_learn_backtest_worker(payload: dict[str, Any], result_queue: Any) -> None:
    try:
        settings = load_settings()
        dashboard = PaperTradingDashboard(settings)
        state = dashboard.run_backtest(payload)
        backtest = state.get("backtest") or {}
        hold_return = dashboard._buy_hold_return(backtest)
        return_pct = float(backtest.get("return_pct") or 0.0)
        max_drawdown = dashboard._max_drawdown(backtest.get("daily") or [])
        regime = dashboard._auto_learn_year_regime(hold_return, max_drawdown)
        result_queue.put(
            {
                "ok": True,
                "summary": {
                    "year": int(payload.get("backtest_year") or 0),
                    "regime": regime,
                    "regime_label": dashboard._auto_learn_regime_label(regime),
                    "return_pct": return_pct,
                    "buy_hold_return_pct": hold_return,
                    "excess_return_pct": return_pct - hold_return,
                    "trade_count": int(backtest.get("trade_count") or 0),
                    "max_drawdown_pct": max_drawdown,
                    "window_start": backtest.get("window_start"),
                    "window_end": backtest.get("window_end"),
                    "training_days": backtest.get("ml_training_days"),
                    "training_mode": backtest.get("ml_training_mode"),
                    "errors": (backtest.get("errors") or [])[:3],
                },
            }
        )
    except BaseException as exc:
        result_queue.put(
            {
                "ok": False,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=8),
            }
        )


STRATEGY_KEYS = ("conservative", "balanced", "aggressive", "custom")
DEFAULT_TRAINING_SYMBOLS = (
    "510050.SH",
    "159919.SZ",
    "159922.SZ",
    "512010.SH",
    "512000.SH",
    "515000.SH",
    "512690.SH",
    "515180.SH",
    "513500.SH",
    "159934.SZ",
)


class PaperTradingDashboard:
    def __init__(self, settings: Settings) -> None:
        self.lock = threading.Lock()
        self.base_settings = self._paper_only(settings)
        self.settings = self.base_settings
        self.strategy_key = "conservative"
        self.local_strategy_key = "conservative"
        self.ml_strategy_key = "conservative"
        self.ai_strategy_key = "conservative"
        self.rf_strategy_key = "conservative"
        self.hgb_strategy_key = "conservative"
        self.xgb_strategy_key = "conservative"
        self.mlp_strategy_key = "conservative"
        self.cnn_strategy_key = "conservative"
        self.local_buy_scores: dict[str, int | None] = {key: None for key in STRATEGY_KEYS}
        self.max_buy_signals_by_strategy: dict[str, int | None] = {key: None for key in STRATEGY_KEYS}
        self.training_symbols = list(DEFAULT_TRAINING_SYMBOLS)
        self.training_pool_mode = "training_plus_trade"
        self.training_regime_filter = "off"
        self.training_volatility_filter = "off"
        self.year_regime_overrides: dict[str, str] = {}
        self.strategy_auto_select = "off"
        self.run_engine = "local"
        self.ml_model = "random_forest"
        self.ml_params = self._default_ml_params()
        self.ml_training_days = 1095
        self.rf_training_days = 1095
        self.hgb_training_days = 1095
        self.xgb_training_days = 1095
        self.mlp_training_days = 365
        self.cnn_training_days = 1095
        self.rf_params = self._profile_ml_params("conservative", "random_forest")
        self.hgb_params = self._profile_ml_params("conservative", "hist_gradient_boosting")
        self.xgb_params = self._profile_ml_params("conservative", "xgboost")
        self.mlp_params = self._profile_ml_params("conservative", "mlp")
        self.cnn_params = self._profile_ml_params("conservative", "cnn")
        self.ml_profile_overrides: dict[str, dict[str, dict[str, Any]]] = {}
        self.backtest_interval_minutes = 30
        self.market_data = self._build_market_data(self.settings)
        self.ai = self._build_signal_provider(
            self.settings,
            self.strategy_key,
            self.run_engine,
            self.ml_model,
            self.ml_params,
            self._local_buy_score_for(self.strategy_key),
            self._max_buy_signals_for(self.strategy_key),
        )
        self.risk = self._build_risk(self.settings, self.strategy_key)
        self.broker = PaperBroker(self.settings.starting_cash, log_orders=False)
        self.storage = TradingStorage(self.settings.paper_db_path)
        self.snapshots: list[MarketSnapshot] = []
        self.histories: dict[str, list[Bar]] = {}
        self.training_histories: dict[str, list[Bar]] = {}
        self.signals: list[Signal] = []
        self.orders: list[Order] = []
        self.events: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.backtest: dict[str, Any] | None = None
        self.backtest_tasks: dict[str, dict[str, Any]] = {}
        self.auto_learn_tasks: dict[str, dict[str, Any]] = {}
        self.latest_auto_learn: dict[str, Any] | None = None
        self.cycle = 0
        self.auto_running = False
        self.auto_interval_seconds = 1800
        self.auto_last_run_at: float | None = None
        self.auto_last_error: str | None = None
        self._auto_stop = threading.Event()
        self._auto_thread: threading.Thread | None = None
        self._restore_latest_state()

    def state(self) -> dict[str, Any]:
        with self.lock:
            return self._state_unlocked()

    def run_once(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.lock:
            payload = payload or {}
            self._apply_global_preferences(payload)
            self._apply_runtime_settings(payload)
            self.errors = []
            snapshots = self._load_snapshots()
            self._prepare_signal_provider(self.ai, self.training_histories)
            self._prepare_decision_histories(self.ai, self.histories)
            signals = self.ai.decide(snapshots)
            signals = self._limit_buy_signals(signals, self.strategy_key)
            signal_by_symbol = {signal.symbol: signal for signal in signals}
            orders: list[Order] = []

            for snapshot in snapshots:
                self.broker.mark_price(snapshot.symbol, snapshot.last_price)
                signal = signal_by_symbol.get(snapshot.symbol)
                if signal is None:
                    continue
                position = self.broker.get_position(snapshot.symbol)
                account = self.broker.get_account()
                order = self.risk.build_order(account, snapshot, signal, position)
                if order is not None:
                    self.broker.submit_order(order)
                    orders.append(order)

            self.cycle += 1
            self.snapshots = snapshots
            self.signals = signals
            self.orders = orders
            self.events = [
                {
                    "cycle": self.cycle,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "quantity": order.quantity,
                    "price": order.limit_price,
                    "reason": order.reason,
                }
                for order in orders
            ] + self.events[:49]
            state = self._state_unlocked()
            self.storage.save_run(state)
            return state

    def reset(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.stop_auto()
        with self.lock:
            symbols_raw = str(payload.get("symbols", ",".join(self.settings.symbols)))
            symbols = [item.strip().upper() for item in symbols_raw.split(",") if item.strip()]
            if not symbols:
                symbols = self.base_settings.symbols

            self.settings = replace(
                self.base_settings,
                symbols=symbols,
                starting_cash=self._float(payload, "starting_cash", self.base_settings.starting_cash),
                max_trade_cash=self._float(payload, "max_trade_cash", self.base_settings.max_trade_cash),
                max_order_shares=self._int(payload, "max_order_shares", self.base_settings.max_order_shares),
                max_position_pct=self._pct(payload, "max_position_pct", self.base_settings.max_position_pct),
                max_daily_loss_pct=self._pct(payload, "max_daily_loss_pct", self.base_settings.max_daily_loss_pct),
                stop_loss_pct=self._pct(payload, "stop_loss_pct", self.base_settings.stop_loss_pct),
                take_profit_pct=self._pct(payload, "take_profit_pct", self.base_settings.take_profit_pct),
            )
            self.market_data = self._build_market_data(self.settings)
            self._apply_global_preferences(payload)
            self._apply_runtime_settings(payload)
            self.ai = self._build_signal_provider(
                self.settings,
                self.strategy_key,
                self.run_engine,
                self.ml_model,
                self.ml_params,
                self._local_buy_score_for(self.strategy_key),
                self._max_buy_signals_for(self.strategy_key),
            )
            self.risk = self._build_risk(self.settings, self.strategy_key)
            self.broker = PaperBroker(self.settings.starting_cash, log_orders=False)
            self.snapshots = []
            self.signals = []
            self.orders = []
            self.events = []
            self.errors = []
            self.cycle = 0
            self.histories = {}
            self.backtest = None
            state = self._state_unlocked()
            self.storage.save_latest_state(state)
            return state

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self._apply_global_preferences(payload)
            self._apply_runtime_settings(payload)
            self.ai = self._build_signal_provider(
                self.settings,
                self.strategy_key,
                self.run_engine,
                self.ml_model,
                self.ml_params,
                self._local_buy_score_for(self.strategy_key),
                self._max_buy_signals_for(self.strategy_key),
            )
            self.risk = self._build_risk(self.settings, self.strategy_key)
            state = self._state_unlocked()
            self.storage.save_latest_state(state)
            return state

    def reset_settings(self) -> dict[str, Any]:
        with self.lock:
            self.settings = self.base_settings
            self.strategy_key = "conservative"
            self.local_strategy_key = "conservative"
            self.ml_strategy_key = "conservative"
            self.ai_strategy_key = "conservative"
            self.rf_strategy_key = "conservative"
            self.hgb_strategy_key = "conservative"
            self.xgb_strategy_key = "conservative"
            self.mlp_strategy_key = "conservative"
            self.cnn_strategy_key = "conservative"
            self.local_buy_scores = {key: None for key in STRATEGY_KEYS}
            self.max_buy_signals_by_strategy = {key: None for key in STRATEGY_KEYS}
            self.training_symbols = list(DEFAULT_TRAINING_SYMBOLS)
            self.training_pool_mode = "training_plus_trade"
            self.training_regime_filter = "off"
            self.training_volatility_filter = "off"
            self.year_regime_overrides = {}
            self.strategy_auto_select = "off"
            self.run_engine = "local"
            self.ml_model = "random_forest"
            self.ml_params = self._default_ml_params()
            self.ml_training_days = 1095
            self.rf_training_days = 1095
            self.hgb_training_days = 1095
            self.xgb_training_days = 1095
            self.mlp_training_days = 365
            self.cnn_training_days = 1095
            self.rf_params = self._profile_ml_params("conservative", "random_forest")
            self.hgb_params = self._profile_ml_params("conservative", "hist_gradient_boosting")
            self.xgb_params = self._profile_ml_params("conservative", "xgboost")
            self.mlp_params = self._profile_ml_params("conservative", "mlp")
            self.cnn_params = self._profile_ml_params("conservative", "cnn")
            self.backtest_interval_minutes = 30
            self.market_data = self._build_market_data(self.settings)
            self.ai = self._build_signal_provider(
                self.settings,
                self.strategy_key,
                self.run_engine,
                self.ml_model,
                self.ml_params,
                self._local_buy_score_for(self.strategy_key),
                self._max_buy_signals_for(self.strategy_key),
            )
            self.risk = self._build_risk(self.settings, self.strategy_key)
            state = self._state_unlocked()
            self.storage.save_latest_state(state)
            return state

    def refresh_training_regimes(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self._apply_global_preferences(payload)
            self._apply_runtime_settings(payload)
            lookback_days = max(420, self._max_training_days() + 420)
            market_data = self._build_market_data(self.settings, lookback_days=lookback_days)
            training_histories, training_errors = self._load_training_histories(market_data)
            trade_histories, trade_errors = self._load_training_histories(
                market_data,
                symbols=self.settings.symbols,
            )
            training_histories.update(trade_histories)
            training_errors.extend(trade_errors)
            if training_histories:
                self.training_histories = training_histories
            self.errors = list(training_errors[:8])
            state = self._state_unlocked()
            self.storage.save_latest_state(state)
            return state

    def manual_buy(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            symbol = str(payload.get("symbol", "")).strip().upper()
            if not symbol:
                raise ValueError("Please choose or enter a stock symbol.")
            requested_cash = max(0.0, self._float(payload, "cash", self.settings.max_trade_cash))
            requested_shares = max(0, self._int(payload, "shares", 0))
            if requested_cash <= 0 and requested_shares <= 0:
                raise ValueError("Please enter buy cash or share quantity.")

            snapshot = self._snapshot_for_symbol(symbol)
            self.broker.mark_price(snapshot.symbol, snapshot.last_price)
            position = self.broker.get_position(snapshot.symbol)
            account = self.broker.get_account()
            max_position_cash = account.equity * self.settings.max_position_pct
            current_position_cash = 0.0 if position is None else position.quantity * snapshot.last_price
            available_cash = max(
                0.0,
                min(
                    account.cash,
                    self.settings.max_trade_cash,
                    max_position_cash - current_position_cash,
                    requested_cash if requested_cash > 0 else account.cash,
                ),
            )
            shares_by_cash = int(available_cash // snapshot.last_price)
            quantity = requested_shares if requested_shares > 0 else shares_by_cash
            quantity = min(quantity, shares_by_cash, self.settings.max_order_shares)
            if quantity <= 0:
                raise ValueError("Order rejected by risk controls or insufficient cash.")

            order = Order(
                snapshot.symbol,
                Side.BUY,
                quantity,
                snapshot.last_price,
                "Manual buy: user decision",
            )
            self.broker.submit_order(order)
            self.orders = [order] + self.orders[:49]
            self.events = [
                {
                    "cycle": self.cycle,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "quantity": order.quantity,
                    "price": order.limit_price,
                    "reason": order.reason,
                }
            ] + self.events[:49]
            state = self._state_unlocked()
            self.storage.save_latest_state(state)
            return state

    def start_backtest_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            existing = [
                task
                for task in self.backtest_tasks.values()
                if task.get("status") in {"queued", "running"}
            ]
            if existing:
                task = existing[-1]
                return {
                    "task_id": task["id"],
                    "status": task["status"],
                    "message": "已有回测任务正在运行。",
                }
            task_id = uuid.uuid4().hex
            self.backtest_tasks[task_id] = {
                "id": task_id,
                "status": "queued",
                "progress": 0,
                "message": "回测任务已排队。",
                "created_at": time.time(),
                "updated_at": time.time(),
                "result": None,
                "error": None,
            }

        thread = threading.Thread(target=self._run_backtest_task, args=(task_id, payload), daemon=True)
        thread.start()
        return {"task_id": task_id, "status": "queued", "progress": 0, "message": "回测已在后台启动。"}

    def backtest_task_status(self, task_id: str) -> dict[str, Any]:
        task = self.backtest_tasks.get(task_id)
        if task is None:
            raise ValueError("Backtest task not found or expired.")
        progress = task.get("progress", 0)
        if task.get("status") == "running":
            elapsed = max(0.0, time.time() - float(task.get("updated_at", task.get("created_at", time.time()))))
            progress = min(95, max(int(progress), 5 + int(elapsed * 3)))
        result = {
            "task_id": task_id,
            "status": task["status"],
            "progress": progress,
            "message": task.get("message", ""),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "error": task.get("error"),
        }
        if task.get("status") == "done":
            result["state"] = task.get("result")
        return result

    def _run_backtest_task(self, task_id: str, payload: dict[str, Any]) -> None:
        with self.lock:
            task = self.backtest_tasks.get(task_id)
            if task is not None:
                task["status"] = "running"
                task["progress"] = 5
                task["message"] = "正在拉取行情并训练模型..."
                task["updated_at"] = time.time()
        try:
            result = self.run_backtest(payload)
            with self.lock:
                task = self.backtest_tasks.get(task_id)
                if task is not None:
                    task["status"] = "done"
                    task["progress"] = 100
                    task["message"] = "回测完成。"
                    task["result"] = result
                    task["updated_at"] = time.time()
                    self._trim_backtest_tasks()
        except Exception as exc:
            with self.lock:
                task = self.backtest_tasks.get(task_id)
                if task is not None:
                    task["status"] = "error"
                    task["progress"] = 100
                    task["message"] = "回测失败。"
                    task["error"] = str(exc)
                    task["updated_at"] = time.time()
                    self._trim_backtest_tasks()

    def _trim_backtest_tasks(self) -> None:
        if len(self.backtest_tasks) <= 8:
            return
        keep = sorted(self.backtest_tasks.values(), key=lambda item: item.get("created_at", 0), reverse=True)[:8]
        self.backtest_tasks = {task["id"]: task for task in keep}

    def start_auto_learn_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            existing = [
                task
                for task in self.auto_learn_tasks.values()
                if task.get("status") in {"queued", "running"}
            ]
            if existing:
                task = existing[-1]
                return {
                    "task_id": task["id"],
                    "status": task["status"],
                    "message": "已有自动学习任务正在运行。",
                }
            task_id = uuid.uuid4().hex
            self.auto_learn_tasks[task_id] = {
                "id": task_id,
                "status": "queued",
                "progress": 0,
                "message": "自动学习任务已排队。",
                "created_at": time.time(),
                "updated_at": time.time(),
                "result": None,
                "error": None,
                "stop_event": threading.Event(),
            }
            self.latest_auto_learn = {
                "task_id": task_id,
                "status": "queued",
                "progress": 0,
                "message": "自动学习任务已排队。",
                "created_at": time.time(),
                "updated_at": time.time(),
                "result": None,
                "error": None,
            }

        thread = threading.Thread(target=self._run_auto_learn_task, args=(task_id, payload), daemon=True)
        thread.start()
        return {"task_id": task_id, "status": "queued", "progress": 0, "message": "自动学习已在后台启动。"}

    def auto_learn_task_status(self, task_id: str) -> dict[str, Any]:
        task = self.auto_learn_tasks.get(task_id)
        if task is None:
            raise ValueError("Auto-learn task not found or expired.")
        progress = task.get("progress", 0)
        if task.get("status") == "running":
            elapsed = max(0.0, time.time() - float(task.get("updated_at", task.get("created_at", time.time()))))
            progress = min(98, max(int(progress), 3 + int(elapsed * 2)))
        result = {
            "task_id": task_id,
            "status": task["status"],
            "progress": progress,
            "message": task.get("message", ""),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "error": task.get("error"),
        }
        if task.get("status") in {"done", "stopped"}:
            result["result"] = task.get("result")
        return result

    def _public_auto_learn_task(self, task: dict[str, Any] | None) -> dict[str, Any] | None:
        if not task:
            return None
        task_id = str(task.get("id") or task.get("task_id") or "")
        status = str(task.get("status") or "")
        progress = task.get("progress", 0)
        if status == "running":
            elapsed = max(0.0, time.time() - float(task.get("updated_at", task.get("created_at", time.time()))))
            progress = min(98, max(int(progress or 0), 3 + int(elapsed * 2)))
        public = {
            "task_id": task_id,
            "status": status,
            "progress": progress,
            "message": task.get("message", ""),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "error": task.get("error"),
        }
        if task.get("result") is not None:
            public["result"] = task.get("result")
        return public

    def _active_auto_learn_task_unlocked(self) -> dict[str, Any] | None:
        active = [
            task
            for task in self.auto_learn_tasks.values()
            if task.get("status") in {"queued", "running"}
        ]
        if not active:
            return None
        active.sort(key=lambda item: item.get("updated_at", item.get("created_at", 0)), reverse=True)
        return self._public_auto_learn_task(active[0])

    def stop_auto_learn_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = str(payload.get("task_id", "")).strip()
        with self.lock:
            candidates = [self.auto_learn_tasks.get(task_id)] if task_id else list(self.auto_learn_tasks.values())
            stopped = False
            for task in candidates:
                if not task or task.get("status") not in {"queued", "running"}:
                    continue
                stop_event = task.get("stop_event")
                if isinstance(stop_event, threading.Event):
                    stop_event.set()
                task["message"] = "正在停止自动学习..."
                task["updated_at"] = time.time()
                stopped = True
            if not stopped:
                return {"status": "idle", "message": "当前没有正在运行的自动学习任务。"}
            return {"status": "stopping", "message": "已发送停止信号，当前这一小步结束后会停下。"}

    def apply_auto_learn_recommendation(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = str(payload.get("task_id", "")).strip()
        profile_raw = str(payload.get("profile", "all")).strip().lower()
        with self.lock:
            task = self.auto_learn_tasks.get(task_id)
            latest = self.latest_auto_learn or {}
            if task and task.get("status") in {"done", "stopped"} and task.get("result"):
                result = task["result"]
            elif latest.get("result") and (not task_id or str(latest.get("task_id") or "") == task_id):
                result = latest["result"]
            else:
                raise ValueError("没有可应用的自动学习结果，请先等待自动学习完成。")
            model = self._normalize_ml_model(payload.get("model", result.get("model", self.ml_model)))
            best_by_profile = result.get("best_by_profile") or {}
            applied: dict[str, dict[str, Any]] = {}
            if profile_raw == "all":
                target_profiles = ["aggressive", "balanced", "conservative"]
            elif profile_raw in {"best", "global"}:
                best = result.get("best") or {}
                target_profiles = [strategy_profile(str(best.get("profile") or "balanced")).key]
                best_by_profile = {target_profiles[0]: best}
            else:
                target_profiles = [strategy_profile(profile_raw).key]

            for profile in target_profiles:
                suggestion = best_by_profile.get(profile)
                if not suggestion:
                    continue
                params = self._candidate_base_params(suggestion.get("payload") or {}, model, profile)
                cleaned = self._clean_ml_profile_override(model, params)
                if not cleaned:
                    continue
                self.ml_profile_overrides.setdefault(model, {})[profile] = cleaned
                applied[profile] = cleaned

            if not applied:
                raise ValueError("没有找到可应用的档位建议。")

            self.rf_params = self._profile_ml_params(self.rf_strategy_key, "random_forest")
            self.hgb_params = self._profile_ml_params(self.hgb_strategy_key, "hist_gradient_boosting")
            self.xgb_params = self._profile_ml_params(self.xgb_strategy_key, "xgboost")
            self.mlp_params = self._profile_ml_params(self.mlp_strategy_key, "mlp")
            self.cnn_params = self._profile_ml_params(self.cnn_strategy_key, "cnn")
            if self.ml_model == "xgboost":
                self.ml_params = self.xgb_params
                self.ml_strategy_key = self.xgb_strategy_key
            elif self.ml_model == "mlp":
                self.ml_params = self.mlp_params
                self.ml_strategy_key = self.mlp_strategy_key
            elif self.ml_model == "cnn":
                self.ml_params = self.cnn_params
                self.ml_strategy_key = self.cnn_strategy_key
            elif self.ml_model == "hist_gradient_boosting":
                self.ml_params = self.hgb_params
                self.ml_strategy_key = self.hgb_strategy_key
            else:
                self.ml_params = self.rf_params
                self.ml_strategy_key = self.rf_strategy_key
            self.ai = self._build_signal_provider(
                self.settings,
                self.strategy_key,
                self.run_engine,
                self.ml_model,
                self.ml_params,
                self._local_buy_score_for(self.strategy_key),
                self._max_buy_signals_for(self.strategy_key),
            )
            state = self._state_unlocked()
            state["applied_auto_learn"] = {
                "model": model,
                "profiles": list(applied),
            }
            self.storage.save_latest_state(state)
            return state

    def _run_auto_learn_task(self, task_id: str, payload: dict[str, Any]) -> None:
        with self.lock:
            task = self.auto_learn_tasks.get(task_id)
            if task is not None:
                task["status"] = "running"
                task["progress"] = 3
                task["message"] = "正在准备候选参数..."
                task["updated_at"] = time.time()
        try:
            result = self.run_auto_learn(payload, task_id)
            with self.lock:
                task = self.auto_learn_tasks.get(task_id)
                if task is not None:
                    task["status"] = "stopped" if result.get("stopped") else "done"
                    task["progress"] = 100
                    task["message"] = "自动学习已停止。" if result.get("stopped") else "自动学习完成。"
                    task["result"] = result
                    task["updated_at"] = time.time()
                    public = self._public_auto_learn_task(task)
                    if public and public.get("result"):
                        public["result"]["task_id"] = task_id
                    self.latest_auto_learn = public
                    self.storage.save_latest_state(self._state_unlocked())
                    self._trim_auto_learn_tasks()
        except Exception as exc:
            with self.lock:
                task = self.auto_learn_tasks.get(task_id)
                if task is not None:
                    task["status"] = "error"
                    task["progress"] = 100
                    task["message"] = "自动学习失败。"
                    task["error"] = str(exc)
                    task["updated_at"] = time.time()
                    self.latest_auto_learn = self._public_auto_learn_task(task)
                    self.storage.save_latest_state(self._state_unlocked())
                    self._trim_auto_learn_tasks()

    def _trim_auto_learn_tasks(self) -> None:
        if len(self.auto_learn_tasks) <= 8:
            return
        keep = sorted(self.auto_learn_tasks.values(), key=lambda item: item.get("created_at", 0), reverse=True)[:8]
        self.auto_learn_tasks = {task["id"]: task for task in keep}

    def run_auto_learn(self, payload: dict[str, Any], task_id: str | None = None) -> dict[str, Any]:
        model = self._auto_learn_model(payload)
        budget = self._auto_learn_budget(payload.get("auto_learn_budget", "balanced"))
        years = self._auto_learn_years(payload.get("auto_learn_years"))
        continuous = self._bool(payload, "auto_learn_continuous", False)
        max_rounds = self._bounded_int(payload, "auto_learn_max_rounds", 999 if continuous else 1, 1, 999)
        patience = self._bounded_int(payload, "auto_learn_patience", 6, 1, 50)
        min_improvement = self._bounded_float(payload, "auto_learn_min_improvement", 0.002, 0.0, 1.0)
        score_weights = self._auto_learn_score_weights(payload)
        candidates = self._auto_learn_candidates(payload, model, budget)
        if not candidates:
            raise RuntimeError("No auto-learning candidates were generated.")

        previous_backtest = self.backtest
        all_results: list[dict[str, Any]] = []
        rounds: list[dict[str, Any]] = []
        best: dict[str, Any] | None = None
        best_by_profile: dict[str, dict[str, Any]] = {}
        seen_candidates: set[str] = set()
        stale_rounds = 0
        stale_by_profile: dict[str, int] = {key: 0 for key in ("aggressive", "balanced", "conservative")}
        stopped = False
        stop_reason = ""
        finished_runs = 0
        evaluated_candidates = 0
        max_stored_results = 80
        max_returned_results = 40
        max_stored_rounds = 80
        started_at = time.time()
        try:
            for round_index in range(1, max_rounds + 1):
                if self._auto_learn_stop_requested(task_id):
                    stopped = True
                    stop_reason = "用户手动停止。"
                    break
                round_candidates = self._unique_auto_learn_candidates(candidates, seen_candidates)
                if not round_candidates:
                    stopped = True
                    stop_reason = "没有新的候选参数可继续测试。"
                    break
                round_results: list[dict[str, Any]] = []
                for candidate_index, candidate in enumerate(round_candidates, start=1):
                    per_year: list[dict[str, Any]] = []
                    for year in years:
                        if self._auto_learn_stop_requested(task_id):
                            stopped = True
                            stop_reason = "用户手动停止。"
                            break
                        finished_runs += 1
                        progress = self._auto_learn_progress(round_index, max_rounds, candidate_index, len(round_candidates), continuous)
                        self._update_auto_learn_task(
                            task_id,
                            progress,
                            f"第 {round_index} 轮：正在验证 {candidate['label']}：{year} 年...",
                        )
                        test_payload = {
                            **payload,
                            **candidate["payload"],
                            "run_engine": "ml",
                            "engine": "ml",
                            "ml_model": model,
                            "mode": "natural_year",
                            "backtest_year": year,
                            "days": 366,
                            "time_granularity": "fast",
                            "_suppress_state_save": "true",
                        }
                        try:
                            per_year.append(self._run_auto_learn_backtest_isolated(test_payload))
                        except Exception as exc:
                            per_year.append({"year": year, "error": str(exc)})
                        finally:
                            with self.lock:
                                self.backtest = previous_backtest
                            if finished_runs % 10 == 0:
                                gc.collect()
                    if stopped:
                        break

                    result = self._score_auto_learn_candidate(candidate, per_year, score_weights)
                    result["round"] = round_index
                    evaluated_candidates += 1
                    result["rank_source_index"] = evaluated_candidates
                    round_results.append(result)
                    all_results.append(result)
                    if len(all_results) > max_stored_results:
                        all_results.sort(key=lambda item: item.get("score", -9999.0), reverse=True)
                        del all_results[max_stored_results:]

                if round_results:
                    round_results.sort(key=lambda item: item.get("score", -9999.0), reverse=True)
                    previous_score = float(best.get("score", -9999.0)) if best else -9999.0
                    round_best = round_results[0]
                    improved = best is None or float(round_best.get("score", -9999.0)) > previous_score + min_improvement
                    if improved:
                        best = round_best
                        stale_rounds = 0
                    else:
                        stale_rounds += 1
                    round_profile_best: dict[str, dict[str, Any]] = {}
                    for item in round_results:
                        profile = str(item.get("profile") or "balanced")
                        current = round_profile_best.get(profile)
                        if current is None or float(item.get("score", -9999.0)) > float(current.get("score", -9999.0)):
                            round_profile_best[profile] = item
                    profile_rounds: dict[str, dict[str, Any]] = {}
                    for profile in ("aggressive", "balanced", "conservative"):
                        candidate_best = round_profile_best.get(profile)
                        previous_profile_best = best_by_profile.get(profile)
                        if candidate_best is None:
                            stale_by_profile[profile] = stale_by_profile.get(profile, 0) + 1
                            continue
                        previous_profile_score = float(previous_profile_best.get("score", -9999.0)) if previous_profile_best else -9999.0
                        profile_improved = previous_profile_best is None or float(candidate_best.get("score", -9999.0)) > previous_profile_score + min_improvement
                        if profile_improved:
                            best_by_profile[profile] = candidate_best
                            stale_by_profile[profile] = 0
                        else:
                            stale_by_profile[profile] = stale_by_profile.get(profile, 0) + 1
                        profile_rounds[profile] = {
                            "best_label": candidate_best.get("label"),
                            "best_score": candidate_best.get("score"),
                            "global_best_score": best_by_profile.get(profile, {}).get("score"),
                            "improved": profile_improved,
                            "stale_rounds": stale_by_profile.get(profile, 0),
                        }
                    rounds.append(
                        {
                            "round": round_index,
                            "candidate_count": len(round_results),
                            "best_label": round_best.get("label"),
                            "best_score": round_best.get("score"),
                            "global_best_score": best.get("score") if best else None,
                            "improved": improved,
                            "stale_rounds": stale_rounds,
                            "profile_best": profile_rounds,
                        }
                    )
                    if len(rounds) > max_stored_rounds:
                        del rounds[:-max_stored_rounds]
                    self._update_auto_learn_task(
                        task_id,
                        self._auto_learn_progress(round_index, max_rounds, len(round_candidates), len(round_candidates), continuous),
                        f"第 {round_index} 轮完成，三档最佳已分别更新。",
                    )

                if stopped:
                    break
                if not continuous:
                    break
                if all(stale_by_profile.get(profile, 0) >= patience for profile in ("aggressive", "balanced", "conservative")):
                    stopped = True
                    stop_reason = f"三档都连续 {patience} 轮没有超过最小提升，已认为当前范围基本收敛。"
                    break
                candidates = self._next_auto_learn_candidates_for_profiles(payload, model, budget, best_by_profile, round_index + 1)

            all_results.sort(key=lambda item: item.get("score", -9999.0), reverse=True)
            all_results = all_results[:max_returned_results]
            for rank, item in enumerate(all_results, start=1):
                item["rank"] = rank
            best = all_results[0] if all_results else best
            best_by_profile = {
                profile: self._ranked_result(item, all_results)
                for profile, item in best_by_profile.items()
            }
            return {
                "model": model,
                "model_label": self._ml_model_label(model),
                "budget": budget,
                "years": years,
                "continuous": continuous,
                "round_count": len(rounds),
                "candidate_count": evaluated_candidates,
                "returned_candidate_count": len(all_results),
                "run_count": finished_runs,
                "elapsed_seconds": round(time.time() - started_at, 2),
                "best": best,
                "best_by_profile": best_by_profile,
                "profile_labels": {key: strategy_profile(key).label for key in ("aggressive", "balanced", "conservative")},
                "results": all_results,
                "rounds": rounds,
                "score_weights": score_weights,
                "stopped": stopped,
                "stop_reason": stop_reason,
                "method": "walk_forward_natural_year",
                "accuracy_note": "每个验证年只使用该年开始前的数据训练；专项评分按行情标签重点评价对应档位，兜底评分检查非目标行情不能太差。",
                "apply_note": "自动学习给出建议后，可以点击按钮应用到对应档位或一键应用三档。",
            }
        finally:
            with self.lock:
                self.backtest = previous_backtest
                self._apply_global_preferences(payload)
                self._apply_runtime_settings(payload)
                self.storage.save_latest_state(self._state_unlocked())

    def _run_auto_learn_backtest_isolated(self, payload: dict[str, Any]) -> dict[str, Any]:
        timeout_seconds = self._auto_learn_step_timeout(payload)
        try:
            return self._run_auto_learn_backtest_process(payload, timeout_seconds)
        except RuntimeError as exc:
            if not self._should_retry_auto_learn_on_cpu(payload, exc):
                raise
            retry_payload = dict(payload)
            model = self._normalize_ml_model(str(retry_payload.get("ml_model", retry_payload.get("auto_learn_model", ""))))
            if model == "cnn":
                retry_payload["cnn_device"] = "cpu"
            elif model == "mlp":
                retry_payload["mlp_device"] = "cpu"
            retry_payload["_auto_learn_cpu_retry"] = "true"
            try:
                summary = self._run_auto_learn_backtest_process(retry_payload, timeout_seconds)
            except Exception as retry_exc:
                raise RuntimeError(f"{exc}；CPU 重试也失败：{retry_exc}") from retry_exc
            errors = list(summary.get("errors") or [])
            errors.insert(0, f"GPU/CUDA 年度子进程崩溃，已自动改用 CPU 重试成功：{exc}")
            summary["errors"] = errors[:4]
            summary["device_retry"] = "cpu"
            return summary

    def _run_auto_learn_backtest_process(
        self,
        payload: dict[str, Any],
        timeout_seconds: int | None,
    ) -> dict[str, Any]:
        ctx = multiprocessing.get_context("spawn")
        result_queue = ctx.Queue(maxsize=1)
        process = ctx.Process(target=_auto_learn_backtest_worker, args=(payload, result_queue))
        process.start()
        process.join(timeout_seconds)
        if timeout_seconds is not None and process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
            raise RuntimeError(f"单次年度回测超过 {timeout_seconds} 秒，已终止该子任务。")

        try:
            result = result_queue.get_nowait()
        except queue_module.Empty:
            exit_code = process.exitcode
            exit_detail = f"{exit_code}"
            if isinstance(exit_code, int):
                exit_detail = f"{exit_code} / 0x{exit_code & 0xFFFFFFFF:08X}"
            raise RuntimeError(f"年度回测子进程异常退出，退出码 {exit_detail}。")
        finally:
            result_queue.close()
            result_queue.join_thread()

        if not result.get("ok"):
            detail = str(result.get("error") or "unknown error")
            trace = str(result.get("traceback") or "").strip()
            if trace:
                detail = f"{detail}\n{trace}"
            raise RuntimeError(detail)
        summary = result.get("summary")
        if not isinstance(summary, dict):
            raise RuntimeError("年度回测子进程没有返回有效结果。")
        return summary

    def _auto_learn_step_timeout(self, payload: dict[str, Any]) -> int | None:
        raw = payload.get("auto_learn_step_timeout", "")
        if raw is None or str(raw).strip().lower() in {"", "0", "off", "false", "none", "unlimited"}:
            return None
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return min(max(value, 60), 86400)

    def _should_retry_auto_learn_on_cpu(self, payload: dict[str, Any], exc: Exception) -> bool:
        model = self._normalize_ml_model(str(payload.get("ml_model", payload.get("auto_learn_model", ""))))
        if model not in {"cnn", "mlp"}:
            return False
        if str(payload.get("_auto_learn_cpu_retry", "")).lower() in {"true", "1", "yes", "on"}:
            return False
        message = str(exc)
        return "0xC0000005" in message or "3221225477" in message

    def _auto_learn_score_weights(self, payload: dict[str, Any]) -> dict[str, float]:
        specialized = self._bounded_float(payload, "auto_learn_specialized_weight", 50.0, 0.0, 100.0)
        fallback = self._bounded_float(payload, "auto_learn_fallback_weight", 25.0, 0.0, 100.0)
        composite = self._bounded_float(payload, "auto_learn_composite_weight", 25.0, 0.0, 100.0)
        total = specialized + fallback + composite
        if total <= 0:
            specialized, fallback, composite, total = 50.0, 25.0, 25.0, 100.0
        return {
            "specialized": specialized / total,
            "fallback": fallback / total,
            "composite": composite / total,
            "raw_specialized": specialized,
            "raw_fallback": fallback,
            "raw_composite": composite,
        }

    def _auto_learn_stop_requested(self, task_id: str | None) -> bool:
        if not task_id:
            return False
        task = self.auto_learn_tasks.get(task_id)
        stop_event = task.get("stop_event") if task else None
        return isinstance(stop_event, threading.Event) and stop_event.is_set()

    def _auto_learn_progress(
        self,
        round_index: int,
        max_rounds: int,
        candidate_index: int,
        candidate_count: int,
        continuous: bool,
    ) -> int:
        if continuous:
            round_progress = ((candidate_index - 1) / max(1, candidate_count)) * 8
            return min(96, 5 + ((round_index - 1) % 10) * 9 + int(round_progress))
        total_steps = max(1, max_rounds * max(1, candidate_count))
        done = (round_index - 1) * max(1, candidate_count) + candidate_index - 1
        return min(96, 5 + int(done / total_steps * 90))

    def _unique_auto_learn_candidates(
        self,
        candidates: list[dict[str, Any]],
        seen: set[str],
    ) -> list[dict[str, Any]]:
        unique: list[dict[str, Any]] = []
        for candidate in candidates:
            key = json.dumps(
                {"profile": candidate.get("profile"), "payload": candidate.get("payload", {})},
                sort_keys=True,
                ensure_ascii=False,
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique

    def _next_auto_learn_candidates(
        self,
        payload: dict[str, Any],
        model: str,
        budget: str,
        best: dict[str, Any] | None,
        round_index: int,
    ) -> list[dict[str, Any]]:
        if not best:
            return self._auto_learn_candidates(payload, model, budget)
        prefix = self._ml_prefix(model)
        base_payload = {**best.get("payload", {})}
        profile = strategy_profile(str(best.get("profile") or base_payload.get(f"{prefix}_strategy_profile", base_payload.get("ml_strategy_profile", "custom")))).key
        base_params = self._candidate_base_params({**payload, **base_payload}, model, profile)
        step_scale = max(0.25, 1.0 / max(1, round_index // 2))
        threshold_step = round(0.03 * step_scale, 3)
        max_candidates = {"quick": 4, "balanced": 6, "deep": 9}[budget]
        candidates: list[dict[str, Any]] = []

        def merged(label: str, updates: dict[str, Any], phase: str = "fine") -> None:
            candidate_payload = self._auto_learn_custom_payload(model, profile, base_params, {**base_payload, **updates})
            candidates.append(
                {
                    "label": f"第{round_index}轮 {strategy_profile(profile).label}{label}",
                    "payload": candidate_payload,
                    "profile": profile,
                    "phase": phase,
                }
            )

        buy = self._clamp_threshold(base_params.get("ml_buy_threshold"), 0.58, 0)
        sell = self._clamp_threshold(base_params.get("ml_sell_threshold"), 0.30, 0)
        merged("沿用最佳", {}, "incumbent")
        merged("买入更积极", {f"{prefix}_buy_threshold": self._clamp_threshold(buy, 0.58, -threshold_step)})
        merged("买入更谨慎", {f"{prefix}_buy_threshold": self._clamp_threshold(buy, 0.58, threshold_step)})
        merged("卖出更耐心", {f"{prefix}_sell_threshold": self._clamp_threshold(sell, 0.30, -threshold_step)})
        merged("卖出更果断", {f"{prefix}_sell_threshold": self._clamp_threshold(sell, 0.30, threshold_step)})
        for label, updates in self._model_param_mutations(model, base_params, step_scale):
            merged(label, updates, "model")
        for index, updates in enumerate(self._random_model_candidate_updates(model, base_params, step_scale, 1), start=1):
            merged(f"随机扰动{index}", updates, "random")
        return self._ordered_auto_learn_candidates(candidates)[:max_candidates]

    def _next_auto_learn_candidates_for_profiles(
        self,
        payload: dict[str, Any],
        model: str,
        budget: str,
        best_by_profile: dict[str, dict[str, Any]],
        round_index: int,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for profile in ("aggressive", "balanced", "conservative"):
            best = best_by_profile.get(profile)
            if best is None:
                candidates.extend(
                    item for item in self._auto_learn_candidates(payload, model, "quick") if item.get("profile") == profile
                )
                continue
            candidates.extend(self._next_auto_learn_candidates(payload, model, budget, best, round_index))
        return candidates

    def _ranked_result(self, item: dict[str, Any], ranked_results: list[dict[str, Any]]) -> dict[str, Any]:
        if "rank" in item:
            return item
        source_index = item.get("rank_source_index")
        for ranked in ranked_results:
            if ranked.get("rank_source_index") == source_index:
                return ranked
        return item

    def _model_param_mutations(self, model: str, params: dict[str, Any], step_scale: float) -> list[tuple[str, dict[str, Any]]]:
        def bounded_int_value(key: str, default: int, delta: int, minimum: int, maximum: int) -> int:
            try:
                value = int(float(params.get(key, default)))
            except (TypeError, ValueError):
                value = default
            return min(max(value + delta, minimum), maximum)

        def bounded_float_value(key: str, default: float, delta: float, minimum: float, maximum: float) -> float:
            try:
                value = float(params.get(key, default))
            except (TypeError, ValueError):
                value = default
            return round(min(max(value + delta, minimum), maximum), 5)

        scale = max(0.25, step_scale)
        if model == "random_forest":
            tree_delta = max(10, int(40 * scale))
            leaf_delta = max(1, int(5 * scale))
            return [
                ("树更多", {"rf_n_estimators": bounded_int_value("rf_n_estimators", 100, tree_delta, 10, 500)}),
                ("树更少", {"rf_n_estimators": bounded_int_value("rf_n_estimators", 100, -tree_delta, 10, 500)}),
                ("叶子更稳", {"rf_min_samples_leaf": bounded_int_value("rf_min_samples_leaf", 5, leaf_delta, 1, 100)}),
                ("叶子更灵活", {"rf_min_samples_leaf": bounded_int_value("rf_min_samples_leaf", 5, -leaf_delta, 1, 100)}),
                ("树更深", {"rf_max_depth": bounded_int_value("rf_max_depth", 5, 1, 1, 30)}),
                ("树更浅", {"rf_max_depth": bounded_int_value("rf_max_depth", 5, -1, 1, 30)}),
                ("切换特征采样", {"rf_max_features_sqrt": not bool(params.get("rf_max_features_sqrt", True))}),
            ]
        if model == "hist_gradient_boosting":
            iter_delta = max(10, int(30 * scale))
            leaf_delta = max(1, int(4 * scale))
            return [
                ("迭代更多", {"hgb_max_iter": bounded_int_value("hgb_max_iter", 140, iter_delta, 20, 500)}),
                ("迭代更少", {"hgb_max_iter": bounded_int_value("hgb_max_iter", 140, -iter_delta, 20, 500)}),
                ("学习率更大", {"hgb_learning_rate": bounded_float_value("hgb_learning_rate", 0.03, 0.005 * scale, 0.005, 0.5)}),
                ("学习率更小", {"hgb_learning_rate": bounded_float_value("hgb_learning_rate", 0.03, -0.005 * scale, 0.005, 0.5)}),
                ("叶子更稳", {"hgb_min_samples_leaf": bounded_int_value("hgb_min_samples_leaf", 12, leaf_delta, 2, 100)}),
                ("叶子更灵活", {"hgb_min_samples_leaf": bounded_int_value("hgb_min_samples_leaf", 12, -leaf_delta, 2, 100)}),
                ("正则更强", {"hgb_l2_regularization": bounded_float_value("hgb_l2_regularization", 0.08, 0.03 * scale, 0.0, 10.0)}),
                ("正则更弱", {"hgb_l2_regularization": bounded_float_value("hgb_l2_regularization", 0.08, -0.03 * scale, 0.0, 10.0)}),
            ]
        if model == "xgboost":
            tree_delta = max(10, int(30 * scale))
            return [
                ("树更多", {"xgb_n_estimators": bounded_int_value("xgb_n_estimators", 120, tree_delta, 20, 500)}),
                ("树更少", {"xgb_n_estimators": bounded_int_value("xgb_n_estimators", 120, -tree_delta, 20, 500)}),
                ("学习率更大", {"xgb_learning_rate": bounded_float_value("xgb_learning_rate", 0.035, 0.005 * scale, 0.005, 0.5)}),
                ("学习率更小", {"xgb_learning_rate": bounded_float_value("xgb_learning_rate", 0.035, -0.005 * scale, 0.005, 0.5)}),
                ("更弱正则", {"xgb_reg_lambda": bounded_float_value("xgb_reg_lambda", 1.5, -0.5 * scale, 0.0, 20.0)}),
                ("更强正则", {"xgb_reg_lambda": bounded_float_value("xgb_reg_lambda", 1.5, 0.5 * scale, 0.0, 20.0)}),
                ("树更深", {"xgb_max_depth": bounded_int_value("xgb_max_depth", 3, 1, 1, 12)}),
                ("树更浅", {"xgb_max_depth": bounded_int_value("xgb_max_depth", 3, -1, 1, 12)}),
            ]
        if model == "cnn":
            channel_delta = max(4, int(12 * scale))
            iter_delta = max(10, int(40 * scale))
            return [
                ("通道更多", {"cnn_channels": bounded_int_value("cnn_channels", 32, channel_delta, 4, 256)}),
                ("通道更少", {"cnn_channels": bounded_int_value("cnn_channels", 32, -channel_delta, 4, 256)}),
                ("窗口更长", {"cnn_window": bounded_int_value("cnn_window", 20, max(2, int(6 * scale)), 5, 120)}),
                ("窗口更短", {"cnn_window": bounded_int_value("cnn_window", 20, -max(2, int(6 * scale)), 5, 120)}),
                ("卷积层更多", {"cnn_layers": bounded_int_value("cnn_layers", 2, 1, 1, 5)}),
                ("卷积层更少", {"cnn_layers": bounded_int_value("cnn_layers", 2, -1, 1, 5)}),
                ("学习率更大", {"cnn_learning_rate": bounded_float_value("cnn_learning_rate", 0.001, 0.0002 * scale, 0.0001, 0.1)}),
                ("学习率更小", {"cnn_learning_rate": bounded_float_value("cnn_learning_rate", 0.001, -0.0002 * scale, 0.0001, 0.1)}),
                ("训练轮数更多", {"cnn_max_iter": bounded_int_value("cnn_max_iter", 160, iter_delta, 20, 1000)}),
                ("训练轮数更少", {"cnn_max_iter": bounded_int_value("cnn_max_iter", 160, -iter_delta, 20, 1000)}),
            ]
        layer_delta = max(8, int(16 * scale))
        return [
            ("网络稍大", {"mlp_layer_size": bounded_int_value("mlp_layer_size", 64, layer_delta, 8, 512)}),
            ("网络稍小", {"mlp_layer_size": bounded_int_value("mlp_layer_size", 64, -layer_delta, 8, 512)}),
            ("学习率更大", {"mlp_learning_rate": bounded_float_value("mlp_learning_rate", 0.001, 0.0002 * scale, 0.0001, 0.1)}),
            ("学习率更小", {"mlp_learning_rate": bounded_float_value("mlp_learning_rate", 0.001, -0.0002 * scale, 0.0001, 0.1)}),
            ("L2更弱", {"mlp_alpha": bounded_float_value("mlp_alpha", 0.002, -0.001 * scale, 0.00001, 1.0)}),
            ("L2更强", {"mlp_alpha": bounded_float_value("mlp_alpha", 0.002, 0.001 * scale, 0.00001, 1.0)}),
            ("层数更多", {"mlp_layer_count": bounded_int_value("mlp_layer_count", 1, 1, 1, 4)}),
            ("层数更少", {"mlp_layer_count": bounded_int_value("mlp_layer_count", 1, -1, 1, 4)}),
        ]

    def _auto_learn_year_regime(self, hold_return: float, max_drawdown: float) -> str:
        if hold_return >= 0.12 and max_drawdown > -0.18:
            return "bull"
        if hold_return <= -0.06 or max_drawdown <= -0.22:
            return "bear"
        return "sideways"

    def _auto_learn_regime_label(self, regime: str) -> str:
        if regime == "bull":
            return "牛市/强势"
        if regime == "bear":
            return "熊市/弱势"
        return "震荡/不确定"

    def _update_auto_learn_task(self, task_id: str | None, progress: int, message: str) -> None:
        if not task_id:
            return
        with self.lock:
            task = self.auto_learn_tasks.get(task_id)
            if task is not None:
                task["progress"] = progress
                task["message"] = message
                task["updated_at"] = time.time()

    def _auto_learn_model(self, payload: dict[str, Any]) -> str:
        requested = str(payload.get("auto_learn_model", "current")).strip().lower()
        if requested in {"", "current", "active"}:
            requested = str(payload.get("ml_model", self.ml_model))
        return self._normalize_ml_model(requested)

    def _auto_learn_budget(self, value: Any) -> str:
        raw = str(value or "balanced").strip().lower()
        if raw in {"quick", "fast", "smoke"}:
            return "quick"
        if raw in {"deep", "full"}:
            return "deep"
        return "balanced"

    def _auto_learn_years(self, raw: Any) -> list[int]:
        if raw is None or str(raw).strip() == "":
            return [2021, 2022, 2023, 2024, 2025]
        years: list[int] = []
        for item in str(raw).replace(";", ",").split(","):
            item = item.strip()
            if not item:
                continue
            try:
                year = int(item)
            except ValueError:
                continue
            if 2015 <= year <= 2030 and year not in years:
                years.append(year)
        return years or [2021, 2022, 2023, 2024, 2025]

    def _auto_learn_candidates(self, payload: dict[str, Any], model: str, budget: str) -> list[dict[str, Any]]:
        prefix = self._ml_prefix(model)
        profile_order = ["aggressive", "balanced", "conservative"]
        max_candidates = {"quick": 12, "balanced": 21, "deep": 33}[budget]
        candidates: list[dict[str, Any]] = []

        def add_candidate(
            label: str,
            candidate_payload: dict[str, Any],
            profile: str,
            baseline: bool = False,
            phase: str = "coarse",
        ) -> None:
            key = json.dumps({"profile": profile, "payload": candidate_payload}, sort_keys=True, ensure_ascii=False)
            if any(item["dedupe_key"] == key for item in candidates):
                return
            candidates.append(
                {
                    "label": label,
                    "payload": candidate_payload,
                    "profile": profile,
                    "baseline": baseline,
                    "phase": phase,
                    "dedupe_key": key,
                }
            )

        for profile in profile_order:
            base_params = self._candidate_base_params(payload, model, profile)
            add_candidate(
                f"{strategy_profile(profile).label}默认",
                {
                    "ml_strategy_profile": profile,
                    f"{prefix}_strategy_profile": profile,
                },
                profile,
                baseline=True,
                phase="default",
            )
            for phase, scale, limit in (("粗搜", 2.0, 4), ("细搜", 0.75, 4)):
                for label, updates in self._threshold_candidate_updates(model, base_params, scale)[:limit]:
                    add_candidate(
                        f"{strategy_profile(profile).label}{phase}{label}",
                        self._auto_learn_custom_payload(model, profile, base_params, updates),
                        profile,
                        phase="coarse" if phase == "粗搜" else "fine",
                    )
            model_mutations = self._model_param_mutations(model, base_params, 1.0)
            for label, updates in model_mutations[:4]:
                add_candidate(
                    f"{strategy_profile(profile).label}模型{label}",
                    self._auto_learn_custom_payload(model, profile, base_params, updates),
                    profile,
                    phase="model",
                )
            for index, updates in enumerate(self._random_model_candidate_updates(model, base_params, 1.0, 3), start=1):
                add_candidate(
                    f"{strategy_profile(profile).label}随机扰动{index}",
                    self._auto_learn_custom_payload(model, profile, base_params, updates),
                    profile,
                    phase="random",
                )

        interleaved: list[dict[str, Any]] = []
        grouped = {
            profile: self._ordered_auto_learn_candidates([item for item in candidates if item.get("profile") == profile])
            for profile in profile_order
        }
        while any(grouped.values()):
            for profile in profile_order:
                if grouped[profile]:
                    interleaved.append(grouped[profile].pop(0))
        trimmed = interleaved[:max_candidates]
        for item in trimmed:
            item.pop("dedupe_key", None)
        return trimmed

    def _ordered_auto_learn_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        phase_groups: dict[str, list[dict[str, Any]]] = {
            "default": [],
            "incumbent": [],
            "coarse": [],
            "fine": [],
            "random": [],
            "model": [],
            "other": [],
        }
        for item in candidates:
            phase = str(item.get("phase") or "other")
            phase_groups.setdefault(phase, phase_groups["other"]).append(item)
        ordered: list[dict[str, Any]] = []
        for phase in ("default", "incumbent"):
            ordered.extend(phase_groups.get(phase, []))
        phase_cycle = ("coarse", "fine", "random", "model")
        while any(phase_groups.get(phase) for phase in phase_cycle):
            for phase in phase_cycle:
                group = phase_groups.get(phase) or []
                if group:
                    ordered.append(group.pop(0))
        ordered.extend(phase_groups.get("other", []))
        return ordered

    def _auto_learn_custom_payload(
        self,
        model: str,
        profile: str,
        base_params: dict[str, Any],
        updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prefix = self._ml_prefix(model)
        payload = {
            "ml_strategy_profile": "custom",
            f"{prefix}_strategy_profile": "custom",
            f"{prefix}_buy_threshold": base_params.get("ml_buy_threshold", ""),
            f"{prefix}_sell_threshold": base_params.get("ml_sell_threshold", ""),
            f"{prefix}_min_samples": base_params.get("ml_min_samples", 80),
            **self._model_specific_candidate_params(model, base_params),
            **(updates or {}),
        }
        # Keep the ownership profile outside the model payload. The candidate can cross style,
        # but its score still belongs to the profile that generated it.
        return payload

    def _threshold_candidate_updates(
        self,
        model: str,
        base_params: dict[str, Any],
        scale: float,
    ) -> list[tuple[str, dict[str, Any]]]:
        prefix = self._ml_prefix(model)
        buy = self._clamp_threshold(base_params.get("ml_buy_threshold"), 0.58, 0)
        sell = self._clamp_threshold(base_params.get("ml_sell_threshold"), 0.30, 0)
        buy_step = round(0.04 * scale, 3)
        sell_step = round(0.035 * scale, 3)
        sample_step = max(10, int(30 * scale))
        try:
            samples = int(float(base_params.get("ml_min_samples", 80)))
        except (TypeError, ValueError):
            samples = 80
        return [
            ("买入更积极", {f"{prefix}_buy_threshold": self._clamp_threshold(buy, 0.58, -buy_step)}),
            ("买入更谨慎", {f"{prefix}_buy_threshold": self._clamp_threshold(buy, 0.58, buy_step)}),
            ("卖出更耐心", {f"{prefix}_sell_threshold": self._clamp_threshold(sell, 0.30, -sell_step)}),
            ("卖出更果断", {f"{prefix}_sell_threshold": self._clamp_threshold(sell, 0.30, sell_step)}),
            (
                "更进攻组合",
                {
                    f"{prefix}_buy_threshold": self._clamp_threshold(buy, 0.58, -buy_step),
                    f"{prefix}_sell_threshold": self._clamp_threshold(sell, 0.30, -sell_step),
                },
            ),
            (
                "更防守组合",
                {
                    f"{prefix}_buy_threshold": self._clamp_threshold(buy, 0.58, buy_step),
                    f"{prefix}_sell_threshold": self._clamp_threshold(sell, 0.30, sell_step),
                },
            ),
            (f"样本更少", {f"{prefix}_min_samples": max(20, samples - sample_step)}),
            (f"样本更多", {f"{prefix}_min_samples": min(1000, samples + sample_step)}),
        ]

    def _random_model_candidate_updates(
        self,
        model: str,
        base_params: dict[str, Any],
        scale: float,
        count: int,
    ) -> list[dict[str, Any]]:
        prefix = self._ml_prefix(model)
        buy = self._clamp_threshold(base_params.get("ml_buy_threshold"), 0.58, 0)
        sell = self._clamp_threshold(base_params.get("ml_sell_threshold"), 0.30, 0)
        try:
            samples = int(float(base_params.get("ml_min_samples", 80)))
        except (TypeError, ValueError):
            samples = 80
        updates: list[dict[str, Any]] = []
        for _ in range(max(0, count)):
            item: dict[str, Any] = {
                f"{prefix}_buy_threshold": self._clamp_threshold(buy, 0.58, random.uniform(-0.075, 0.075) * scale),
                f"{prefix}_sell_threshold": self._clamp_threshold(sell, 0.30, random.uniform(-0.065, 0.065) * scale),
                f"{prefix}_min_samples": max(20, min(1000, samples + random.choice([-60, -30, 30, 60]))),
            }
            item.update(self._random_model_specific_updates(model, base_params, scale))
            updates.append(item)
        return updates

    def _random_model_specific_updates(self, model: str, params: dict[str, Any], scale: float) -> dict[str, Any]:
        def int_param(key: str, default: int) -> int:
            try:
                return int(float(params.get(key, default)))
            except (TypeError, ValueError):
                return default

        def float_param(key: str, default: float) -> float:
            try:
                return float(params.get(key, default))
            except (TypeError, ValueError):
                return default

        def bounded_int(value: int, minimum: int, maximum: int) -> int:
            return min(max(value, minimum), maximum)

        def bounded_float(value: float, minimum: float, maximum: float) -> float:
            return round(min(max(value, minimum), maximum), 5)

        if model == "random_forest":
            return {
                "rf_n_estimators": bounded_int(int_param("rf_n_estimators", 100) + random.choice([-80, -40, 40, 80]), 10, 500),
                "rf_max_depth": bounded_int(int_param("rf_max_depth", 5) + random.choice([-2, -1, 1, 2]), 1, 30),
                "rf_min_samples_leaf": bounded_int(int_param("rf_min_samples_leaf", 5) + random.choice([-10, -5, 5, 10]), 1, 100),
                "rf_max_features_sqrt": random.choice([True, False, bool(params.get("rf_max_features_sqrt", True))]),
            }
        if model == "hist_gradient_boosting":
            return {
                "hgb_max_iter": bounded_int(int_param("hgb_max_iter", 140) + random.choice([-60, -30, 30, 60]), 20, 500),
                "hgb_learning_rate": bounded_float(float_param("hgb_learning_rate", 0.03) + random.uniform(-0.012, 0.012) * scale, 0.005, 0.5),
                "hgb_max_leaf_nodes": bounded_int(int_param("hgb_max_leaf_nodes", 15) + random.choice([-6, -3, 3, 6]), 3, 100),
                "hgb_min_samples_leaf": bounded_int(int_param("hgb_min_samples_leaf", 12) + random.choice([-8, -4, 4, 8]), 2, 100),
                "hgb_l2_regularization": bounded_float(float_param("hgb_l2_regularization", 0.08) + random.uniform(-0.08, 0.08) * scale, 0.0, 10.0),
            }
        if model == "xgboost":
            return {
                "xgb_n_estimators": bounded_int(int_param("xgb_n_estimators", 120) + random.choice([-60, -30, 30, 60]), 20, 500),
                "xgb_max_depth": bounded_int(int_param("xgb_max_depth", 3) + random.choice([-2, -1, 1, 2]), 1, 12),
                "xgb_learning_rate": bounded_float(float_param("xgb_learning_rate", 0.035) + random.uniform(-0.015, 0.015) * scale, 0.005, 0.5),
                "xgb_subsample": bounded_float(float_param("xgb_subsample", 0.85) + random.uniform(-0.12, 0.12) * scale, 0.4, 1.0),
                "xgb_colsample_bytree": bounded_float(float_param("xgb_colsample_bytree", 0.85) + random.uniform(-0.12, 0.12) * scale, 0.4, 1.0),
                "xgb_min_child_weight": bounded_float(float_param("xgb_min_child_weight", 5.0) + random.uniform(-2.0, 2.0) * scale, 0.1, 50.0),
                "xgb_reg_lambda": bounded_float(float_param("xgb_reg_lambda", 1.5) + random.uniform(-1.0, 1.0) * scale, 0.0, 20.0),
            }
        if model == "cnn":
            return {
                "cnn_window": bounded_int(int_param("cnn_window", 20) + random.choice([-8, -4, 4, 8]), 5, 120),
                "cnn_channels": bounded_int(int_param("cnn_channels", 32) + random.choice([-16, -8, 8, 16]), 4, 256),
                "cnn_kernel_size": random.choice([3, 5, 7]),
                "cnn_layers": bounded_int(int_param("cnn_layers", 2) + random.choice([-1, 0, 1]), 1, 5),
                "cnn_max_iter": bounded_int(int_param("cnn_max_iter", 160) + random.choice([-60, -30, 30, 60]), 50, 600),
                "cnn_learning_rate": bounded_float(float_param("cnn_learning_rate", 0.001) + random.uniform(-0.0006, 0.0006) * scale, 0.0001, 0.1),
                "cnn_alpha": bounded_float(float_param("cnn_alpha", 0.001) + random.uniform(-0.003, 0.003) * scale, 0.00001, 1.0),
                "cnn_dropout": bounded_float(float_param("cnn_dropout", 0.08) + random.uniform(-0.05, 0.05) * scale, 0.0, 0.8),
            }
        return {
            "mlp_layer_size": bounded_int(int_param("mlp_layer_size", 64) + random.choice([-32, -16, 16, 32]), 8, 512),
            "mlp_layer_count": bounded_int(int_param("mlp_layer_count", 1) + random.choice([-1, 0, 1]), 1, 4),
            "mlp_max_iter": bounded_int(int_param("mlp_max_iter", 180) + random.choice([-60, -30, 30, 60]), 50, 600),
            "mlp_learning_rate": bounded_float(float_param("mlp_learning_rate", 0.001) + random.uniform(-0.0006, 0.0006) * scale, 0.0001, 0.1),
            "mlp_alpha": bounded_float(float_param("mlp_alpha", 0.002) + random.uniform(-0.003, 0.003) * scale, 0.00001, 1.0),
        }

    def _ml_prefix(self, model: str) -> str:
        if model == "xgboost":
            return "xgb"
        if model == "mlp":
            return "mlp"
        if model == "cnn":
            return "cnn"
        if model == "hist_gradient_boosting":
            return "hgb"
        return "rf"

    def _candidate_base_params(self, payload: dict[str, Any], model: str, profile: str) -> dict[str, Any]:
        prefix = self._ml_prefix(model)
        defaults = self._profile_ml_params(profile, model)
        params = dict(defaults)
        for key, value in payload.items():
            if key.startswith(f"{prefix}_"):
                mapped = key.replace(f"{prefix}_", "ml_", 1)
                if mapped in {"ml_buy_threshold", "ml_sell_threshold", "ml_min_samples", "ml_training_mode", "ml_target"}:
                    params[mapped] = value
                else:
                    params[key] = value
        return params

    def _model_specific_candidate_params(self, model: str, params: dict[str, Any]) -> dict[str, Any]:
        keys_by_model = {
            "random_forest": [
                "rf_n_estimators",
                "rf_max_depth",
                "rf_min_samples_leaf",
                "rf_max_features_sqrt",
                "rf_class_weight",
                "rf_sample_weight",
            ],
            "hist_gradient_boosting": [
                "hgb_max_iter",
                "hgb_learning_rate",
                "hgb_max_leaf_nodes",
                "hgb_min_samples_leaf",
                "hgb_l2_regularization",
                "hgb_early_stopping",
                "hgb_sample_weight",
            ],
            "xgboost": [
                "xgb_n_estimators",
                "xgb_max_depth",
                "xgb_learning_rate",
                "xgb_subsample",
                "xgb_colsample_bytree",
                "xgb_min_child_weight",
                "xgb_reg_lambda",
                "xgb_sample_weight",
            ],
            "mlp": [
                "mlp_layer_size",
                "mlp_layer_count",
                "mlp_max_iter",
                "mlp_learning_rate",
                "mlp_alpha",
                "mlp_early_stopping",
            ],
            "cnn": [
                "cnn_window",
                "cnn_channels",
                "cnn_kernel_size",
                "cnn_layers",
                "cnn_max_iter",
                "cnn_learning_rate",
                "cnn_alpha",
                "cnn_dropout",
                "cnn_early_stopping",
                "cnn_sample_weight",
            ],
        }
        return {key: params[key] for key in keys_by_model.get(model, []) if key in params}

    def _clamp_threshold(self, value: Any, default: float, delta: float) -> float:
        try:
            base = float(value if str(value).strip() != "" else default)
        except (TypeError, ValueError):
            base = default
        return round(min(max(base + delta, 0.05), 0.95), 3)

    def _score_auto_learn_candidate(
        self,
        candidate: dict[str, Any],
        per_year: list[dict[str, Any]],
        score_weights: dict[str, float],
    ) -> dict[str, Any]:
        valid = [item for item in per_year if "return_pct" in item]
        expected_count = len(per_year)
        failed_count = max(0, expected_count - len(valid))
        target_regime = self._target_regime_for_candidate(candidate)
        candidate_profile = strategy_profile(str(candidate.get("profile") or "balanced")).key
        if not valid:
            return {
                "label": candidate["label"],
                "profile": candidate_profile,
                "profile_label": strategy_profile(candidate_profile).label,
                "phase": candidate.get("phase", ""),
                "payload": candidate["payload"],
                "per_year": per_year,
                "target_regime": target_regime,
                "target_regime_label": self._auto_learn_regime_label(target_regime),
                "score": -9999.0,
                "specialized_score": -9999.0,
                "fallback_score": -9999.0,
                "composite_score": -9999.0,
                "avg_return_pct": 0.0,
                "worst_return_pct": 0.0,
                "avg_excess_return_pct": 0.0,
                "beat_buy_hold_count": 0,
                "valid_year_count": 0,
                "failed_year_count": failed_count,
                "expected_year_count": expected_count,
                "reason": "所有年份都没有跑通。",
            }
        avg_return = sum(float(item["return_pct"]) for item in valid) / len(valid)
        worst_return = min(float(item["return_pct"]) for item in valid)
        avg_excess = sum(float(item.get("excess_return_pct") or 0.0) for item in valid) / len(valid)
        worst_drawdown = min(float(item.get("max_drawdown_pct") or 0.0) for item in valid)
        beat_count = sum(1 for item in valid if float(item.get("excess_return_pct") or 0.0) > 0)
        avg_trades = sum(int(item.get("trade_count") or 0) for item in valid) / len(valid)
        target_years = [item for item in valid if item.get("regime") == target_regime]
        fallback_years = [item for item in valid if item.get("regime") != target_regime]
        specialized_score = self._auto_learn_group_score(target_years or valid, target_regime, specialized=True)
        fallback_score = self._auto_learn_group_score(fallback_years or valid, target_regime, specialized=False)
        composite_score = self._auto_learn_group_score(valid, target_regime, specialized=False)
        score = (
            score_weights["specialized"] * specialized_score
            + score_weights["fallback"] * fallback_score
            + score_weights["composite"] * composite_score
        )
        if avg_trades < 1:
            score -= 0.08
        elif avg_trades > 80:
            score -= min(0.08, (avg_trades - 80) / 1000)
        if candidate.get("baseline"):
            score -= 0.015
        if expected_count:
            valid_ratio = len(valid) / expected_count
            score -= failed_count * 0.12
            score -= (1.0 - valid_ratio) * 0.20
            if len(valid) < max(2, expected_count // 2 + 1):
                score -= 0.25
        return {
            "label": candidate["label"],
            "profile": candidate_profile,
            "profile_label": strategy_profile(candidate_profile).label,
            "baseline": bool(candidate.get("baseline")),
            "phase": candidate.get("phase", ""),
            "payload": candidate["payload"],
            "per_year": per_year,
            "target_regime": target_regime,
            "target_regime_label": self._auto_learn_regime_label(target_regime),
            "score": round(score, 6),
            "specialized_score": round(specialized_score, 6),
            "fallback_score": round(fallback_score, 6),
            "composite_score": round(composite_score, 6),
            "avg_return_pct": avg_return,
            "worst_return_pct": worst_return,
            "avg_excess_return_pct": avg_excess,
            "worst_drawdown_pct": worst_drawdown,
            "beat_buy_hold_count": beat_count,
            "valid_year_count": len(valid),
            "failed_year_count": failed_count,
            "expected_year_count": expected_count,
            "avg_trade_count": avg_trades,
            "reason": self._auto_learn_reason(
                avg_return,
                avg_excess,
                worst_return,
                beat_count,
                len(valid),
                expected_count,
                failed_count,
            ),
        }

    def _target_regime_for_candidate(self, candidate: dict[str, Any]) -> str:
        if candidate.get("profile"):
            profile = strategy_profile(str(candidate.get("profile"))).key
            if profile == "aggressive":
                return "bull"
            if profile == "conservative":
                return "bear"
            return "sideways"
        payload = candidate.get("payload", {})
        raw_profile = (
            payload.get("rf_strategy_profile")
            or payload.get("hgb_strategy_profile")
            or payload.get("xgb_strategy_profile")
            or payload.get("mlp_strategy_profile")
            or payload.get("cnn_strategy_profile")
            or payload.get("ml_strategy_profile")
            or ""
        )
        profile = strategy_profile(str(raw_profile or "balanced")).key
        if profile == "aggressive":
            return "bull"
        if profile == "conservative":
            return "bear"
        return "sideways"

    def _auto_learn_group_score(
        self,
        rows: list[dict[str, Any]],
        target_regime: str,
        specialized: bool,
    ) -> float:
        if not rows:
            return -9999.0
        scores = [self._auto_learn_year_score(item, target_regime, specialized) for item in rows]
        return sum(scores) / len(scores)

    def _auto_learn_year_score(self, row: dict[str, Any], target_regime: str, specialized: bool) -> float:
        return_pct = float(row.get("return_pct") or 0.0)
        excess = float(row.get("excess_return_pct") or 0.0)
        drawdown = float(row.get("max_drawdown_pct") or 0.0)
        trades = int(row.get("trade_count") or 0)
        beat_bonus = 0.025 if excess > 0 else 0.0
        trade_penalty = 0.0
        if trades < 1:
            trade_penalty = 0.06
        elif trades > 80:
            trade_penalty = min(0.08, (trades - 80) / 1000)
        if specialized and target_regime == "bull":
            return 0.85 * return_pct + 0.45 * excess + 0.08 * drawdown + beat_bonus - trade_penalty
        if specialized and target_regime == "bear":
            positive_bonus = 0.035 if return_pct > 0 else 0.0
            loss_penalty = min(0.14, abs(return_pct) * 0.70) if return_pct < 0 else 0.0
            severe_drawdown_penalty = min(0.12, abs(drawdown) * 0.65) if drawdown < -0.08 else 0.0
            return (
                0.30 * return_pct
                + 0.55 * excess
                + 0.75 * drawdown
                + beat_bonus
                + positive_bonus
                - loss_penalty
                - severe_drawdown_penalty
                - trade_penalty
            )
        if specialized and target_regime == "sideways":
            return 0.45 * return_pct + 0.45 * excess + 0.30 * drawdown + beat_bonus - trade_penalty
        return 0.50 * return_pct + 0.35 * excess + 0.25 * drawdown + beat_bonus - trade_penalty

    def _auto_learn_reason(
        self,
        avg_return: float,
        avg_excess: float,
        worst_return: float,
        beat_count: int,
        valid_count: int,
        expected_count: int | None = None,
        failed_count: int = 0,
    ) -> str:
        if expected_count and failed_count:
            return f"只有 {valid_count}/{expected_count} 个年份跑通，失败年份已重罚；该结果只能参考，不能认为稳定。"
        if avg_return > 0 and avg_excess > 0 and beat_count >= max(1, valid_count - 1):
            return "平均收益和跑赢持有表现都较好，且跨年份稳定性较强。"
        if avg_return > 0 and worst_return < 0:
            return "平均收益为正，但弱市年份仍有回撤，需要结合风险偏好。"
        if avg_excess > 0:
            return "相对持有有优势，但绝对收益一般，适合继续小步调参。"
        return "没有明显跑赢持有，暂不建议直接替换当前设置。"

    def _buy_hold_return(self, backtest: dict[str, Any]) -> float:
        returns: list[float] = []
        for points in (backtest.get("symbol_curves") or {}).values():
            if not points:
                continue
            first = float(points[0].get("close") or 0.0)
            last = float(points[-1].get("close") or 0.0)
            if first > 0:
                returns.append((last - first) / first)
        if not returns:
            return 0.0
        return sum(returns) / len(returns)

    def _max_drawdown(self, daily: list[dict[str, Any]]) -> float:
        peak = 0.0
        worst = 0.0
        for row in daily:
            equity = float(row.get("equity") or 0.0)
            if equity <= 0:
                continue
            peak = max(peak, equity)
            if peak > 0:
                worst = min(worst, (equity - peak) / peak)
        return worst

    def run_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            mode = str(payload.get("mode", "random")).strip().lower()
            annual_year = self._backtest_year_from_payload(payload, mode)
            fixed_year = mode in {"year", "fixed_year", "one_year"}
            annual_backtest = annual_year is not None
            fixed_window = fixed_year or annual_backtest
            time_granularity = str(payload.get("time_granularity", "fast")).strip().lower()
            self._apply_global_preferences(payload)
            self._apply_runtime_settings(payload)
            slow_granularity = time_granularity in {"slow", "actual", "intraday"}
            requested_engine = self.run_engine
            ml_requested = requested_engine in {
                "ml",
                "machine_learning",
                "random_forest",
                "hist_gradient_boosting",
                "xgboost",
                "mlp",
                "cnn",
            }
            ai_requested = requested_engine not in {
                "local",
                "heuristic",
                "ml",
                "machine_learning",
                "random_forest",
                "hist_gradient_boosting",
                "xgboost",
                "mlp",
                "cnn",
            }
            if slow_granularity and annual_backtest:
                raise RuntimeError("Natural-year backtest only supports fast daily data.")
            if ai_requested and fixed_window:
                raise RuntimeError("AI long-window backtest is disabled. Use local or machine-learning mode for yearly tests.")

            interval_minutes = self._backtest_interval_minutes(payload)
            interval_seconds = interval_minutes * 60
            bars_per_day = self._bars_per_day(interval_minutes)
            requested_days = 366 if annual_backtest else 252 if fixed_year else self._int(payload, "days", 14)
            days = max(1, min(366 if annual_backtest else 260 if fixed_year else 120, requested_days))
            desired_steps = days * bars_per_day if slow_granularity else days
            if ai_requested and slow_granularity and desired_steps > 3:
                desired_steps = 3
            elif ai_requested and desired_steps > 14:
                desired_steps = 14

            histories: dict[str, list[Bar]] = {}
            errors: list[str] = []
            market_data = self._market_data_for_backtest(annual_year) if annual_backtest else self.market_data
            if ai_requested and slow_granularity:
                errors.append("AI slow backtest is limited to avoid too many model calls.")
            for symbol in self.settings.symbols:
                try:
                    if slow_granularity:
                        lookback_days = self._history_lookback_days(days, fixed_window, ml_requested)
                        bars = market_data.history_intraday(symbol, interval_minutes, lookback_days)
                    else:
                        bars = market_data.history(symbol)
                    normalized = _snapshot_from_bars(symbol, bars).symbol
                    histories[normalized] = bars
                except Exception as exc:
                    errors.append(f"{symbol}: {exc}")

            if not histories:
                raise RuntimeError("No valid historical data loaded for backtest.")

            timeline = self._aligned_backtest_timeline(
                histories,
                desired_steps,
                fixed_window,
                slow_granularity,
                ml_requested,
                annual_year=annual_year,
            )
            actual_steps = len(timeline)
            if actual_steps <= 0:
                raise RuntimeError("Need at least 21 aligned bars per symbol for backtest.")
            if slow_granularity and fixed_window and actual_steps < desired_steps:
                errors.append(
                    f"Slow intraday data is incomplete; using {actual_steps} available points."
                )

            strategy = strategy_profile(self.strategy_key)
            broker = PaperBroker(self.settings.starting_cash, log_orders=False)
            risk = self._build_risk(self.settings, strategy.key)
            if requested_engine in {"local", "heuristic"}:
                ai = HeuristicSignalProvider(
                    strategy.key,
                    buy_score=self._local_buy_score_for(strategy.key),
                    max_buys_per_day=self._max_buy_signals_for(strategy.key),
                    auto_strategy=self.strategy_auto_select == "on",
                )
                engine = f"heuristic-{strategy.key}"
            elif ml_requested:
                ai = MachineLearningSignalProvider(
                    strategy.key,
                    self.ml_model,
                    params=self.ml_params,
                    max_buys_per_day=self._max_buy_signals_for(strategy.key),
                    auto_strategy=self.strategy_auto_select == "on",
                )
                engine = f"{ai.engine_key}-{strategy.key}"
            else:
                ai = self._build_ai(self.settings, strategy.key, self._max_buy_signals_for(strategy.key))
                engine = self._ai_engine_name(self.settings, strategy.key)
            daily: list[dict[str, Any]] = []
            all_orders: list[dict[str, Any]] = []
            fallback_count = 0
            window_offset = int(timeline[0].get("_offset", 0))
            if ml_requested:
                training_bar_limit = self._training_bar_limit(slow_granularity, bars_per_day)
                cutoff_timestamp = min(
                    histories[symbol][int(timeline[0][symbol])].timestamp
                    for symbol in histories
                )
                training_histories, training_errors = self._load_training_histories(
                    market_data,
                    cutoff_timestamp=cutoff_timestamp,
                    bar_limit=training_bar_limit,
                )
                trade_training_histories, trade_training_errors = self._load_training_histories(
                    market_data,
                    cutoff_timestamp=cutoff_timestamp,
                    bar_limit=training_bar_limit,
                    symbols=self.settings.symbols,
                )
                training_histories.update(trade_training_histories)
                errors.extend(training_errors[:8])
                errors.extend(trade_training_errors[:4])
                if not training_histories:
                    training_histories = {
                        symbol: bars[max(0, int(timeline[0][symbol]) - training_bar_limit) : int(timeline[0][symbol])]
                        for symbol, bars in histories.items()
                    }
                    errors.append("训练池数据不足，已回退使用交易池历史数据。")
                self._prepare_signal_provider(ai, training_histories)

            for offset in range(actual_steps):
                timeline_row = timeline[offset]
                snapshots: list[MarketSnapshot] = []
                day_timestamps: list[int] = []
                for symbol, bars in histories.items():
                    idx = int(timeline_row[symbol])
                    prefix = bars[: idx + 1]
                    snapshot = _snapshot_from_bars(symbol, prefix)
                    snapshots.append(snapshot)
                    day_timestamps.append(prefix[-1].timestamp)
                    broker.mark_price(snapshot.symbol, snapshot.last_price)

                broker.day_start_equity = broker.get_account().equity
                if not ml_requested:
                    self._prepare_signal_provider(
                        ai,
                        {
                            symbol: bars[: int(timeline_row[symbol]) + 1]
                            for symbol, bars in histories.items()
                        },
                    )
                else:
                    self._prepare_decision_histories(
                        ai,
                        {
                            symbol: bars[: int(timeline_row[symbol]) + 1]
                            for symbol, bars in histories.items()
                        },
                    )
                signals = ai.decide(snapshots)
                signals = self._limit_buy_signals(signals, strategy.key)
                fallback_count += sum(1 for signal in signals if "fallback" in signal.reason.lower())
                signal_by_symbol = {signal.symbol: signal for signal in signals}
                day_orders: list[dict[str, Any]] = []

                for snapshot in snapshots:
                    signal = signal_by_symbol.get(snapshot.symbol)
                    if signal is None:
                        continue
                    order = risk.build_order(
                        broker.get_account(),
                        snapshot,
                        signal,
                        broker.get_position(snapshot.symbol),
                    )
                    if order is not None:
                        broker.submit_order(order)
                        order_row = {
                            "date": max(day_timestamps),
                            "symbol": order.symbol,
                            "side": order.side.value,
                            "quantity": order.quantity,
                            "price": order.limit_price,
                            "reason": order.reason,
                        }
                        day_orders.append(order_row)
                        all_orders.append(order_row)

                account = broker.get_account()
                daily.append(
                    {
                        "timestamp": max(day_timestamps),
                        "equity": account.equity,
                        "cash": account.cash,
                        "orders": day_orders,
                        "signals": [asdict(signal) for signal in signals],
                    }
                )

            final_account = broker.get_account()
            positions = [
                {
                    **asdict(position),
                    "market_price": broker.last_prices.get(symbol, position.average_price),
                    "market_value": position.quantity * broker.last_prices.get(symbol, position.average_price),
                }
                for symbol, position in broker.positions.items()
            ]
            symbol_curves = {
                symbol: [
                    {
                        "timestamp": bars[int(row[symbol])].timestamp,
                        "close": bars[int(row[symbol])].close,
                        "volume": bars[int(row[symbol])].volume,
                    }
                    for row in timeline
                    if symbol in row and int(row[symbol]) < len(bars)
                ]
                for symbol, bars in histories.items()
            }
            self.backtest = {
                "days": days,
                "actual_days": actual_steps if not slow_granularity else max(1, round(actual_steps / bars_per_day, 2)),
                "actual_steps": actual_steps,
                "engine": engine,
                "strategy": strategy.key,
                "strategy_label": strategy.label,
                "ml_model": self.ml_model,
                "ml_model_label": self._ml_model_label(self.ml_model),
                "ml_params": self.ml_params,
                "ml_training_mode": self.ml_params.get("ml_training_mode", "general"),
                "ml_training_days": self.ml_training_days,
                "mode": "natural_year" if annual_backtest else "fixed_year" if fixed_year else "random",
                "backtest_year": annual_year,
                "time_granularity": "slow" if slow_granularity else "fast",
                "time_granularity_label": (
                    f"Slow {interval_minutes} min/step" if slow_granularity else "Fast daily"
                ),
                "interval_seconds": interval_seconds,
                "interval_minutes": interval_minutes if slow_granularity else None,
                "random_window": not fixed_window,
                "window_offset": window_offset,
                "window_start": daily[0]["timestamp"] if daily else None,
                "window_end": daily[-1]["timestamp"] if daily else None,
                "start_equity": self.settings.starting_cash,
                "end_equity": final_account.equity,
                "return_pct": (final_account.equity - self.settings.starting_cash) / self.settings.starting_cash,
                "trade_count": len(all_orders),
                "fallback_count": fallback_count,
                "daily": daily,
                "symbol_curves": symbol_curves,
                "marker_orders": all_orders,
                "orders": all_orders[-50:],
                "positions": positions,
                "errors": errors,
            }
            state = self._state_unlocked()
            if not self._bool(payload, "_suppress_state_save", False):
                self.storage.save_latest_state(state)
            return state

    def _aligned_backtest_timeline(
        self,
        histories: dict[str, list[Bar]],
        desired_steps: int,
        fixed_year: bool,
        slow_granularity: bool,
        ml_requested: bool,
        annual_year: int | None = None,
    ) -> list[dict[str, int]]:
        if not histories:
            return []

        def bar_key(bar: Bar) -> int:
            if slow_granularity:
                return int(bar.timestamp)
            return dt.datetime.fromtimestamp(bar.timestamp).date().toordinal()

        indexes_by_symbol: dict[str, dict[int, int]] = {}
        common_keys: set[int] | None = None
        for symbol, bars in histories.items():
            key_to_index: dict[int, int] = {}
            for index, bar in enumerate(bars):
                if index < 20:
                    continue
                key_to_index[bar_key(bar)] = index
            indexes_by_symbol[symbol] = key_to_index
            symbol_keys = set(key_to_index)
            common_keys = symbol_keys if common_keys is None else common_keys & symbol_keys

        ordered_keys = sorted(common_keys or set())
        if annual_year is not None:
            start_key = dt.date(annual_year, 1, 1).toordinal()
            end_key = dt.date(annual_year, 12, 31).toordinal()
            ordered_keys = [key for key in ordered_keys if start_key <= key <= end_key]
        if not ordered_keys:
            return []

        actual_steps = len(ordered_keys) if annual_year is not None else min(max(1, desired_steps), len(ordered_keys))
        if annual_year is not None:
            start_offset = 0
        elif fixed_year:
            start_offset = len(ordered_keys) - actual_steps
        else:
            max_offset = max(0, len(ordered_keys) - actual_steps)
            min_offset = min(max_offset, 100) if ml_requested else 0
            start_offset = random.randint(min_offset, max_offset)

        selected_keys = ordered_keys[start_offset : start_offset + actual_steps]
        timeline: list[dict[str, int]] = []
        for key in selected_keys:
            row = {symbol: indexes[key] for symbol, indexes in indexes_by_symbol.items()}
            row["_offset"] = start_offset
            timeline.append(row)
        return timeline

    def start_auto(self, payload: dict[str, Any]) -> dict[str, Any]:
        interval = max(30, self._int(payload, "interval_seconds", self.auto_interval_seconds))
        with self.lock:
            self._apply_global_preferences(payload)
            self._apply_runtime_settings(payload)
            if self.auto_running:
                self.auto_interval_seconds = interval
                return self._state_unlocked()
            self.auto_running = True
            self.auto_interval_seconds = interval
            self.auto_last_error = None
            self._auto_stop.clear()

        self._auto_thread = threading.Thread(target=self._auto_loop, daemon=True)
        self._auto_thread.start()
        return self.state()

    def stop_auto(self) -> dict[str, Any]:
        with self.lock:
            self.auto_running = False
            self._auto_stop.set()
        return self.state()

    def _auto_loop(self) -> None:
        while not self._auto_stop.is_set():
            try:
                self.run_once()
                with self.lock:
                    self.auto_last_run_at = time.time()
                    self.auto_last_error = None
            except Exception as exc:
                with self.lock:
                    self.auto_last_error = str(exc)
            wait_for = self.auto_interval_seconds
            if self._auto_stop.wait(wait_for):
                break
        with self.lock:
            self.auto_running = False

    def _load_snapshots(self) -> list[MarketSnapshot]:
        snapshots: list[MarketSnapshot] = []
        histories: dict[str, list[Bar]] = {}
        training_histories: dict[str, list[Bar]] = {}
        for symbol in self.settings.symbols:
            try:
                bars = self.market_data.history(symbol)
                snapshot = _snapshot_from_bars(symbol, bars)
                histories[snapshot.symbol] = bars[-60:]
                snapshots.append(snapshot)
            except Exception as exc:
                self.errors.append(f"{symbol}: {exc}")
        if not snapshots:
            raise RuntimeError("No valid market snapshots loaded.")
        training_histories, training_errors = self._load_training_histories(self.market_data)
        self.errors.extend(training_errors[:5])
        if not training_histories:
            training_histories = {
                snapshot.symbol: self.market_data.history(snapshot.symbol)
                for snapshot in snapshots
            }
        self.histories = histories
        self.training_histories = training_histories
        return snapshots

    def _snapshot_for_symbol(self, symbol: str) -> MarketSnapshot:
        for snapshot in self.snapshots:
            if snapshot.symbol.upper() == symbol:
                return snapshot
        bars = self.market_data.history(symbol)
        snapshot = _snapshot_from_bars(symbol, bars)
        self.histories[snapshot.symbol] = bars[-60:]
        self.training_histories[snapshot.symbol] = bars
        self.snapshots = [item for item in self.snapshots if item.symbol != snapshot.symbol] + [snapshot]
        return snapshot

    def _state_unlocked(self) -> dict[str, Any]:
        account = self.broker.get_account()
        positions = [
            {
                **asdict(position),
                "market_price": self.broker.last_prices.get(symbol, position.average_price),
                "market_value": position.quantity * self.broker.last_prices.get(symbol, position.average_price),
                "pnl_pct": (
                    self.broker.last_prices.get(symbol, position.average_price) - position.average_price
                )
                / position.average_price
                if position.average_price
                else 0.0,
            }
            for symbol, position in self.broker.positions.items()
        ]
        return {
            "cycle": self.cycle,
            "config": {
                "symbols": ",".join(self.settings.symbols),
                "training_symbols": ",".join(self.training_symbols),
                "training_pool_mode": self.training_pool_mode,
                "training_regime_filter": self.training_regime_filter,
                "training_volatility_filter": self.training_volatility_filter,
                "year_regime_overrides": self.year_regime_overrides,
                "ml_profile_overrides": self.ml_profile_overrides,
                "strategy_auto_select": self.strategy_auto_select,
                "starting_cash": self.settings.starting_cash,
                "max_trade_cash": self.settings.max_trade_cash,
                "max_order_shares": self.settings.max_order_shares,
                "max_position_pct": self.settings.max_position_pct,
                "max_daily_loss_pct": self.settings.max_daily_loss_pct,
                "stop_loss_pct": self.settings.stop_loss_pct,
                "take_profit_pct": self.settings.take_profit_pct,
                "market_data": self.settings.market_data,
                "ai_provider": self.settings.ai_provider,
                "strategy_profile": self.strategy_key,
                "local_strategy_profile": self.local_strategy_key,
                "ml_strategy_profile": self.ml_strategy_key,
                "ai_strategy_profile": self.ai_strategy_key,
                "local_buy_score": self._local_buy_score_for(self.strategy_key) or "",
                "max_buy_signals": self._max_buy_signals_for(self.strategy_key) or "",
                **self._profile_control_config(),
                "run_engine": self.run_engine,
                "ml_model": self.ml_model,
                "ml_training_days": self.ml_training_days,
                "backtest_interval_minutes": self.backtest_interval_minutes,
                "ml_regime_filter": self.ml_params.get("ml_regime_filter", self.training_regime_filter),
                "ml_volatility_filter": self.ml_params.get("ml_volatility_filter", self.training_volatility_filter),
                "rf_strategy_profile": self.rf_strategy_key,
                "rf_training_days": self.rf_training_days,
                "rf_target": self.rf_params.get("ml_target", "binary"),
                "rf_training_mode": self.rf_params.get("ml_training_mode", "self"),
                "rf_buy_threshold": self.rf_params.get("ml_buy_threshold", ""),
                "rf_sell_threshold": self.rf_params.get("ml_sell_threshold", ""),
                "rf_min_samples": self.rf_params.get("ml_min_samples", 80),
                "hgb_strategy_profile": self.hgb_strategy_key,
                "hgb_training_days": self.hgb_training_days,
                "hgb_target": self.hgb_params.get("ml_target", "binary"),
                "hgb_training_mode": self.hgb_params.get("ml_training_mode", "self"),
                "hgb_buy_threshold": self.hgb_params.get("ml_buy_threshold", ""),
                "hgb_sell_threshold": self.hgb_params.get("ml_sell_threshold", ""),
                "hgb_min_samples": self.hgb_params.get("ml_min_samples", 80),
                "xgb_strategy_profile": self.xgb_strategy_key,
                "xgb_training_days": self.xgb_training_days,
                "xgb_target": self.xgb_params.get("ml_target", "binary"),
                "xgb_training_mode": self.xgb_params.get("ml_training_mode", "self"),
                "xgb_buy_threshold": self.xgb_params.get("ml_buy_threshold", ""),
                "xgb_sell_threshold": self.xgb_params.get("ml_sell_threshold", ""),
                "xgb_min_samples": self.xgb_params.get("ml_min_samples", 80),
                "mlp_strategy_profile": self.mlp_strategy_key,
                "mlp_training_days": self.mlp_training_days,
                "mlp_target": self.mlp_params.get("ml_target", "binary"),
                "mlp_training_mode": self.mlp_params.get("ml_training_mode", "self"),
                "mlp_buy_threshold": self.mlp_params.get("ml_buy_threshold", ""),
                "mlp_sell_threshold": self.mlp_params.get("ml_sell_threshold", ""),
                "mlp_min_samples": self.mlp_params.get("ml_min_samples", 120),
                "cnn_strategy_profile": self.cnn_strategy_key,
                "cnn_training_days": self.cnn_training_days,
                "cnn_target": self.cnn_params.get("ml_target", "binary"),
                "cnn_training_mode": self.cnn_params.get("ml_training_mode", "self"),
                "cnn_buy_threshold": self.cnn_params.get("ml_buy_threshold", ""),
                "cnn_sell_threshold": self.cnn_params.get("ml_sell_threshold", ""),
                "cnn_min_samples": self.cnn_params.get("ml_min_samples", 140),
                **self.ml_params,
                "xgb_n_estimators": self.xgb_params.get("xgb_n_estimators", 120),
                "xgb_max_depth": self.xgb_params.get("xgb_max_depth", 3),
                "xgb_learning_rate": self.xgb_params.get("xgb_learning_rate", 0.035),
                "xgb_subsample": self.xgb_params.get("xgb_subsample", 0.85),
                "xgb_colsample_bytree": self.xgb_params.get("xgb_colsample_bytree", 0.85),
                "xgb_min_child_weight": self.xgb_params.get("xgb_min_child_weight", 5.0),
                "xgb_reg_lambda": self.xgb_params.get("xgb_reg_lambda", 1.5),
                "xgb_sample_weight": self.xgb_params.get("xgb_sample_weight", True),
                "mlp_layer_size": self.mlp_params.get("mlp_layer_size", 64),
                "mlp_layer_count": self.mlp_params.get("mlp_layer_count", 2),
                "mlp_max_iter": self.mlp_params.get("mlp_max_iter", 180),
                "mlp_learning_rate": self.mlp_params.get("mlp_learning_rate", 0.001),
                "mlp_alpha": self.mlp_params.get("mlp_alpha", 0.001),
                "mlp_early_stopping": self.mlp_params.get("mlp_early_stopping", True),
                "cnn_window": self.cnn_params.get("cnn_window", 20),
                "cnn_channels": self.cnn_params.get("cnn_channels", 32),
                "cnn_kernel_size": self.cnn_params.get("cnn_kernel_size", 3),
                "cnn_layers": self.cnn_params.get("cnn_layers", 2),
                "cnn_max_iter": self.cnn_params.get("cnn_max_iter", 160),
                "cnn_learning_rate": self.cnn_params.get("cnn_learning_rate", 0.001),
                "cnn_alpha": self.cnn_params.get("cnn_alpha", 0.001),
                "cnn_dropout": self.cnn_params.get("cnn_dropout", 0.08),
                "cnn_early_stopping": self.cnn_params.get("cnn_early_stopping", True),
                "cnn_sample_weight": self.cnn_params.get("cnn_sample_weight", True),
            },
            "account": asdict(account),
            "snapshots": [asdict(item) for item in self.snapshots],
            "histories": {
                symbol: [
                    {
                        "timestamp": bar.timestamp,
                        "close": bar.close,
                        "volume": bar.volume,
                    }
                    for bar in bars
                ]
                for symbol, bars in self.histories.items()
            },
            "signals": [{**asdict(item), "side": item.side.value} for item in self.signals],
            "orders": [{**asdict(item), "side": item.side.value} for item in self.orders],
            "positions": positions,
            "events": self.events,
            "backtest": self.backtest,
            "auto_learn": {
                "active": self._active_auto_learn_task_unlocked(),
                "latest": self.latest_auto_learn,
            },
            "latest_auto_learn": self.latest_auto_learn,
            "year_regime_labels": self._year_regime_label_rows(),
            "errors": self.errors,
            "auto": {
                "running": self.auto_running,
                "interval_seconds": self.auto_interval_seconds,
                "last_run_at": self.auto_last_run_at,
                "last_error": self.auto_last_error,
            },
        }

    def _restore_latest_state(self) -> None:
        state = self.storage.load_latest_state()
        if not state:
            return
        account = state.get("account", {})
        self.broker.cash = float(account.get("cash", self.settings.starting_cash))
        self.broker.day_start_equity = float(account.get("day_start_equity", self.settings.starting_cash))
        self.broker.positions = {
            item["symbol"]: Position(
                symbol=item["symbol"],
                quantity=int(item["quantity"]),
                average_price=float(item["average_price"]),
            )
            for item in state.get("positions", [])
        }
        self.broker.last_prices = {
            item["symbol"]: float(item.get("market_price", item.get("average_price", 0.0)))
            for item in state.get("positions", [])
        }
        for item in state.get("snapshots", []):
            self.broker.last_prices[item["symbol"]] = float(item["last_price"])
        self.cycle = int(state.get("cycle", 0))
        self.snapshots = [
            MarketSnapshot(
                symbol=item["symbol"],
                last_price=float(item["last_price"]),
                previous_close=float(item["previous_close"]),
                change_pct=float(item["change_pct"]),
                sma_5=float(item["sma_5"]),
                sma_20=float(item["sma_20"]),
                volume=int(item["volume"]),
                momentum_5=float(item.get("momentum_5", 0.0)),
                momentum_20=float(item.get("momentum_20", 0.0)),
                rsi_14=float(item.get("rsi_14", 50.0)),
                volatility_20=float(item.get("volatility_20", 0.0)),
                volume_ratio_20=float(item.get("volume_ratio_20", 1.0)),
            )
            for item in state.get("snapshots", [])
        ]
        self.signals = [
            Signal(
                symbol=item["symbol"],
                side=Side(item["side"]),
                confidence=float(item["confidence"]),
                reason=item["reason"],
            )
            for item in state.get("signals", [])
        ]
        self.orders = [
            Order(
                symbol=item["symbol"],
                side=Side(item["side"]),
                quantity=int(item["quantity"]),
                limit_price=float(item["limit_price"]),
                reason=item["reason"],
            )
            for item in state.get("orders", [])
        ]
        self.histories = {
            symbol: [
                Bar(
                    symbol=symbol,
                    timestamp=int(item["timestamp"]),
                    close=float(item["close"]),
                    volume=int(item["volume"]),
                )
                for item in bars
            ]
            for symbol, bars in state.get("histories", {}).items()
        }
        self.events = list(state.get("events", []))[:50]
        self.backtest = state.get("backtest")
        self.latest_auto_learn = state.get("latest_auto_learn") or (state.get("auto_learn") or {}).get("latest")
        self.errors = list(state.get("errors", []))
        config = state.get("config", {})
        self._apply_global_preferences(config)

    def _local_buy_score_for(self, strategy: str | None) -> int | None:
        key = strategy_profile(strategy).key
        return self.local_buy_scores.get(key)

    def _max_buy_signals_for(self, strategy: str | None) -> int | None:
        key = strategy_profile(strategy).key
        return self.max_buy_signals_by_strategy.get(key)

    def _profile_control_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {}
        for key in STRATEGY_KEYS:
            buy_score = self.local_buy_scores.get(key)
            max_buy_signals = self.max_buy_signals_by_strategy.get(key)
            config[f"{key}_buy_score"] = buy_score if buy_score is not None else ""
            config[f"{key}_max_buy_signals"] = max_buy_signals if max_buy_signals is not None else ""
        return config

    def _year_regime_label_rows(self) -> list[dict[str, Any]]:
        histories: dict[str, list[Bar]] = {}
        histories.update(self.training_histories)
        rows: list[dict[str, Any]] = []
        for symbol in sorted(histories):
            by_year: dict[int, list[Bar]] = {}
            for bar in histories[symbol]:
                by_year.setdefault(dt.datetime.fromtimestamp(bar.timestamp).year, []).append(bar)
            for year in sorted(by_year):
                metrics = classify_year_regime_bars(by_year[year])
                if int(metrics["bar_count"]) < 20:
                    continue
                key = f"{symbol.upper()}:{year}"
                auto_regime = str(metrics["regime"])
                manual_regime = self.year_regime_overrides.get(key)
                rows.append(
                    {
                        "key": key,
                        "symbol": symbol,
                        "year": year,
                        "auto_regime": auto_regime,
                        "regime": manual_regime or auto_regime,
                        "manual": manual_regime is not None,
                        "annual_return": metrics["annual_return"],
                        "max_drawdown": metrics["max_drawdown"],
                        "volatility": metrics["volatility"],
                        "close_vs_ma": metrics["close_vs_ma"],
                        "bar_count": metrics["bar_count"],
                        "curve": self._compact_year_curve(by_year[year]),
                    }
                )
        return rows

    def _compact_year_curve(self, bars: list[Bar], max_points: int = 36) -> list[dict[str, Any]]:
        valid = [bar for bar in bars if bar.close > 0]
        if not valid:
            return []
        if len(valid) <= max_points:
            selected = valid
        else:
            selected = []
            last_index = len(valid) - 1
            for index in range(max_points):
                selected.append(valid[round(index * last_index / (max_points - 1))])
        return [
            {
                "timestamp": bar.timestamp,
                "close": bar.close,
            }
            for bar in selected
        ]

    def _max_training_days(self) -> int:
        return max(
            int(getattr(self, "ml_training_days", 1095)),
            int(getattr(self, "rf_training_days", 1095)),
            int(getattr(self, "hgb_training_days", 1095)),
            int(getattr(self, "xgb_training_days", 1095)),
            int(getattr(self, "mlp_training_days", 365)),
            int(getattr(self, "cnn_training_days", 1095)),
        )

    def _build_market_data(self, settings: Settings, lookback_days: int | None = None) -> MarketData:
        lookback_days = lookback_days or max(420, int(getattr(self, "ml_training_days", 1095)) + 420)
        providers: list[tuple[str, MarketData]] = []
        for name in [item.strip().lower() for item in settings.market_data.split(",") if item.strip()]:
            if name == "akshare":
                providers.append((name, AkShareMarketData(lookback_days=lookback_days)))
            elif name == "tushare":
                if not settings.tushare_token:
                    continue
                providers.append((name, TushareMarketData(settings.tushare_token, lookback_days=lookback_days)))
            elif name == "yahoo":
                providers.append((name, YahooMarketData(daily_range=self._yahoo_range_for_days(lookback_days))))
        if not providers:
            providers.append(("yahoo", YahooMarketData(daily_range=self._yahoo_range_for_days(lookback_days))))
        return FallbackMarketData(providers)

    def _market_data_for_backtest(self, annual_year: int | None = None) -> MarketData:
        if annual_year is None:
            return self.market_data
        today = dt.date.today()
        test_start = dt.date(annual_year, 1, 1)
        days_to_test_start = max(0, (today - test_start).days)
        lookback_days = max(420, days_to_test_start + int(getattr(self, "ml_training_days", 1095)) + 80)
        return self._build_market_data(self.settings, lookback_days=lookback_days)

    def _backtest_year_from_payload(self, payload: dict[str, Any], mode: str) -> int | None:
        raw = payload.get("backtest_year")
        if raw is None and mode.startswith("year_"):
            raw = mode.removeprefix("year_")
        if raw is None and mode.isdigit():
            raw = mode
        try:
            year = int(raw)
        except (TypeError, ValueError):
            return None
        return year if 2021 <= year <= 2025 else None

    def _build_ai(
        self,
        settings: Settings,
        strategy: str | None = None,
        max_buy_signals: int | None = None,
    ) -> SignalProvider:
        if settings.ai_provider == "openai" and settings.openai_api_key:
            return OpenAISignalProvider(settings.openai_api_key, settings.openai_model)
        if settings.ai_provider == "codex" and settings.codex_bin:
            return CodexSignalProvider(
                settings.codex_bin,
                settings.codex_model,
                settings.codex_timeout_seconds,
                strategy,
                max_buy_signals,
            )
        return HeuristicSignalProvider(strategy, max_buys_per_day=max_buy_signals)

    def _build_signal_provider(
        self,
        settings: Settings,
        strategy: str | None,
        engine: str,
        ml_model: str = "random_forest",
        ml_params: dict[str, Any] | None = None,
        local_buy_score: int | None = None,
        max_buy_signals: int | None = None,
    ) -> SignalProvider:
        if engine in {"local", "heuristic"}:
            return HeuristicSignalProvider(
                strategy,
                buy_score=local_buy_score,
                max_buys_per_day=max_buy_signals,
                auto_strategy=self.strategy_auto_select == "on",
            )
        if engine in {"ml", "machine_learning", "random_forest", "hist_gradient_boosting", "xgboost", "mlp", "neural_network", "cnn", "cnn1d", "1d_cnn"}:
            return MachineLearningSignalProvider(
                strategy,
                ml_model,
                params=ml_params,
                max_buys_per_day=max_buy_signals,
                auto_strategy=self.strategy_auto_select == "on",
            )
        return self._build_ai(settings, strategy, max_buy_signals)

    def _prepare_signal_provider(self, provider: SignalProvider, histories: dict[str, list[Bar]]) -> None:
        if isinstance(provider, MachineLearningSignalProvider):
            provider.set_histories(histories)

    def _prepare_decision_histories(self, provider: SignalProvider, histories: dict[str, list[Bar]]) -> None:
        if isinstance(provider, MachineLearningSignalProvider):
            provider.set_decision_histories(histories)

    def _training_symbol_universe(self) -> list[str]:
        active_training_mode = self._normalize_ml_training_mode(self.ml_params.get("ml_training_mode", "self"))
        if active_training_mode in {"self", "classified", "mixed"}:
            symbols = list(self.training_symbols) + list(self.settings.symbols)
        elif self.training_pool_mode == "trade_only":
            symbols = list(self.settings.symbols)
        elif self.training_pool_mode == "training_plus_trade":
            symbols = list(self.training_symbols) + list(self.settings.symbols)
        else:
            symbols = list(self.training_symbols)
        seen: set[str] = set()
        unique: list[str] = []
        for symbol in symbols:
            normalized = symbol.strip().upper()
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique.append(normalized)
        return unique

    def _load_training_histories(
        self,
        market_data: MarketData,
        cutoff_timestamp: int | None = None,
        bar_limit: int | None = None,
        symbols: list[str] | tuple[str, ...] | None = None,
    ) -> tuple[dict[str, list[Bar]], list[str]]:
        histories: dict[str, list[Bar]] = {}
        errors: list[str] = []
        source_symbols = list(symbols) if symbols is not None else self._training_symbol_universe()
        for symbol in source_symbols:
            try:
                bars = market_data.history(symbol)
                if cutoff_timestamp is not None:
                    bars = [bar for bar in bars if bar.timestamp < cutoff_timestamp]
                if bar_limit is not None:
                    bars = bars[-bar_limit:]
                normalized = _snapshot_from_bars(symbol, bars).symbol
                histories[normalized] = bars
            except Exception as exc:
                errors.append(f"训练池 {symbol}: {exc}")
        return histories, errors

    def _limit_buy_signals(self, signals: list[Signal], strategy: str | None) -> list[Signal]:
        profile = strategy_profile_with_overrides(strategy, max_buys_per_day=self._max_buy_signals_for(strategy))
        buy_candidates = [(signal.confidence, index) for index, signal in enumerate(signals) if signal.side == Side.BUY]
        if len(buy_candidates) <= profile.max_buys_per_day:
            return signals
        keep = {
            index
            for _, index in sorted(buy_candidates, key=lambda item: item[0], reverse=True)[
                : profile.max_buys_per_day
            ]
        }
        return [
            signal
            if index in keep or signal.side != Side.BUY
            else Signal(signal.symbol, Side.HOLD, 0.55, f"Buy signal limit: stronger candidates exist. Original reason: {signal.reason}")
            for index, signal in enumerate(signals)
        ]

    def _apply_runtime_settings(self, payload: dict[str, Any]) -> None:
        if not payload:
            return
        symbols_raw = str(payload.get("symbols", ",".join(self.settings.symbols)))
        symbols = [item.strip().upper() for item in symbols_raw.split(",") if item.strip()]
        if not symbols:
            symbols = self.settings.symbols

        next_settings = replace(
            self.base_settings,
            symbols=symbols,
            starting_cash=self._float(payload, "starting_cash", self.settings.starting_cash),
            max_trade_cash=self._float(payload, "max_trade_cash", self.settings.max_trade_cash),
            max_order_shares=self._int(payload, "max_order_shares", self.settings.max_order_shares),
            max_position_pct=self._pct(payload, "max_position_pct", self.settings.max_position_pct),
            max_daily_loss_pct=self._pct(payload, "max_daily_loss_pct", self.settings.max_daily_loss_pct),
            stop_loss_pct=self._pct(payload, "stop_loss_pct", self.settings.stop_loss_pct),
            take_profit_pct=self._pct(payload, "take_profit_pct", self.settings.take_profit_pct),
        )
        market_changed = next_settings.symbols != self.settings.symbols or next_settings.market_data != self.settings.market_data
        self.settings = next_settings
        if market_changed:
            self.market_data = self._build_market_data(self.settings)
        self.risk = self._build_risk(self.settings, self.strategy_key)

    def _ai_engine_name(self, settings: Settings, strategy: str | None = None) -> str:
        profile = strategy_profile(strategy)
        if settings.ai_provider == "openai" and settings.openai_api_key:
            return f"openai:{settings.openai_model}:{profile.key}"
        if settings.ai_provider == "codex" and settings.codex_bin:
            return f"codex-login-guarded-{profile.key}"
        return f"heuristic-{profile.key}"

    def _build_risk(self, settings: Settings, strategy: str | None = None) -> RiskManager:
        profile = strategy_profile(strategy)
        return RiskManager(
            settings.max_position_pct,
            settings.max_trade_cash,
            settings.max_order_shares,
            settings.max_daily_loss_pct,
            settings.stop_loss_pct,
            settings.take_profit_pct,
            profile.allow_add_position,
        )

    def _apply_global_preferences(self, payload: dict[str, Any]) -> None:
        raw_engine = str(payload.get("run_engine", payload.get("engine", self.run_engine))).strip().lower()
        raw_engine_normalized = raw_engine.replace("-", "_")
        if raw_engine_normalized in {"local", "heuristic"}:
            engine = "local"
        elif raw_engine_normalized in {"ml", "machine_learning", "random_forest", "hist_gradient_boosting", "xgboost", "mlp", "neural_network", "cnn", "cnn1d", "1d_cnn"}:
            engine = "ml"
        else:
            engine = "ai"
        local_strategy = strategy_profile(str(payload.get("local_strategy_profile", self.local_strategy_key))).key
        ml_strategy = strategy_profile(str(payload.get("ml_strategy_profile", self.ml_strategy_key))).key
        ai_strategy = strategy_profile(str(payload.get("ai_strategy_profile", self.ai_strategy_key))).key
        has_mode_strategy = any(
            key in payload for key in ("local_strategy_profile", "ml_strategy_profile", "ai_strategy_profile")
        )
        if not has_mode_strategy:
            raw_strategy = payload.get("strategy_profile", payload.get("strategy"))
            if raw_strategy is not None:
                compat_strategy = strategy_profile(str(raw_strategy)).key
                if engine == "ml":
                    ml_strategy = compat_strategy
                elif engine == "ai":
                    ai_strategy = compat_strategy
                else:
                    local_strategy = compat_strategy
        default_ml_model = raw_engine_normalized if raw_engine_normalized in {"random_forest", "hist_gradient_boosting", "xgboost", "mlp", "neural_network", "cnn", "cnn1d", "1d_cnn"} else self.ml_model
        ml_model = self._normalize_ml_model(payload.get("ml_model", default_ml_model))
        rf_strategy = strategy_profile(str(payload.get("rf_strategy_profile", self.rf_strategy_key))).key
        hgb_strategy = strategy_profile(str(payload.get("hgb_strategy_profile", self.hgb_strategy_key))).key
        xgb_strategy = strategy_profile(str(payload.get("xgb_strategy_profile", self.xgb_strategy_key))).key
        mlp_strategy = strategy_profile(str(payload.get("mlp_strategy_profile", self.mlp_strategy_key))).key
        cnn_strategy = strategy_profile(str(payload.get("cnn_strategy_profile", self.cnn_strategy_key))).key
        if "rf_strategy_profile" not in payload and ml_model == "random_forest":
            rf_strategy = ml_strategy
        if "hgb_strategy_profile" not in payload and ml_model == "hist_gradient_boosting":
            hgb_strategy = ml_strategy
        if "xgb_strategy_profile" not in payload and ml_model == "xgboost":
            xgb_strategy = ml_strategy
        if "mlp_strategy_profile" not in payload and ml_model == "mlp":
            mlp_strategy = ml_strategy
        if "cnn_strategy_profile" not in payload and ml_model == "cnn":
            cnn_strategy = ml_strategy
        ml_profile_overrides = self._normalize_ml_profile_overrides(
            payload.get("ml_profile_overrides", self.ml_profile_overrides)
        )
        old_profile_overrides = self.ml_profile_overrides
        self.ml_profile_overrides = ml_profile_overrides
        rf_params = self._model_ml_params_from_payload(payload, "random_forest", rf_strategy, self.rf_params)
        hgb_params = self._model_ml_params_from_payload(payload, "hist_gradient_boosting", hgb_strategy, self.hgb_params)
        xgb_params = self._model_ml_params_from_payload(payload, "xgboost", xgb_strategy, self.xgb_params)
        mlp_params = self._model_ml_params_from_payload(payload, "mlp", mlp_strategy, self.mlp_params)
        cnn_params = self._model_ml_params_from_payload(payload, "cnn", cnn_strategy, self.cnn_params)
        rf_training_days = self._bounded_int(
            payload,
            "rf_training_days",
            self._bounded_int(payload, "ml_training_days", self.rf_training_days, 90, 3650)
            if ml_model == "random_forest"
            else self.rf_training_days,
            90,
            3650,
        )
        hgb_training_days = self._bounded_int(
            payload,
            "hgb_training_days",
            self._bounded_int(payload, "ml_training_days", self.hgb_training_days, 90, 3650)
            if ml_model == "hist_gradient_boosting"
            else self.hgb_training_days,
            90,
            3650,
        )
        xgb_training_days = self._bounded_int(
            payload,
            "xgb_training_days",
            self._bounded_int(payload, "ml_training_days", self.xgb_training_days, 90, 3650)
            if ml_model == "xgboost"
            else self.xgb_training_days,
            90,
            3650,
        )
        mlp_training_days = self._bounded_int(
            payload,
            "mlp_training_days",
            self._bounded_int(payload, "ml_training_days", self.mlp_training_days, 90, 3650)
            if ml_model == "mlp"
            else self.mlp_training_days,
            90,
            3650,
        )
        cnn_training_days = self._bounded_int(
            payload,
            "cnn_training_days",
            self._bounded_int(payload, "ml_training_days", self.cnn_training_days, 90, 3650)
            if ml_model == "cnn"
            else self.cnn_training_days,
            90,
            3650,
        )
        if ml_model == "xgboost":
            ml_strategy = xgb_strategy
            ml_params = xgb_params
            ml_training_days = xgb_training_days
        elif ml_model == "mlp":
            ml_strategy = mlp_strategy
            ml_params = mlp_params
            ml_training_days = mlp_training_days
        elif ml_model == "cnn":
            ml_strategy = cnn_strategy
            ml_params = cnn_params
            ml_training_days = cnn_training_days
        elif ml_model == "hist_gradient_boosting":
            ml_strategy = hgb_strategy
            ml_params = hgb_params
            ml_training_days = hgb_training_days
        else:
            ml_strategy = rf_strategy
            ml_params = rf_params
            ml_training_days = rf_training_days
        active_strategy = {
            "local": local_strategy,
            "ml": ml_strategy,
            "ai": ai_strategy,
        }[engine]
        local_buy_scores = self._profile_optional_ints(
            payload,
            "buy_score",
            self.local_buy_scores,
            1,
            20,
            legacy_key="local_buy_score",
            legacy_strategy=local_strategy,
        )
        max_buy_signals_by_strategy = self._profile_optional_ints(
            payload,
            "max_buy_signals",
            self.max_buy_signals_by_strategy,
            1,
            20,
            legacy_key="max_buy_signals",
            legacy_strategy=active_strategy,
        )
        training_symbols = self._symbols_from_payload(
            payload,
            "training_symbols",
            self.training_symbols or list(DEFAULT_TRAINING_SYMBOLS),
        )
        training_pool_mode = "training_plus_trade"
        year_regime_overrides = self._normalize_year_regime_overrides(
            payload.get("year_regime_overrides", self.year_regime_overrides)
        )
        strategy_auto_select = self._normalize_toggle_text(
            payload.get("strategy_auto_select", self.strategy_auto_select)
        )
        for params in (rf_params, hgb_params, xgb_params, mlp_params, cnn_params):
            regime_filter, volatility_filter = self._training_filters_for_mode(
                str(params.get("ml_training_mode", "self"))
            )
            params["ml_regime_filter"] = regime_filter
            params["ml_volatility_filter"] = volatility_filter
            params["ml_year_regime_overrides"] = year_regime_overrides
        training_regime_filter, training_volatility_filter = self._training_filters_for_mode(
            str(ml_params.get("ml_training_mode", "self"))
        )
        backtest_interval_minutes = self._bounded_interval_minutes(
            payload.get("backtest_interval_minutes", self.backtest_interval_minutes)
        )
        changed = (
            active_strategy != self.strategy_key
            or local_strategy != self.local_strategy_key
            or ml_strategy != self.ml_strategy_key
            or ai_strategy != self.ai_strategy_key
            or rf_strategy != self.rf_strategy_key
            or hgb_strategy != self.hgb_strategy_key
            or xgb_strategy != self.xgb_strategy_key
            or mlp_strategy != self.mlp_strategy_key
            or cnn_strategy != self.cnn_strategy_key
            or engine != self.run_engine
            or ml_model != self.ml_model
            or ml_params != self.ml_params
            or rf_params != self.rf_params
            or hgb_params != self.hgb_params
            or xgb_params != self.xgb_params
            or mlp_params != self.mlp_params
            or cnn_params != self.cnn_params
            or ml_training_days != self.ml_training_days
            or rf_training_days != self.rf_training_days
            or hgb_training_days != self.hgb_training_days
            or xgb_training_days != self.xgb_training_days
            or mlp_training_days != self.mlp_training_days
            or cnn_training_days != self.cnn_training_days
            or backtest_interval_minutes != self.backtest_interval_minutes
            or local_buy_scores != self.local_buy_scores
            or max_buy_signals_by_strategy != self.max_buy_signals_by_strategy
            or training_symbols != self.training_symbols
            or training_pool_mode != self.training_pool_mode
            or training_regime_filter != self.training_regime_filter
            or training_volatility_filter != self.training_volatility_filter
            or year_regime_overrides != self.year_regime_overrides
            or ml_profile_overrides != old_profile_overrides
            or strategy_auto_select != self.strategy_auto_select
        )
        self.strategy_key = active_strategy
        self.local_strategy_key = local_strategy
        self.ml_strategy_key = ml_strategy
        self.ai_strategy_key = ai_strategy
        self.rf_strategy_key = rf_strategy
        self.hgb_strategy_key = hgb_strategy
        self.xgb_strategy_key = xgb_strategy
        self.mlp_strategy_key = mlp_strategy
        self.cnn_strategy_key = cnn_strategy
        self.run_engine = engine
        self.ml_model = ml_model
        self.ml_params = ml_params
        self.rf_params = rf_params
        self.hgb_params = hgb_params
        self.xgb_params = xgb_params
        self.mlp_params = mlp_params
        self.cnn_params = cnn_params
        old_training_days = self.ml_training_days
        self.ml_training_days = ml_training_days
        self.rf_training_days = rf_training_days
        self.hgb_training_days = hgb_training_days
        self.xgb_training_days = xgb_training_days
        self.mlp_training_days = mlp_training_days
        self.cnn_training_days = cnn_training_days
        self.backtest_interval_minutes = backtest_interval_minutes
        self.local_buy_scores = local_buy_scores
        self.max_buy_signals_by_strategy = max_buy_signals_by_strategy
        self.training_symbols = training_symbols
        self.training_pool_mode = training_pool_mode
        self.training_regime_filter = training_regime_filter
        self.training_volatility_filter = training_volatility_filter
        self.year_regime_overrides = year_regime_overrides
        self.ml_profile_overrides = ml_profile_overrides
        self.strategy_auto_select = strategy_auto_select
        if old_training_days != self.ml_training_days:
            self.market_data = self._build_market_data(self.settings)
        if changed:
            self.ai = self._build_signal_provider(
                self.settings,
                self.strategy_key,
                self.run_engine,
                self.ml_model,
                self.ml_params,
                self._local_buy_score_for(self.strategy_key),
                self._max_buy_signals_for(self.strategy_key),
            )
            self.risk = self._build_risk(self.settings, self.strategy_key)

    def _normalize_ml_model(self, model_name: Any) -> str:
        raw = str(model_name or "").strip().lower().replace("-", "_")
        if raw in {"mlp", "neural_network", "neuralnetwork", "nn", "mlpclassifier"}:
            return "mlp"
        if raw in {"cnn", "cnn1d", "1d_cnn", "conv1d", "one_d_cnn", "onedcnn"}:
            return "cnn"
        if raw in {"hist_gradient_boosting", "histgradientboosting", "hgb", "boosting"}:
            return "hist_gradient_boosting"
        if raw in {"xgboost", "xgb", "xgbclassifier"}:
            return "xgboost"
        return "random_forest"

    def _normalize_ml_training_mode(self, mode: Any) -> str:
        raw = str(mode or "").strip().lower().replace("-", "_")
        if raw in {"self", "self_first", "single_first", "single_auto", "own", "own_first"}:
            return "self"
        if raw in {"classified", "regime", "regime_classified", "by_regime", "year_regime"}:
            return "classified"
        if raw in {"mixed", "all", "all_mixed", "general_all"}:
            return "mixed"
        if raw in {"single", "single_symbol", "per_symbol", "symbol"}:
            return "self"
        if raw in {"auto", "adaptive"}:
            return "self"
        return "mixed"

    def _normalize_ml_target(self, target: Any) -> str:
        raw = str(target or "").strip().lower().replace("-", "_")
        if raw in {"trend5", "five_class", "five_classes", "multiclass", "multi_class", "probability", "trend_probability"}:
            return "trend5"
        return "binary"

    def _normalize_torch_device(self, value: Any) -> str:
        raw = str(value or "auto").strip().lower().replace("-", "_")
        if raw in {"cpu", "cuda", "gpu"}:
            return "cuda" if raw == "gpu" else raw
        return "auto"

    def _normalize_training_pool_mode(self, mode: Any) -> str:
        raw = str(mode or "").strip().lower().replace("-", "_")
        if raw in {"trade", "trade_only", "current", "symbols"}:
            return "trade_only"
        if raw in {"plus", "merge", "combined", "training_plus_trade", "train_plus_trade"}:
            return "training_plus_trade"
        return "training_only"

    def _training_filters_for_mode(self, mode: str) -> tuple[str, str]:
        normalized = self._normalize_ml_training_mode(mode)
        if normalized == "classified":
            return "on", "off"
        return "off", "off"

    def _normalize_toggle_text(self, value: Any) -> str:
        raw = str(value if value is not None else "on").strip().lower()
        return "off" if raw in {"off", "false", "0", "none", "all"} else "on"

    def _normalize_year_regime_overrides(self, raw: Any) -> dict[str, str]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                raw = {}
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, str] = {}
        for key, value in raw.items():
            regime = str(value).strip().lower()
            if regime not in {"bull", "bear", "sideways"}:
                continue
            parts = str(key).strip().upper().replace("|", ":").split(":")
            if len(parts) != 2:
                continue
            symbol, year = parts
            if not symbol or not year.isdigit():
                continue
            normalized[f"{symbol}:{int(year)}"] = regime
        return normalized

    def _normalize_ml_profile_overrides(self, raw: Any) -> dict[str, dict[str, dict[str, Any]]]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                raw = {}
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, dict[str, dict[str, Any]]] = {}
        for model_raw, profiles_raw in raw.items():
            model = self._normalize_ml_model(model_raw)
            if not isinstance(profiles_raw, dict):
                continue
            for profile_raw, params_raw in profiles_raw.items():
                profile = strategy_profile(str(profile_raw)).key
                if profile == "custom" or not isinstance(params_raw, dict):
                    continue
                cleaned = self._clean_ml_profile_override(model, params_raw)
                if cleaned:
                    normalized.setdefault(model, {})[profile] = cleaned
        return normalized

    def _clean_ml_profile_override(self, model: str, params: dict[str, Any]) -> dict[str, Any]:
        defaults = self._default_ml_params()
        allowed = {
            "ml_buy_threshold",
            "ml_sell_threshold",
            "ml_min_samples",
            "ml_training_mode",
            "ml_target",
        } | set(self._model_specific_candidate_params(model, defaults).keys())
        cleaned: dict[str, Any] = {}
        for key, value in params.items():
            if key not in allowed:
                continue
            if key == "ml_buy_threshold":
                cleaned[key] = self._coerce_optional_float(value, 0.01, 0.99)
            elif key == "ml_sell_threshold":
                cleaned[key] = self._coerce_optional_float(value, 0.01, 0.99)
            elif key == "ml_min_samples":
                cleaned[key] = self._coerce_int(value, defaults["ml_min_samples"], 20, 5000)
            elif key == "ml_training_mode":
                cleaned[key] = self._normalize_ml_training_mode(value)
            elif key == "ml_target":
                cleaned[key] = self._normalize_ml_target(value)
            elif key in {"mlp_device", "cnn_device"}:
                cleaned[key] = self._normalize_torch_device(value)
            elif key.endswith("_early_stopping") or key.endswith("_sample_weight") or key.endswith("_class_weight") or key.endswith("_max_features_sqrt"):
                cleaned[key] = str(value).strip().lower() in {"1", "true", "yes", "on"}
            elif key in {"hgb_learning_rate", "hgb_l2_regularization", "xgb_learning_rate", "xgb_subsample", "xgb_colsample_bytree", "xgb_min_child_weight", "xgb_reg_lambda", "mlp_learning_rate", "mlp_alpha", "cnn_learning_rate", "cnn_alpha", "cnn_dropout"}:
                cleaned[key] = self._coerce_float(value, float(defaults.get(key, 0.0)), 0.0, 50.0)
            else:
                cleaned[key] = self._coerce_int(value, int(defaults.get(key, 1) or 1), 1, 5000)
        return {key: value for key, value in cleaned.items() if value != ""}

    def _coerce_int(self, value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            parsed = default
        return min(max(parsed, minimum), maximum)

    def _coerce_float(self, value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return min(max(parsed, minimum), maximum)

    def _coerce_optional_float(self, value: Any, minimum: float, maximum: float) -> float | str:
        if value is None or str(value).strip() == "":
            return ""
        return round(self._coerce_float(value, minimum, minimum, maximum), 5)

    def _symbols_from_payload(self, payload: dict[str, Any], key: str, default: list[str]) -> list[str]:
        raw = str(payload.get(key, ",".join(default)))
        symbols = [item.strip().upper() for item in raw.split(",") if item.strip()]
        seen: set[str] = set()
        unique: list[str] = []
        for symbol in symbols:
            if symbol not in seen:
                seen.add(symbol)
                unique.append(symbol)
        return unique or list(default)

    def _ml_model_label(self, model_name: str) -> str:
        if model_name == "mlp":
            return "MLP"
        if model_name == "cnn":
            return "1D-CNN"
        if model_name == "xgboost":
            return "XGBoost"
        if model_name == "hist_gradient_boosting":
            return "HistGradientBoosting"
        return "RandomForest"

    def _default_ml_params(self) -> dict[str, Any]:
        return {
            "ml_buy_threshold": "",
            "ml_sell_threshold": "",
            "ml_min_samples": 80,
            "ml_training_mode": "self",
            "ml_target": "binary",
            "ml_regime_filter": "on",
            "ml_volatility_filter": "on",
            "rf_n_estimators": 80,
            "rf_max_depth": 5,
            "rf_min_samples_leaf": 5,
            "rf_max_features_sqrt": True,
            "rf_class_weight": True,
            "rf_sample_weight": True,
            "hgb_max_iter": 140,
            "hgb_learning_rate": 0.03,
            "hgb_max_leaf_nodes": 15,
            "hgb_min_samples_leaf": 12,
            "hgb_l2_regularization": 0.08,
            "hgb_early_stopping": False,
            "hgb_sample_weight": False,
            "xgb_n_estimators": 120,
            "xgb_max_depth": 3,
            "xgb_learning_rate": 0.035,
            "xgb_subsample": 0.85,
            "xgb_colsample_bytree": 0.85,
            "xgb_min_child_weight": 5.0,
            "xgb_reg_lambda": 1.5,
            "xgb_sample_weight": True,
            "mlp_layer_size": 64,
            "mlp_layer_count": 2,
            "mlp_max_iter": 180,
            "mlp_learning_rate": 0.001,
            "mlp_alpha": 0.001,
            "mlp_early_stopping": True,
            "mlp_device": "auto",
            "cnn_window": 20,
            "cnn_channels": 32,
            "cnn_kernel_size": 3,
            "cnn_layers": 2,
            "cnn_max_iter": 160,
            "cnn_learning_rate": 0.001,
            "cnn_alpha": 0.001,
            "cnn_dropout": 0.08,
            "cnn_early_stopping": True,
            "cnn_sample_weight": True,
            "cnn_device": "auto",
        }

    def _model_ml_params_from_payload(
        self,
        payload: dict[str, Any],
        ml_model: str,
        profile_key: str,
        current_params: dict[str, Any],
    ) -> dict[str, Any]:
        if ml_model == "xgboost":
            prefix = "xgb"
        elif ml_model == "cnn":
            prefix = "cnn"
        elif ml_model == "mlp":
            prefix = "mlp"
        elif ml_model == "hist_gradient_boosting":
            prefix = "hgb"
        else:
            prefix = "rf"
        defaults = self._profile_ml_params(profile_key, ml_model)
        selected_training_mode = self._normalize_ml_training_mode(
            payload.get(f"{prefix}_training_mode", current_params.get("ml_training_mode", defaults["ml_training_mode"]))
        )
        selected_target = self._normalize_ml_target(
            payload.get(f"{prefix}_target", current_params.get("ml_target", defaults["ml_target"]))
        )
        if profile_key != "custom":
            defaults["ml_training_mode"] = selected_training_mode
            defaults["ml_target"] = selected_target
            if prefix in {"cnn", "mlp"}:
                defaults[f"{prefix}_device"] = self._normalize_torch_device(
                    payload.get(f"{prefix}_device", current_params.get(f"{prefix}_device", defaults.get(f"{prefix}_device", "auto")))
                )
            return defaults
        current = {**defaults, **current_params}
        return {
            "ml_buy_threshold": self._optional_float(
                payload,
                f"{prefix}_buy_threshold",
                current["ml_buy_threshold"],
                0.01,
                0.99,
            ),
            "ml_sell_threshold": self._optional_float(
                payload,
                f"{prefix}_sell_threshold",
                current["ml_sell_threshold"],
                0.01,
                0.99,
            ),
            "ml_min_samples": self._bounded_int(payload, f"{prefix}_min_samples", current["ml_min_samples"], 20, 5000),
            "ml_training_mode": self._normalize_ml_training_mode(
                payload.get(f"{prefix}_training_mode", current["ml_training_mode"])
            ),
            "ml_target": self._normalize_ml_target(payload.get(f"{prefix}_target", current["ml_target"])),
            "rf_n_estimators": self._bounded_int(payload, "rf_n_estimators", current["rf_n_estimators"], 10, 500),
            "rf_max_depth": self._bounded_int(payload, "rf_max_depth", current["rf_max_depth"], 1, 30),
            "rf_min_samples_leaf": self._bounded_int(payload, "rf_min_samples_leaf", current["rf_min_samples_leaf"], 1, 100),
            "rf_max_features_sqrt": self._bool(payload, "rf_max_features_sqrt", bool(current["rf_max_features_sqrt"])),
            "rf_class_weight": self._bool(payload, "rf_class_weight", bool(current["rf_class_weight"])),
            "rf_sample_weight": self._bool(payload, "rf_sample_weight", bool(current["rf_sample_weight"])),
            "hgb_max_iter": self._bounded_int(payload, "hgb_max_iter", current["hgb_max_iter"], 20, 500),
            "hgb_learning_rate": self._bounded_float(payload, "hgb_learning_rate", current["hgb_learning_rate"], 0.005, 0.5),
            "hgb_max_leaf_nodes": self._bounded_int(payload, "hgb_max_leaf_nodes", current["hgb_max_leaf_nodes"], 4, 63),
            "hgb_min_samples_leaf": self._bounded_int(payload, "hgb_min_samples_leaf", current["hgb_min_samples_leaf"], 2, 100),
            "hgb_l2_regularization": self._bounded_float(
                payload,
                "hgb_l2_regularization",
                current["hgb_l2_regularization"],
                0.0,
                10.0,
            ),
            "hgb_early_stopping": self._bool(payload, "hgb_early_stopping", bool(current["hgb_early_stopping"])),
            "hgb_sample_weight": self._bool(payload, "hgb_sample_weight", bool(current["hgb_sample_weight"])),
            "xgb_n_estimators": self._bounded_int(payload, "xgb_n_estimators", current["xgb_n_estimators"], 20, 500),
            "xgb_max_depth": self._bounded_int(payload, "xgb_max_depth", current["xgb_max_depth"], 1, 12),
            "xgb_learning_rate": self._bounded_float(payload, "xgb_learning_rate", current["xgb_learning_rate"], 0.005, 0.5),
            "xgb_subsample": self._bounded_float(payload, "xgb_subsample", current["xgb_subsample"], 0.3, 1.0),
            "xgb_colsample_bytree": self._bounded_float(
                payload,
                "xgb_colsample_bytree",
                current["xgb_colsample_bytree"],
                0.3,
                1.0,
            ),
            "xgb_min_child_weight": self._bounded_float(
                payload,
                "xgb_min_child_weight",
                current["xgb_min_child_weight"],
                0.1,
                50.0,
            ),
            "xgb_reg_lambda": self._bounded_float(payload, "xgb_reg_lambda", current["xgb_reg_lambda"], 0.0, 20.0),
            "xgb_sample_weight": self._bool(payload, "xgb_sample_weight", bool(current["xgb_sample_weight"])),
            "mlp_layer_size": self._bounded_int(payload, "mlp_layer_size", current["mlp_layer_size"], 8, 512),
            "mlp_layer_count": self._bounded_int(payload, "mlp_layer_count", current["mlp_layer_count"], 1, 4),
            "mlp_max_iter": self._bounded_int(payload, "mlp_max_iter", current["mlp_max_iter"], 20, 1000),
            "mlp_learning_rate": self._bounded_float(payload, "mlp_learning_rate", current["mlp_learning_rate"], 0.0001, 0.1),
            "mlp_alpha": self._bounded_float(payload, "mlp_alpha", current["mlp_alpha"], 0.00001, 1.0),
            "mlp_early_stopping": self._bool(payload, "mlp_early_stopping", bool(current["mlp_early_stopping"])),
            "mlp_device": self._normalize_torch_device(payload.get("mlp_device", current.get("mlp_device", "auto"))),
            "cnn_window": self._bounded_int(payload, "cnn_window", current["cnn_window"], 5, 120),
            "cnn_channels": self._bounded_int(payload, "cnn_channels", current["cnn_channels"], 4, 256),
            "cnn_kernel_size": self._bounded_int(payload, "cnn_kernel_size", current["cnn_kernel_size"], 1, 9),
            "cnn_layers": self._bounded_int(payload, "cnn_layers", current["cnn_layers"], 1, 5),
            "cnn_max_iter": self._bounded_int(payload, "cnn_max_iter", current["cnn_max_iter"], 20, 1000),
            "cnn_learning_rate": self._bounded_float(payload, "cnn_learning_rate", current["cnn_learning_rate"], 0.0001, 0.1),
            "cnn_alpha": self._bounded_float(payload, "cnn_alpha", current["cnn_alpha"], 0.00001, 1.0),
            "cnn_dropout": self._bounded_float(payload, "cnn_dropout", current["cnn_dropout"], 0.0, 0.8),
            "cnn_early_stopping": self._bool(payload, "cnn_early_stopping", bool(current["cnn_early_stopping"])),
            "cnn_sample_weight": self._bool(payload, "cnn_sample_weight", bool(current["cnn_sample_weight"])),
            "cnn_device": self._normalize_torch_device(payload.get("cnn_device", current.get("cnn_device", "auto"))),
        }

    def _ml_params_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_strategy = payload.get(
            "ml_strategy_profile",
            payload.get("strategy_profile", payload.get("strategy", self.ml_strategy_key)),
        )
        profile_key = strategy_profile(str(raw_strategy)).key
        raw_model = payload.get("ml_model", self.ml_model)
        ml_model = self._normalize_ml_model(raw_model)
        defaults = self._profile_ml_params(profile_key, ml_model)
        if profile_key != "custom":
            if ml_model in {"cnn", "mlp"}:
                prefix = self._ml_prefix(ml_model)
                defaults[f"{prefix}_device"] = self._normalize_torch_device(
                    payload.get(f"{prefix}_device", getattr(self, "ml_params", {}).get(f"{prefix}_device", defaults.get(f"{prefix}_device", "auto")))
                )
            return defaults
        current = {**defaults, **getattr(self, "ml_params", {})}
        return {
            "ml_buy_threshold": self._optional_float(payload, "ml_buy_threshold", current["ml_buy_threshold"], 0.01, 0.99),
            "ml_sell_threshold": self._optional_float(payload, "ml_sell_threshold", current["ml_sell_threshold"], 0.01, 0.99),
            "ml_min_samples": self._bounded_int(payload, "ml_min_samples", current["ml_min_samples"], 20, 5000),
            "ml_training_mode": self._normalize_ml_training_mode(payload.get("ml_training_mode", current["ml_training_mode"])),
            "ml_target": self._normalize_ml_target(payload.get("ml_target", current["ml_target"])),
            "rf_n_estimators": self._bounded_int(payload, "rf_n_estimators", current["rf_n_estimators"], 10, 500),
            "rf_max_depth": self._bounded_int(payload, "rf_max_depth", current["rf_max_depth"], 1, 30),
            "rf_min_samples_leaf": self._bounded_int(payload, "rf_min_samples_leaf", current["rf_min_samples_leaf"], 1, 100),
            "rf_max_features_sqrt": self._bool(payload, "rf_max_features_sqrt", bool(current["rf_max_features_sqrt"])),
            "rf_class_weight": self._bool(payload, "rf_class_weight", bool(current["rf_class_weight"])),
            "rf_sample_weight": self._bool(payload, "rf_sample_weight", bool(current["rf_sample_weight"])),
            "hgb_max_iter": self._bounded_int(payload, "hgb_max_iter", current["hgb_max_iter"], 20, 500),
            "hgb_learning_rate": self._bounded_float(payload, "hgb_learning_rate", current["hgb_learning_rate"], 0.005, 0.5),
            "hgb_max_leaf_nodes": self._bounded_int(payload, "hgb_max_leaf_nodes", current["hgb_max_leaf_nodes"], 4, 63),
            "hgb_min_samples_leaf": self._bounded_int(payload, "hgb_min_samples_leaf", current["hgb_min_samples_leaf"], 2, 100),
            "hgb_l2_regularization": self._bounded_float(payload, "hgb_l2_regularization", current["hgb_l2_regularization"], 0.0, 10.0),
            "hgb_early_stopping": self._bool(payload, "hgb_early_stopping", bool(current["hgb_early_stopping"])),
            "hgb_sample_weight": self._bool(payload, "hgb_sample_weight", bool(current["hgb_sample_weight"])),
            "mlp_layer_size": self._bounded_int(payload, "mlp_layer_size", current["mlp_layer_size"], 8, 512),
            "mlp_layer_count": self._bounded_int(payload, "mlp_layer_count", current["mlp_layer_count"], 1, 4),
            "mlp_max_iter": self._bounded_int(payload, "mlp_max_iter", current["mlp_max_iter"], 20, 1000),
            "mlp_learning_rate": self._bounded_float(payload, "mlp_learning_rate", current["mlp_learning_rate"], 0.0001, 0.1),
            "mlp_alpha": self._bounded_float(payload, "mlp_alpha", current["mlp_alpha"], 0.00001, 1.0),
            "mlp_early_stopping": self._bool(payload, "mlp_early_stopping", bool(current["mlp_early_stopping"])),
            "mlp_device": self._normalize_torch_device(payload.get("mlp_device", current.get("mlp_device", "auto"))),
            "cnn_window": self._bounded_int(payload, "cnn_window", current["cnn_window"], 5, 120),
            "cnn_channels": self._bounded_int(payload, "cnn_channels", current["cnn_channels"], 4, 256),
            "cnn_kernel_size": self._bounded_int(payload, "cnn_kernel_size", current["cnn_kernel_size"], 1, 9),
            "cnn_layers": self._bounded_int(payload, "cnn_layers", current["cnn_layers"], 1, 5),
            "cnn_max_iter": self._bounded_int(payload, "cnn_max_iter", current["cnn_max_iter"], 20, 1000),
            "cnn_learning_rate": self._bounded_float(payload, "cnn_learning_rate", current["cnn_learning_rate"], 0.0001, 0.1),
            "cnn_alpha": self._bounded_float(payload, "cnn_alpha", current["cnn_alpha"], 0.00001, 1.0),
            "cnn_dropout": self._bounded_float(payload, "cnn_dropout", current["cnn_dropout"], 0.0, 0.8),
            "cnn_early_stopping": self._bool(payload, "cnn_early_stopping", bool(current["cnn_early_stopping"])),
            "cnn_sample_weight": self._bool(payload, "cnn_sample_weight", bool(current["cnn_sample_weight"])),
            "cnn_device": self._normalize_torch_device(payload.get("cnn_device", current.get("cnn_device", "auto"))),
        }

    def _profile_ml_params(self, profile_key: str, ml_model: str) -> dict[str, Any]:
        defaults = self._default_ml_params()
        if profile_key == "balanced" and ml_model == "random_forest":
            defaults.update(
                {
                    "ml_buy_threshold": 0.57,
                    "ml_sell_threshold": 0.42,
                    "ml_min_samples": 80,
                    "rf_n_estimators": 80,
                    "rf_max_depth": 5,
                    "rf_min_samples_leaf": 5,
                    "rf_max_features_sqrt": True,
                }
            )
        elif profile_key == "conservative" and ml_model == "random_forest":
            defaults.update(
                {
                    "ml_buy_threshold": 0.61,
                    "ml_sell_threshold": 0.44,
                    "ml_min_samples": 80,
                    "rf_n_estimators": 80,
                    "rf_max_depth": 5,
                    "rf_min_samples_leaf": 5,
                    "rf_max_features_sqrt": True,
                }
            )
        elif profile_key == "aggressive" and ml_model == "random_forest":
            defaults.update(
                {
                    "ml_buy_threshold": 0.40,
                    "ml_sell_threshold": 0.20,
                    "ml_min_samples": 100,
                    "rf_n_estimators": 300,
                    "rf_max_depth": 5,
                    "rf_min_samples_leaf": 20,
                    "rf_max_features_sqrt": True,
                }
            )
        elif profile_key == "conservative" and ml_model == "hist_gradient_boosting":
            defaults.update(
                {
                    "ml_buy_threshold": 0.63,
                    "ml_sell_threshold": 0.20,
                    "ml_min_samples": 80,
                    "hgb_max_iter": 140,
                    "hgb_learning_rate": 0.03,
                    "hgb_max_leaf_nodes": 15,
                    "hgb_min_samples_leaf": 12,
                    "hgb_l2_regularization": 0.08,
                    "hgb_early_stopping": False,
                    "hgb_sample_weight": False,
                }
            )
        elif profile_key == "balanced" and ml_model == "hist_gradient_boosting":
            defaults.update(
                {
                    "ml_buy_threshold": 0.60,
                    "ml_sell_threshold": 0.22,
                    "ml_min_samples": 80,
                    "hgb_max_iter": 140,
                    "hgb_learning_rate": 0.03,
                    "hgb_max_leaf_nodes": 15,
                    "hgb_min_samples_leaf": 12,
                    "hgb_l2_regularization": 0.08,
                    "hgb_early_stopping": False,
                    "hgb_sample_weight": False,
                }
            )
        elif profile_key == "aggressive" and ml_model == "hist_gradient_boosting":
            defaults.update(
                {
                    "ml_buy_threshold": 0.52,
                    "ml_sell_threshold": 0.18,
                    "ml_min_samples": 80,
                    "hgb_max_iter": 120,
                    "hgb_learning_rate": 0.035,
                    "hgb_max_leaf_nodes": 15,
                    "hgb_min_samples_leaf": 12,
                    "hgb_l2_regularization": 0.08,
                    "hgb_early_stopping": False,
                    "hgb_sample_weight": True,
                }
            )
        elif profile_key == "conservative" and ml_model == "xgboost":
            defaults.update(
                {
                    "ml_buy_threshold": 0.62,
                    "ml_sell_threshold": 0.22,
                    "ml_min_samples": 100,
                    "xgb_n_estimators": 100,
                    "xgb_max_depth": 2,
                    "xgb_learning_rate": 0.03,
                    "xgb_subsample": 0.80,
                    "xgb_colsample_bytree": 0.80,
                    "xgb_min_child_weight": 7.0,
                    "xgb_reg_lambda": 2.0,
                    "xgb_sample_weight": True,
                }
            )
        elif profile_key == "balanced" and ml_model == "xgboost":
            defaults.update(
                {
                    "ml_buy_threshold": 0.57,
                    "ml_sell_threshold": 0.22,
                    "ml_min_samples": 90,
                    "xgb_n_estimators": 120,
                    "xgb_max_depth": 3,
                    "xgb_learning_rate": 0.035,
                    "xgb_subsample": 0.85,
                    "xgb_colsample_bytree": 0.85,
                    "xgb_min_child_weight": 5.0,
                    "xgb_reg_lambda": 1.5,
                    "xgb_sample_weight": True,
                }
            )
        elif profile_key == "aggressive" and ml_model == "xgboost":
            defaults.update(
                {
                    "ml_buy_threshold": 0.50,
                    "ml_sell_threshold": 0.18,
                    "ml_min_samples": 80,
                    "xgb_n_estimators": 120,
                    "xgb_max_depth": 3,
                    "xgb_learning_rate": 0.045,
                    "xgb_subsample": 0.90,
                    "xgb_colsample_bytree": 0.90,
                    "xgb_min_child_weight": 3.0,
                    "xgb_reg_lambda": 1.0,
                    "xgb_sample_weight": True,
                }
            )
        elif profile_key == "conservative" and ml_model == "mlp":
            defaults.update(
                {
                    "ml_buy_threshold": 0.64,
                    "ml_sell_threshold": 0.32,
                    "ml_min_samples": 160,
                    "mlp_layer_size": 48,
                    "mlp_layer_count": 1,
                    "mlp_max_iter": 160,
                    "mlp_learning_rate": 0.001,
                    "mlp_alpha": 0.003,
                    "mlp_early_stopping": True,
                }
            )
        elif profile_key == "balanced" and ml_model == "mlp":
            defaults.update(
                {
                    "ml_buy_threshold": 0.60,
                    "ml_sell_threshold": 0.30,
                    "ml_min_samples": 140,
                    "mlp_layer_size": 64,
                    "mlp_layer_count": 1,
                    "mlp_max_iter": 180,
                    "mlp_learning_rate": 0.001,
                    "mlp_alpha": 0.002,
                    "mlp_early_stopping": True,
                }
            )
        elif profile_key == "aggressive" and ml_model == "mlp":
            defaults.update(
                {
                    "ml_buy_threshold": 0.60,
                    "ml_sell_threshold": 0.28,
                    "ml_min_samples": 120,
                    "mlp_layer_size": 64,
                    "mlp_layer_count": 2,
                    "mlp_max_iter": 200,
                    "mlp_learning_rate": 0.001,
                    "mlp_alpha": 0.0015,
                    "mlp_early_stopping": True,
                }
            )
        elif profile_key == "conservative" and ml_model == "cnn":
            defaults.update(
                {
                    "ml_buy_threshold": 0.64,
                    "ml_sell_threshold": 0.34,
                    "ml_min_samples": 160,
                    "cnn_window": 30,
                    "cnn_channels": 24,
                    "cnn_kernel_size": 3,
                    "cnn_layers": 2,
                    "cnn_max_iter": 140,
                    "cnn_learning_rate": 0.001,
                    "cnn_alpha": 0.003,
                    "cnn_dropout": 0.12,
                    "cnn_early_stopping": True,
                    "cnn_sample_weight": True,
                }
            )
        elif profile_key == "balanced" and ml_model == "cnn":
            defaults.update(
                {
                    "ml_buy_threshold": 0.60,
                    "ml_sell_threshold": 0.30,
                    "ml_min_samples": 140,
                    "cnn_window": 24,
                    "cnn_channels": 32,
                    "cnn_kernel_size": 3,
                    "cnn_layers": 2,
                    "cnn_max_iter": 160,
                    "cnn_learning_rate": 0.001,
                    "cnn_alpha": 0.002,
                    "cnn_dropout": 0.08,
                    "cnn_early_stopping": True,
                    "cnn_sample_weight": True,
                }
            )
        elif profile_key == "aggressive" and ml_model == "cnn":
            defaults.update(
                {
                    "ml_buy_threshold": 0.58,
                    "ml_sell_threshold": 0.26,
                    "ml_min_samples": 120,
                    "cnn_window": 20,
                    "cnn_channels": 48,
                    "cnn_kernel_size": 5,
                    "cnn_layers": 2,
                    "cnn_max_iter": 180,
                    "cnn_learning_rate": 0.0012,
                    "cnn_alpha": 0.0015,
                    "cnn_dropout": 0.08,
                    "cnn_early_stopping": True,
                    "cnn_sample_weight": True,
                }
            )
        override = getattr(self, "ml_profile_overrides", {}).get(ml_model, {}).get(profile_key, {})
        if override and profile_key != "custom":
            defaults.update(override)
        return defaults

    def _paper_only(self, settings: Settings) -> Settings:
        return replace(settings, broker="paper", dry_run=True)

    def _float(self, payload: dict[str, Any], key: str, default: float) -> float:
        try:
            return float(payload.get(key, default))
        except (TypeError, ValueError):
            return default

    def _int(self, payload: dict[str, Any], key: str, default: int) -> int:
        try:
            return int(float(payload.get(key, default)))
        except (TypeError, ValueError):
            return default

    def _bounded_float(
        self,
        payload: dict[str, Any],
        key: str,
        default: float,
        minimum: float,
        maximum: float,
    ) -> float:
        value = self._float(payload, key, default)
        return min(max(value, minimum), maximum)

    def _optional_float(
        self,
        payload: dict[str, Any],
        key: str,
        default: Any,
        minimum: float,
        maximum: float,
    ) -> float | str:
        raw = payload.get(key, default)
        if raw is None or str(raw).strip() == "":
            return ""
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default if default == "" else min(max(float(default), minimum), maximum)
        return min(max(value, minimum), maximum)

    def _bounded_int(
        self,
        payload: dict[str, Any],
        key: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        value = self._int(payload, key, default)
        return min(max(value, minimum), maximum)

    def _optional_int(
        self,
        payload: dict[str, Any],
        key: str,
        default: int | None,
        minimum: int,
        maximum: int,
    ) -> int | None:
        raw = payload.get(key, default)
        if raw is None or str(raw).strip() == "":
            return None
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            return default
        return min(max(value, minimum), maximum)

    def _profile_optional_ints(
        self,
        payload: dict[str, Any],
        suffix: str,
        current: dict[str, int | None],
        minimum: int,
        maximum: int,
        legacy_key: str | None = None,
        legacy_strategy: str | None = None,
    ) -> dict[str, int | None]:
        values = {key: current.get(key) for key in STRATEGY_KEYS}
        has_profile_field = False
        for key in STRATEGY_KEYS:
            has_profile_field = has_profile_field or f"{key}_{suffix}" in payload
            values[key] = self._optional_int(payload, f"{key}_{suffix}", values.get(key), minimum, maximum)
        if (
            not has_profile_field
            and legacy_key
            and legacy_key in payload
            and str(payload.get(legacy_key, "")).strip() != ""
        ):
            target = strategy_profile(legacy_strategy).key
            values[target] = self._optional_int(payload, legacy_key, values.get(target), minimum, maximum)
        return values

    def _bool(self, payload: dict[str, Any], key: str, default: bool) -> bool:
        raw = payload.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on", "checked"}

    def _bounded_interval_minutes(self, value: Any) -> int:
        try:
            minutes = int(float(value))
        except (TypeError, ValueError):
            minutes = self.backtest_interval_minutes
        if minutes <= 5:
            return 5
        if minutes <= 15:
            return 15
        if minutes <= 30:
            return 30
        return 60

    def _backtest_interval_minutes(self, payload: dict[str, Any]) -> int:
        raw = payload.get("backtest_interval_minutes", self.backtest_interval_minutes)
        if raw is None or str(raw).strip() == "":
            raw = self._interval_minutes(max(30, self._int(payload, "interval_seconds", self.auto_interval_seconds)))
        return self._bounded_interval_minutes(raw)

    def _history_lookback_days(self, days: int, fixed_year: bool, ml_requested: bool) -> int:
        base = 420 if fixed_year else max(30, days + 30)
        if ml_requested:
            base = max(base, self.ml_training_days + days + 30)
        return min(max(base, 30), 3650)

    def _training_bar_limit(self, slow_granularity: bool, bars_per_day: int) -> int:
        if slow_granularity:
            return max(80, self.ml_training_days * max(1, bars_per_day))
        return max(80, self.ml_training_days)

    def _yahoo_range_for_days(self, lookback_days: int) -> str:
        if lookback_days <= 730:
            return "2y"
        if lookback_days <= 1825:
            return "5y"
        return "10y"

    def _interval_minutes(self, interval_seconds: int) -> int:
        minutes = max(1, round(interval_seconds / 60))
        if minutes <= 1:
            return 1
        if minutes <= 5:
            return 5
        if minutes <= 15:
            return 15
        if minutes <= 30:
            return 30
        return 60

    def _bars_per_day(self, interval_minutes: int) -> int:
        trading_minutes = 240
        return max(1, trading_minutes // max(1, interval_minutes))

    def _pct(self, payload: dict[str, Any], key: str, default: float) -> float:
        value = self._float(payload, key, default)
        return value / 100 if value > 1 else value


def create_handler(dashboard: PaperTradingDashboard, static_dir: Path):
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_file(static_dir / "index.html")
                return
            if parsed.path == "/api/state":
                self._send_json(dashboard.state())
                return
            if parsed.path == "/api/backtest/status":
                query = parse_qs(parsed.query)
                task_id = (query.get("id") or [""])[0]
                self._send_json(dashboard.backtest_task_status(task_id))
                return
            if parsed.path == "/api/auto-learn/status":
                query = parse_qs(parsed.query)
                task_id = (query.get("id") or [""])[0]
                self._send_json(dashboard.auto_learn_task_status(task_id))
                return
            if parsed.path.startswith("/static/"):
                relative = parsed.path.removeprefix("/static/")
                self._send_file(static_dir / relative)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            try:
                if self.path == "/api/run":
                    self._send_json(dashboard.run_once(self._read_json()))
                    return
                if self.path == "/api/reset":
                    self._send_json(dashboard.reset(self._read_json()))
                    return
                if self.path == "/api/settings/save":
                    self._send_json(dashboard.save_settings(self._read_json()))
                    return
                if self.path == "/api/settings/reset":
                    self._read_json()
                    self._send_json(dashboard.reset_settings())
                    return
                if self.path == "/api/training/regimes/refresh":
                    self._send_json(dashboard.refresh_training_regimes(self._read_json()))
                    return
                if self.path == "/api/backtest":
                    self._send_json(dashboard.run_backtest(self._read_json()))
                    return
                if self.path == "/api/backtest/start":
                    self._send_json(dashboard.start_backtest_task(self._read_json()))
                    return
                if self.path == "/api/auto-learn/start":
                    self._send_json(dashboard.start_auto_learn_task(self._read_json()))
                    return
                if self.path == "/api/auto-learn/stop":
                    self._send_json(dashboard.stop_auto_learn_task(self._read_json()))
                    return
                if self.path == "/api/auto-learn/apply":
                    self._send_json(dashboard.apply_auto_learn_recommendation(self._read_json()))
                    return
                if self.path == "/api/manual/buy":
                    self._send_json(dashboard.manual_buy(self._read_json()))
                    return
                if self.path == "/api/auto/start":
                    self._send_json(dashboard.start_auto(self._read_json()))
                    return
                if self.path == "/api/auto/stop":
                    self._send_json(dashboard.stop_auto())
                    return
                self.send_error(HTTPStatus.NOT_FOUND)
            except Exception as exc:
                self._send_json({"error": str(exc), **dashboard.state()}, status=500)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path) -> None:
            if not path.exists() or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = path.read_bytes()
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
                content_type = f"{content_type}; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return DashboardHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Local AI paper trading dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    settings = load_settings()
    dashboard = PaperTradingDashboard(settings)
    static_dir = Path(__file__).resolve().parent / "static"
    server = ThreadingHTTPServer((args.host, args.port), create_handler(dashboard, static_dir))
    print(f"AI stock dashboard running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
