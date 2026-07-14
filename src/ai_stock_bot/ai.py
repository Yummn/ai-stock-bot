from __future__ import annotations

import json
import subprocess
import warnings
import datetime as dt
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .market_data import _snapshot_from_bars
from .models import Bar, MarketSnapshot, Side, Signal


class SignalProvider(Protocol):
    def decide(self, snapshots: list[MarketSnapshot]) -> list[Signal]:
        ...


@dataclass(frozen=True)
class StrategyProfile:
    key: str
    label: str
    buy_score: int
    sell_score: int
    trend_min: float
    momentum_5_min: float
    momentum_5_strong: float
    reversal_momentum_5: float
    reversal_change: float
    rsi_min: float
    rsi_max: float
    overbought_rsi: float
    max_volatility: float
    max_buys_per_day: int
    allow_add_position: bool


def _torch_cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


class _XGBoostCudaFallbackClassifier:
    def __init__(self, build_model: Any, preferred_device: str) -> None:
        self._build_model = build_model
        self.preferred_device = preferred_device
        self.actual_device = "cpu"
        self.model = None
        self.classes_ = []

    def fit(self, features: list[list[float]], labels: list[int], sample_weight: list[float] | None = None):
        devices = ["cuda", "cpu"] if self.preferred_device == "cuda" else ["cpu"]
        last_error: Exception | None = None
        for device in devices:
            model = self._build_model(device)
            try:
                if sample_weight is None:
                    model.fit(features, labels)
                else:
                    model.fit(features, labels, sample_weight=sample_weight)
                self.model = model
                self.actual_device = device
                self.classes_ = getattr(model, "classes_", [])
                return self
            except Exception as exc:
                last_error = exc
                if device == "cpu":
                    raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("XGBoost failed to train.")

    def predict_proba(self, features: list[list[float]]):
        if self.model is None:
            raise RuntimeError("XGBoost model has not been trained.")
        return self.model.predict_proba(features)


class _TorchMLPClassifier:
    def __init__(
        self,
        hidden_layer_sizes: tuple[int, ...],
        learning_rate: float,
        alpha: float,
        max_iter: int,
        early_stopping: bool,
        random_state: int = 42,
        device: str = "auto",
    ) -> None:
        self.hidden_layer_sizes = hidden_layer_sizes
        self.learning_rate = learning_rate
        self.alpha = alpha
        self.max_iter = max_iter
        self.early_stopping = early_stopping
        self.random_state = random_state
        self.requested_device = device
        self.actual_device = "cpu"
        self.classes_: list[int] = []
        self._model = None
        self._mean = None
        self._std = None

    def fit(self, features: list[list[float]], labels: list[int], sample_weight: list[float] | None = None):
        try:
            import torch
            from torch import nn
        except Exception as exc:
            raise RuntimeError("PyTorch is not installed; install torch to use the MLP model.") from exc

        if not features:
            raise RuntimeError("No samples for PyTorch MLP training.")
        self.classes_ = sorted({int(label) for label in labels})
        class_index = {label: index for index, label in enumerate(self.classes_)}
        if len(self.classes_) < 2:
            raise RuntimeError("PyTorch MLP needs at least two classes.")

        torch.manual_seed(self.random_state)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.random_state)

        device = self._resolve_device(torch)
        self.actual_device = str(device)
        x = torch.tensor(features, dtype=torch.float32, device=device)
        y = torch.tensor([class_index[int(label)] for label in labels], dtype=torch.long, device=device)
        self._mean = x.mean(dim=0, keepdim=True)
        self._std = x.std(dim=0, keepdim=True).clamp_min(1e-6)
        x = (x - self._mean) / self._std

        model = self._build_network(nn, x.shape[1], len(self.classes_)).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=self.learning_rate, weight_decay=self.alpha)
        criterion = nn.CrossEntropyLoss(reduction="none" if sample_weight is not None else "mean")
        weights = (
            torch.tensor(sample_weight, dtype=torch.float32, device=device)
            if sample_weight is not None
            else None
        )

        best_loss = float("inf")
        stale_epochs = 0
        patience = 14 if self.early_stopping else self.max_iter + 1
        for _ in range(self.max_iter):
            model.train()
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(x), y)
            if weights is not None:
                loss = (loss * weights).sum() / weights.sum().clamp_min(1e-6)
            loss.backward()
            optimizer.step()

            value = float(loss.detach().cpu())
            if value + 1e-5 < best_loss:
                best_loss = value
                stale_epochs = 0
            else:
                stale_epochs += 1
            if stale_epochs >= patience:
                break

        self._model = model
        return self

    def predict_proba(self, features: list[list[float]]):
        if self._model is None or self._mean is None or self._std is None:
            raise RuntimeError("PyTorch MLP model has not been trained.")
        import torch

        self._model.eval()
        with torch.no_grad():
            x = torch.tensor(features, dtype=torch.float32, device=self._mean.device)
            x = (x - self._mean) / self._std
            probabilities = torch.softmax(self._model(x), dim=1).detach().cpu().numpy()
        return probabilities

    def _resolve_device(self, torch: Any):
        requested = str(self.requested_device or "auto").strip().lower()
        if requested in {"cuda", "gpu"} and torch.cuda.is_available():
            return torch.device("cuda")
        if requested in {"auto", ""} and torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("cpu")

    def _build_network(self, nn: Any, input_size: int, output_size: int):
        layers: list[Any] = []
        previous = input_size
        for hidden_size in self.hidden_layer_sizes:
            layers.append(nn.Linear(previous, hidden_size))
            layers.append(nn.ReLU())
            layers.append(nn.Dropout(0.08))
            previous = hidden_size
        layers.append(nn.Linear(previous, output_size))
        return nn.Sequential(*layers)


class _TorchCNN1DClassifier:
    def __init__(
        self,
        channels: int,
        kernel_size: int,
        layers: int,
        learning_rate: float,
        alpha: float,
        max_iter: int,
        dropout: float,
        early_stopping: bool,
        random_state: int = 42,
        device: str = "auto",
    ) -> None:
        self.channels = channels
        self.kernel_size = kernel_size
        self.layers = layers
        self.learning_rate = learning_rate
        self.alpha = alpha
        self.max_iter = max_iter
        self.dropout = dropout
        self.early_stopping = early_stopping
        self.random_state = random_state
        self.requested_device = device
        self.actual_device = "cpu"
        self.classes_: list[int] = []
        self._model = None
        self._mean = None
        self._std = None

    def fit(
        self,
        features: list[list[list[float]]],
        labels: list[int],
        sample_weight: list[float] | None = None,
    ):
        try:
            import torch
            from torch import nn
        except Exception as exc:
            raise RuntimeError("PyTorch is not installed; install torch to use the 1D-CNN model.") from exc

        if not features:
            raise RuntimeError("No samples for 1D-CNN training.")
        self.classes_ = sorted({int(label) for label in labels})
        class_index = {label: index for index, label in enumerate(self.classes_)}
        if len(self.classes_) < 2:
            raise RuntimeError("1D-CNN needs at least two classes.")

        torch.manual_seed(self.random_state)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.random_state)

        device = self._resolve_device(torch)
        self.actual_device = str(device)
        x = torch.tensor(features, dtype=torch.float32, device=device)
        y = torch.tensor([class_index[int(label)] for label in labels], dtype=torch.long, device=device)
        self._mean = x.mean(dim=(0, 1), keepdim=True)
        self._std = x.std(dim=(0, 1), keepdim=True).clamp_min(1e-6)
        x = ((x - self._mean) / self._std).permute(0, 2, 1)

        model = self._build_network(nn, x.shape[1], len(self.classes_)).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=self.learning_rate, weight_decay=self.alpha)
        criterion = nn.CrossEntropyLoss(reduction="none" if sample_weight is not None else "mean")
        weights = (
            torch.tensor(sample_weight, dtype=torch.float32, device=device)
            if sample_weight is not None
            else None
        )

        best_loss = float("inf")
        stale_epochs = 0
        patience = 16 if self.early_stopping else self.max_iter + 1
        for _ in range(self.max_iter):
            model.train()
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(x), y)
            if weights is not None:
                loss = (loss * weights).sum() / weights.sum().clamp_min(1e-6)
            loss.backward()
            optimizer.step()

            value = float(loss.detach().cpu())
            if value + 1e-5 < best_loss:
                best_loss = value
                stale_epochs = 0
            else:
                stale_epochs += 1
            if stale_epochs >= patience:
                break

        self._model = model
        return self

    def predict_proba(self, features: list[list[list[float]]]):
        if self._model is None or self._mean is None or self._std is None:
            raise RuntimeError("1D-CNN model has not been trained.")
        import torch

        self._model.eval()
        with torch.no_grad():
            x = torch.tensor(features, dtype=torch.float32, device=self._mean.device)
            x = ((x - self._mean) / self._std).permute(0, 2, 1)
            probabilities = torch.softmax(self._model(x), dim=1).detach().cpu().numpy()
        return probabilities

    def _resolve_device(self, torch: Any):
        requested = str(self.requested_device or "auto").strip().lower()
        if requested in {"cuda", "gpu"} and torch.cuda.is_available():
            return torch.device("cuda")
        if requested in {"auto", ""} and torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("cpu")

    def _build_network(self, nn: Any, feature_count: int, output_size: int):
        blocks: list[Any] = []
        in_channels = feature_count
        padding = self.kernel_size // 2
        for _ in range(self.layers):
            blocks.append(nn.Conv1d(in_channels, self.channels, self.kernel_size, padding=padding))
            blocks.append(nn.ReLU())
            if self.dropout > 0:
                blocks.append(nn.Dropout(self.dropout))
            in_channels = self.channels
        blocks.append(nn.AdaptiveAvgPool1d(1))
        blocks.append(nn.Flatten())
        blocks.append(nn.Linear(in_channels, output_size))
        return nn.Sequential(*blocks)


_STRATEGIES = {
    "conservative": StrategyProfile(
        key="conservative",
        label="保守",
        buy_score=6,
        sell_score=4,
        trend_min=0.002,
        momentum_5_min=0.008,
        momentum_5_strong=0.018,
        reversal_momentum_5=0.045,
        reversal_change=0.025,
        rsi_min=45,
        rsi_max=72,
        overbought_rsi=78,
        max_volatility=0.045,
        max_buys_per_day=1,
        allow_add_position=False,
    ),
    "balanced": StrategyProfile(
        key="balanced",
        label="均衡",
        buy_score=5,
        sell_score=4,
        trend_min=0.0,
        momentum_5_min=0.004,
        momentum_5_strong=0.012,
        reversal_momentum_5=0.035,
        reversal_change=0.018,
        rsi_min=40,
        rsi_max=76,
        overbought_rsi=82,
        max_volatility=0.055,
        max_buys_per_day=2,
        allow_add_position=False,
    ),
    "aggressive": StrategyProfile(
        key="aggressive",
        label="激进",
        buy_score=4,
        sell_score=5,
        trend_min=-0.004,
        momentum_5_min=0.0,
        momentum_5_strong=0.008,
        reversal_momentum_5=0.025,
        reversal_change=0.012,
        rsi_min=35,
        rsi_max=84,
        overbought_rsi=88,
        max_volatility=0.075,
        max_buys_per_day=3,
        allow_add_position=True,
    ),
    "custom": StrategyProfile(
        key="custom",
        label="自定义",
        buy_score=5,
        sell_score=4,
        trend_min=0.0,
        momentum_5_min=0.004,
        momentum_5_strong=0.012,
        reversal_momentum_5=0.035,
        reversal_change=0.018,
        rsi_min=40,
        rsi_max=76,
        overbought_rsi=82,
        max_volatility=0.055,
        max_buys_per_day=2,
        allow_add_position=False,
    ),
}


def strategy_profile(name: str | None) -> StrategyProfile:
    raw = str(name or "conservative").strip().lower().replace("-", "_")
    return _STRATEGIES.get(raw, _STRATEGIES["conservative"])


def strategy_profile_with_overrides(
    name: str | None,
    buy_score: int | None = None,
    max_buys_per_day: int | None = None,
) -> StrategyProfile:
    profile = strategy_profile(name)
    updates: dict[str, int] = {}
    if buy_score is not None:
        updates["buy_score"] = max(1, min(20, int(buy_score)))
    if max_buys_per_day is not None:
        updates["max_buys_per_day"] = max(1, min(20, int(max_buys_per_day)))
    return replace(profile, **updates) if updates else profile


def infer_strategy_key(snapshot: MarketSnapshot) -> str:
    trend = snapshot.sma_5 / snapshot.sma_20 - 1.0 if snapshot.sma_20 else 0.0
    weak = (
        trend < -0.008
        or snapshot.momentum_20 < -0.04
        or snapshot.momentum_5 < -0.025
        or snapshot.volatility_20 > 0.07
    )
    strong = (
        trend > 0.008
        and snapshot.momentum_20 > 0.035
        and snapshot.momentum_5 > 0.006
        and snapshot.volatility_20 < 0.065
        and snapshot.rsi_14 < 82
    )
    if weak:
        return "conservative"
    if strong:
        return "aggressive"
    return "balanced"


def classify_year_regime_bars(bars: list[Bar]) -> dict[str, float | int | str]:
    year_bars = [bar for bar in bars if bar.close > 0]
    if len(year_bars) < 20:
        return {
            "regime": "sideways",
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "volatility": 0.0,
            "close_vs_ma": 0.0,
            "bar_count": len(year_bars),
        }
    annual_return = year_bars[-1].close / year_bars[0].close - 1.0
    peak = year_bars[0].close
    max_drawdown = 0.0
    returns: list[float] = []
    for index, bar in enumerate(year_bars):
        peak = max(peak, bar.close)
        max_drawdown = min(max_drawdown, bar.close / peak - 1.0)
        if index > 0 and year_bars[index - 1].close > 0:
            returns.append(bar.close / year_bars[index - 1].close - 1.0)
    if len(returns) > 1:
        mean_return = sum(returns) / len(returns)
        variance = sum((item - mean_return) ** 2 for item in returns) / (len(returns) - 1)
        volatility = variance**0.5 * (244**0.5)
    else:
        volatility = 0.0
    ma_window = year_bars[-min(60, len(year_bars)) :]
    ma = sum(bar.close for bar in ma_window) / len(ma_window)
    close_vs_ma = year_bars[-1].close / ma - 1.0 if ma else 0.0
    if annual_return >= 0.10 and close_vs_ma >= -0.02 and max_drawdown > -0.22:
        regime = "bull"
    elif annual_return <= -0.08 or (max_drawdown <= -0.20 and annual_return < 0.04):
        regime = "bear"
    else:
        regime = "sideways"
    return {
        "regime": regime,
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "volatility": volatility,
        "close_vs_ma": close_vs_ma,
        "bar_count": len(year_bars),
    }


class HeuristicSignalProvider:
    def __init__(
        self,
        strategy: str | None = None,
        buy_score: int | None = None,
        max_buys_per_day: int | None = None,
        auto_strategy: bool = False,
    ) -> None:
        self.profile = strategy_profile_with_overrides(strategy, buy_score, max_buys_per_day)
        self.auto_strategy = auto_strategy

    def decide(self, snapshots: list[MarketSnapshot]) -> list[Signal]:
        signals = [self._signal(snapshot) for snapshot in snapshots]
        buy_candidates = [(signal.confidence, index) for index, signal in enumerate(signals) if signal.side == Side.BUY]
        if len(buy_candidates) > self.profile.max_buys_per_day:
            keep = {
                index
                for _, index in sorted(buy_candidates, key=lambda item: item[0], reverse=True)[
                    : self.profile.max_buys_per_day
                ]
            }
            signals = [
                signal
                if index in keep or signal.side != Side.BUY
                else Signal(signal.symbol, Side.HOLD, 0.52, f"今日已有更强标的，暂不分散追买：{signal.reason}")
                for index, signal in enumerate(signals)
            ]
        return signals

    def _signal(self, snapshot: MarketSnapshot) -> Signal:
        profile = self._profile_for_snapshot(snapshot)
        trend = snapshot.sma_5 / snapshot.sma_20 - 1.0 if snapshot.sma_20 else 0.0
        buy_score = 0
        sell_score = 0
        reasons: list[str] = []

        if trend > profile.trend_min:
            buy_score += 2
            reasons.append(f"趋势{trend:.2%}")
        elif trend < -0.012:
            sell_score += 2
            reasons.append(f"趋势转弱{trend:.2%}")

        if snapshot.momentum_5 > profile.momentum_5_strong:
            buy_score += 2
            reasons.append(f"5日动量强{snapshot.momentum_5:.2%}")
        elif snapshot.momentum_5 > profile.momentum_5_min:
            buy_score += 1
            reasons.append(f"5日动量{snapshot.momentum_5:.2%}")
        elif snapshot.momentum_5 < -0.018:
            sell_score += 2
            reasons.append(f"5日动量转弱{snapshot.momentum_5:.2%}")

        if snapshot.momentum_20 > 0:
            buy_score += 1
        elif snapshot.momentum_20 < -0.025:
            sell_score += 1

        if profile.rsi_min <= snapshot.rsi_14 <= profile.rsi_max:
            buy_score += 1
            reasons.append(f"RSI {snapshot.rsi_14:.1f}")
        elif snapshot.rsi_14 > profile.overbought_rsi:
            sell_score += 2
            reasons.append(f"RSI过热{snapshot.rsi_14:.1f}")
        elif snapshot.rsi_14 < max(25, profile.rsi_min - 12):
            sell_score += 1
            reasons.append(f"RSI偏弱{snapshot.rsi_14:.1f}")

        if snapshot.volatility_20 <= profile.max_volatility:
            buy_score += 1
        else:
            sell_score += 1
            reasons.append(f"波动偏高{snapshot.volatility_20:.2%}")

        reversal = (
            -0.025 <= trend <= max(0.004, profile.trend_min)
            and snapshot.momentum_5 > profile.reversal_momentum_5
            and snapshot.momentum_20 > 0
            and snapshot.change_pct > profile.reversal_change
            and max(35, profile.rsi_min - 3) <= snapshot.rsi_14 <= min(68, profile.rsi_max)
            and snapshot.volatility_20 < min(profile.max_volatility, 0.055)
        )
        if reversal:
            buy_score += 2
            reasons.append("反转增强")

        prefix = f"自动{profile.label}：" if self.auto_strategy else ""
        if buy_score >= profile.buy_score and sell_score < profile.sell_score:
            return Signal(
                snapshot.symbol,
                Side.BUY,
                min(0.86, 0.55 + buy_score * 0.04),
                f"{prefix}多因子偏强：{self._join_reasons(reasons)}",
            )
        if sell_score >= profile.sell_score:
            return Signal(
                snapshot.symbol,
                Side.SELL,
                min(0.84, 0.55 + sell_score * 0.05),
                f"{prefix}风险信号偏强：{self._join_reasons(reasons)}",
            )
        return Signal(
            snapshot.symbol,
            Side.HOLD,
            0.5 + min(0.16, abs(buy_score - sell_score) * 0.03),
            f"{prefix}信号不够强，观望：{self._join_reasons(reasons)}",
        )

    def _profile_for_snapshot(self, snapshot: MarketSnapshot) -> StrategyProfile:
        if not self.auto_strategy:
            return self.profile
        return strategy_profile_with_overrides(
            infer_strategy_key(snapshot),
            max_buys_per_day=self.profile.max_buys_per_day,
        )

    def _join_reasons(self, reasons: list[str]) -> str:
        return "，".join(reasons[:4]) if reasons else "暂无明显优势"


class MachineLearningSignalProvider:
    """Tree-based signal provider trained from historical bars available at decision time."""

    def __init__(
        self,
        strategy: str | None = None,
        model_name: str = "random_forest",
        min_samples: int = 80,
        params: dict[str, Any] | None = None,
        max_buys_per_day: int | None = None,
        auto_strategy: bool = False,
    ) -> None:
        self.profile = strategy_profile_with_overrides(strategy, max_buys_per_day=max_buys_per_day)
        self.model_name = self._normalize_model_name(model_name)
        self.params = params or {}
        self.auto_strategy = auto_strategy
        self.year_regime_overrides = self._normalize_year_regime_overrides(
            self.params.get("ml_year_regime_overrides")
        )
        self.min_samples = self._param_int("ml_min_samples", min_samples, 20, 5000)
        self.training_mode = self._normalize_training_mode(self.params.get("ml_training_mode"))
        self.target_mode = self._normalize_target_mode(self.params.get("ml_target"))
        self.histories: dict[str, list[Bar]] = {}
        self.decision_histories: dict[str, list[Bar]] = {}
        self._history_signature: tuple[tuple[str, int, int], ...] = ()
        self._model = None
        self._samples = 0
        self._symbol_models: dict[str, Any] = {}
        self._symbol_samples: dict[str, int] = {}
        self._bucket_models: dict[str, Any] = {}
        self._bucket_samples: dict[str, int] = {}
        self._general_models: dict[str, Any] = {}
        self._general_samples: dict[str, int] = {}
        self.fallback = HeuristicSignalProvider(
            self.profile.key,
            max_buys_per_day=max_buys_per_day,
            auto_strategy=auto_strategy,
        )

    def set_histories(self, histories: dict[str, list[Bar]]) -> None:
        self.histories = histories
        self.decision_histories = histories
        signature = tuple(
            sorted((symbol, len(bars), bars[-1].timestamp if bars else 0) for symbol, bars in histories.items())
        )
        if signature != self._history_signature:
            self._history_signature = signature
            self._model = None
            self._samples = 0
            self._symbol_models = {}
            self._symbol_samples = {}
            self._bucket_models = {}
            self._bucket_samples = {}
            self._general_models = {}
            self._general_samples = {}

    def set_decision_histories(self, histories: dict[str, list[Bar]]) -> None:
        self.decision_histories = histories

    def decide(self, snapshots: list[MarketSnapshot]) -> list[Signal]:
        if self.training_mode in {"general", "auto", "mixed"} and self._model is None and not self._use_volatility_buckets():
            try:
                self._model, self._samples = self._train_model(None, use_regime_filter=False)
            except Exception as exc:
                return self._fallback(snapshots, f"{self.model_label} 训练失败：{exc}")

        if self.training_mode in {"general", "mixed"} and self._model is None and not self._use_volatility_buckets():
            return self._fallback(snapshots, f"{self.model_label} 通用模型样本不足：{self._samples} 条")

        signals: list[Signal] = []
        buy_candidates: list[tuple[float, int]] = []
        for snapshot in snapshots:
            model, samples, scope = self._model_for_snapshot(snapshot)
            features = self._prediction_features(snapshot)
            if model is None:
                signal = self._fallback_one(snapshot, f"{self.model_label} {scope}样本不足，已回退本地规则")
            elif features is None:
                signal = self._fallback_one(snapshot, f"{self.model_label} {scope}序列不足，已回退本地规则")
            elif self.target_mode == "trend5":
                probabilities = self._class_probabilities(model, features, 5)
                signal = self._signal_from_trend_probabilities(snapshot, probabilities, samples, scope)
            else:
                probability = self._positive_probability(model, features)
                signal = self._signal_from_probability(snapshot, probability, samples, scope)
            signals.append(signal)
            if signal.side == Side.BUY:
                buy_candidates.append((signal.confidence, len(signals) - 1))

        if len(buy_candidates) > self.profile.max_buys_per_day:
            keep = {
                index
                for _, index in sorted(buy_candidates, key=lambda item: item[0], reverse=True)[
                    : self.profile.max_buys_per_day
                ]
            }
            signals = [
                signal
                if index in keep or signal.side != Side.BUY
                else Signal(signal.symbol, Side.HOLD, 0.54, f"{self.model_label} 有更强标的，暂不分散买入：{signal.reason}")
                for index, signal in enumerate(signals)
            ]
        return signals

    def _model_for_snapshot(self, snapshot: MarketSnapshot):
        if self.training_mode == "self":
            model, samples = self._symbol_model(snapshot.symbol, use_regime_filter=False)
            if model is not None:
                return model, samples, "自己训练自己"
            model, classified_samples = self._general_model("classified", use_regime_filter=True)
            if model is not None:
                return model, classified_samples, f"自己样本不足({samples})，回退按行情分类"
            model, mixed_samples = self._general_model("mixed", use_regime_filter=False)
            return model, mixed_samples, f"分类样本不足({classified_samples})，回退全部混合"
        if self.training_mode == "classified":
            model, samples = self._general_model("classified", use_regime_filter=True)
            if model is not None:
                return model, samples, "按行情分类"
            model, mixed_samples = self._general_model("mixed", use_regime_filter=False)
            return model, mixed_samples, f"分类样本不足({samples})，回退全部混合"
        if self.training_mode == "mixed":
            return self._model, self._samples, "全部混合"
        if self._use_volatility_buckets():
            model, samples = self._bucket_model(self._volatility_bucket_from_snapshot(snapshot))
            if model is not None:
                return model, samples, f"{self._volatility_bucket_label(snapshot)}波动"
            if self.training_mode == "general":
                return None, samples, f"{self._volatility_bucket_label(snapshot)}波动"
        if self.training_mode == "general":
            return self._model, self._samples, "通用"
        if self.training_mode == "single":
            model, samples = self._symbol_model(snapshot.symbol)
            return model, samples, "单股"
        model, samples = self._symbol_model(snapshot.symbol)
        if model is not None:
            return model, samples, "自动单股"
        return self._model, self._samples, "自动通用"

    def _general_model(self, key: str, use_regime_filter: bool):
        if key in self._general_models or key in self._general_samples:
            return self._general_models.get(key), self._general_samples.get(key, 0)
        try:
            model, samples = self._train_model(None, use_regime_filter=use_regime_filter)
        except Exception:
            model, samples = None, 0
        if model is not None:
            self._general_models[key] = model
        self._general_samples[key] = samples
        return model, samples

    def _bucket_model(self, bucket: str):
        if bucket in self._bucket_models or bucket in self._bucket_samples:
            return self._bucket_models.get(bucket), self._bucket_samples.get(bucket, 0)
        try:
            model, samples = self._train_model(None, bucket, use_regime_filter=True)
        except Exception:
            model, samples = None, 0
        if model is not None:
            self._bucket_models[bucket] = model
        self._bucket_samples[bucket] = samples
        return model, samples

    def _symbol_model(self, symbol: str, use_regime_filter: bool | None = None):
        key = symbol if use_regime_filter is None else f"{symbol}|regime={int(use_regime_filter)}"
        if key in self._symbol_models or key in self._symbol_samples:
            return self._symbol_models.get(key), self._symbol_samples.get(key, 0)
        try:
            model, samples = self._train_model(symbol, use_regime_filter=use_regime_filter)
        except Exception:
            model, samples = None, 0
        if model is not None:
            self._symbol_models[key] = model
        self._symbol_samples[key] = samples
        return model, samples

    def _train_model(
        self,
        symbol_filter: str | None,
        volatility_bucket: str | None = None,
        use_regime_filter: bool | None = None,
    ):
        features: list[Any] = []
        labels: list[int] = []
        for symbol, bars in self.histories.items():
            if symbol_filter is not None and symbol != symbol_filter:
                continue
            if volatility_bucket is not None and self._volatility_bucket_from_bars(bars) != volatility_bucket:
                continue
            if len(bars) < 22:
                continue
            year_regimes = self._year_regimes(symbol, bars)
            start_index = max(20, self._cnn_window() + 19) if self.model_name == "cnn" else 20
            for index in range(start_index, len(bars) - 1):
                sample_year = dt.datetime.fromtimestamp(bars[index].timestamp).year
                if not self._regime_allowed(year_regimes.get(sample_year, "sideways"), use_regime_filter):
                    continue
                prefix = bars[: index + 1]
                try:
                    snapshot = _snapshot_from_bars(symbol, prefix)
                except Exception:
                    continue
                if self.model_name == "cnn":
                    sequence = self._sequence_features_from_bars(symbol, bars, index)
                    if sequence is None:
                        continue
                    features.append(sequence)
                else:
                    features.append(self._features(snapshot))
                labels.append(self._label_for_sample(snapshot, bars, index, year_regimes.get(sample_year, "sideways")))

        if len(features) < self.min_samples or len(set(labels)) < 2:
            return None, len(features)

        fit_features = features
        fit_labels = labels
        synthetic_count = 0
        if self.model_name == "xgboost" and self.target_mode == "trend5":
            missing_labels = [label for label in range(5) if label not in set(labels)]
            if missing_labels:
                fit_features = features + [features[0] for _ in missing_labels]
                fit_labels = labels + missing_labels
                synthetic_count = len(missing_labels)

        model = self._build_model()
        sample_weight = self._balanced_sample_weight(labels)
        if synthetic_count:
            base_weight = sample_weight if sample_weight is not None else [1.0 for _ in labels]
            sample_weight = base_weight + [0.0 for _ in range(synthetic_count)]
        with warnings.catch_warnings():
            if self.model_name == "mlp":
                from sklearn.exceptions import ConvergenceWarning

                warnings.simplefilter("ignore", ConvergenceWarning)
            if sample_weight is None:
                model.fit(fit_features, fit_labels)
            else:
                model.fit(fit_features, fit_labels, sample_weight=sample_weight)
        return model, len(features)

    @property
    def engine_key(self) -> str:
        if self.model_name == "mlp":
            return "mlp"
        if self.model_name == "cnn":
            return "cnn1d"
        if self.model_name == "xgboost":
            return "xgboost"
        if self.model_name == "hist_gradient_boosting":
            return "hist-gradient-boosting"
        return "random-forest"

    @property
    def model_label(self) -> str:
        if self.model_name == "mlp":
            return "MLP"
        if self.model_name == "cnn":
            return "1D-CNN"
        if self.model_name == "xgboost":
            return "XGBoost"
        if self.model_name == "hist_gradient_boosting":
            return "HistGradientBoosting"
        return "RandomForest"

    def _build_model(self):
        if self.model_name == "xgboost":
            from xgboost import XGBClassifier

            def build_xgb(device: str):
                common = {
                    "n_estimators": self._param_int("xgb_n_estimators", 120, 20, 500),
                    "max_depth": self._param_int("xgb_max_depth", 3, 1, 12),
                    "learning_rate": self._param_float("xgb_learning_rate", 0.035, 0.005, 0.5),
                    "subsample": self._param_float("xgb_subsample", 0.85, 0.3, 1.0),
                    "colsample_bytree": self._param_float("xgb_colsample_bytree", 0.85, 0.3, 1.0),
                    "min_child_weight": self._param_float("xgb_min_child_weight", 5.0, 0.1, 50.0),
                    "reg_lambda": self._param_float("xgb_reg_lambda", 1.5, 0.0, 20.0),
                    "tree_method": "hist",
                    "device": device,
                    "random_state": 42,
                    "n_jobs": 1,
                }
                if self.target_mode == "trend5":
                    return XGBClassifier(
                        **common,
                        objective="multi:softprob",
                        num_class=5,
                        eval_metric="mlogloss",
                    )
                return XGBClassifier(
                    **common,
                    objective="binary:logistic",
                    eval_metric="logloss",
                )

            return _XGBoostCudaFallbackClassifier(build_xgb, self._xgb_preferred_device())
        if self.model_name == "hist_gradient_boosting":
            from sklearn.ensemble import HistGradientBoostingClassifier

            return HistGradientBoostingClassifier(
                max_iter=self._param_int("hgb_max_iter", 140, 20, 500),
                learning_rate=self._param_float("hgb_learning_rate", 0.03, 0.005, 0.5),
                max_leaf_nodes=self._param_int("hgb_max_leaf_nodes", 15, 4, 63),
                min_samples_leaf=self._param_int("hgb_min_samples_leaf", 12, 2, 100),
                l2_regularization=self._param_float("hgb_l2_regularization", 0.08, 0.0, 10.0),
                early_stopping=self._param_bool("hgb_early_stopping", False),
                random_state=42,
            )
        if self.model_name == "mlp":
            layer_size = self._param_int("mlp_layer_size", 64, 8, 512)
            layer_count = self._param_int("mlp_layer_count", 2, 1, 4)
            hidden_layer_sizes = tuple(layer_size for _ in range(layer_count))
            return _TorchMLPClassifier(
                hidden_layer_sizes=hidden_layer_sizes,
                learning_rate=self._param_float("mlp_learning_rate", 0.001, 0.0001, 0.1),
                alpha=self._param_float("mlp_alpha", 0.001, 0.00001, 1.0),
                max_iter=self._param_int("mlp_max_iter", 180, 20, 1000),
                early_stopping=self._param_bool("mlp_early_stopping", True),
                random_state=42,
                device=self._param_device("mlp_device", "auto"),
            )
        if self.model_name == "cnn":
            kernel_size = self._param_int("cnn_kernel_size", 3, 1, 9)
            if kernel_size % 2 == 0:
                kernel_size += 1
            return _TorchCNN1DClassifier(
                channels=self._param_int("cnn_channels", 32, 4, 256),
                kernel_size=kernel_size,
                layers=self._param_int("cnn_layers", 2, 1, 5),
                learning_rate=self._param_float("cnn_learning_rate", 0.001, 0.0001, 0.1),
                alpha=self._param_float("cnn_alpha", 0.001, 0.00001, 1.0),
                max_iter=self._param_int("cnn_max_iter", 160, 20, 1000),
                dropout=self._param_float("cnn_dropout", 0.08, 0.0, 0.8),
                early_stopping=self._param_bool("cnn_early_stopping", True),
                random_state=42,
                device=self._param_device("cnn_device", "auto"),
            )

        from sklearn.ensemble import RandomForestClassifier

        return RandomForestClassifier(
            n_estimators=self._param_int("rf_n_estimators", 80, 10, 500),
            max_depth=self._param_int("rf_max_depth", 5, 1, 30),
            min_samples_leaf=self._param_int("rf_min_samples_leaf", 5, 1, 100),
            max_features="sqrt" if self._param_bool("rf_max_features_sqrt", True) else None,
            class_weight="balanced_subsample" if self._param_bool("rf_class_weight", True) else None,
            random_state=42,
            n_jobs=1,
        )

    def _balanced_sample_weight(self, labels: list[int]) -> list[float] | None:
        if self.model_name == "xgboost":
            if not self._param_bool("xgb_sample_weight", True):
                return None
        elif self.model_name == "mlp":
            return None
        elif self.model_name == "cnn":
            if not self._param_bool("cnn_sample_weight", True):
                return None
        elif self.model_name == "hist_gradient_boosting":
            if not self._param_bool("hgb_sample_weight", False):
                return None
        elif not self._param_bool("rf_sample_weight", True):
            return None
        counts = {label: labels.count(label) for label in set(labels)}
        total = len(labels)
        classes = max(1, len(counts))
        return [total / (classes * counts[label]) for label in labels]

    def _features(self, snapshot: MarketSnapshot) -> list[float]:
        trend = self._trend(snapshot)
        price_vs_sma5 = self._price_vs_sma5(snapshot)
        price_vs_sma20 = self._price_vs_sma20(snapshot)
        weak_score = self._weak_market_score(snapshot, self.profile)
        oversold = max(0.0, (50.0 - snapshot.rsi_14) / 50.0)
        overbought = max(0.0, (snapshot.rsi_14 - 70.0) / 30.0)
        rebound_setup = 1.0 if self._bear_rebound_setup(snapshot, self.profile, weak_score) else 0.0
        return [
            snapshot.change_pct,
            trend,
            price_vs_sma5,
            price_vs_sma20,
            snapshot.momentum_5,
            snapshot.momentum_20,
            snapshot.rsi_14 / 100.0,
            snapshot.volatility_20,
            snapshot.volume_ratio_20,
            weak_score / 6.0,
            oversold,
            overbought,
            rebound_setup,
        ]

    def _prediction_features(self, snapshot: MarketSnapshot) -> Any | None:
        if self.model_name != "cnn":
            return self._features(snapshot)
        bars = self.decision_histories.get(snapshot.symbol) or self.histories.get(snapshot.symbol) or []
        if not bars:
            return None
        return self._sequence_features_from_bars(snapshot.symbol, bars, len(bars) - 1)

    def _sequence_features_from_bars(self, symbol: str, bars: list[Bar], end_index: int) -> list[list[float]] | None:
        window = self._cnn_window()
        if end_index < 20 or end_index - window + 1 < 20:
            return None
        sequence: list[list[float]] = []
        for index in range(end_index - window + 1, end_index + 1):
            if index < 20:
                return None
            try:
                sequence.append(self._features(_snapshot_from_bars(symbol, bars[: index + 1])))
            except Exception:
                return None
        return sequence

    def _cnn_window(self) -> int:
        return self._param_int("cnn_window", 20, 5, 120)

    def _label_for_sample(self, snapshot: MarketSnapshot, bars: list[Bar], index: int, year_regime: str) -> int:
        next_return = (bars[index + 1].close - bars[index].close) / bars[index].close if bars[index].close else 0.0
        if self.target_mode == "trend5":
            return self._label_from_return(next_return)
        if self._use_bear_rebound_label(snapshot, year_regime):
            return self._bear_rebound_label(bars, index)
        return self._label_from_return(next_return)

    def _label_from_return(self, next_return: float) -> int:
        if self.target_mode != "trend5":
            return 1 if next_return > 0.001 else 0
        if next_return <= -0.015:
            return 0
        if next_return < -0.003:
            return 1
        if next_return <= 0.003:
            return 2
        if next_return <= 0.015:
            return 3
        return 4

    def _use_bear_rebound_label(self, snapshot: MarketSnapshot, year_regime: str) -> bool:
        if self.target_mode != "binary":
            return False
        if not self._param_bool("ml_bear_rebound_label", self.profile.key == "conservative"):
            return False
        if self.profile.key != "conservative":
            return False
        return year_regime == "bear" or self._weak_market_score(snapshot, self.profile) >= 2

    def _bear_rebound_label(self, bars: list[Bar], index: int) -> int:
        current = bars[index].close
        if current <= 0:
            return 0
        horizon = self._param_int("ml_bear_rebound_horizon", 5, 2, 12)
        future = bars[index + 1 : min(len(bars), index + horizon + 1)]
        if not future:
            return 0
        returns = [(bar.close - current) / current for bar in future]
        target = self._param_float("ml_bear_rebound_target", 0.012, 0.001, 0.10)
        max_drawdown = self._param_float("ml_bear_rebound_max_drawdown", 0.035, 0.005, 0.20)
        max_return = max(returns)
        min_return = min(returns)
        final_return = returns[-1]
        return 1 if max_return >= target and min_return >= -max_drawdown and final_return > -max_drawdown * 0.65 else 0

    def _use_volatility_buckets(self) -> bool:
        if self.training_mode in {"self", "classified", "mixed"}:
            return False
        raw = str(self.params.get("ml_volatility_filter", "on")).strip().lower()
        return raw not in {"", "off", "false", "0", "none", "all"}

    def _use_regime_filter(self) -> bool:
        if self.training_mode == "classified":
            return True
        if self.training_mode in {"self", "mixed"}:
            return False
        raw = str(self.params.get("ml_regime_filter", "on")).strip().lower()
        return raw not in {"", "off", "false", "0", "none", "all"}

    def _regime_allowed(self, regime: str, use_regime_filter: bool | None = None) -> bool:
        enabled = self._use_regime_filter() if use_regime_filter is None else use_regime_filter
        if not enabled or self.profile.key == "custom" or self.auto_strategy:
            return True
        if self.profile.key == "aggressive":
            return regime == "bull"
        if self.profile.key == "conservative":
            return regime == "bear"
        return regime == "sideways"

    def _year_regimes(self, symbol: str, bars: list[Bar]) -> dict[int, str]:
        by_year: dict[int, list[Bar]] = {}
        for bar in bars:
            by_year.setdefault(dt.datetime.fromtimestamp(bar.timestamp).year, []).append(bar)
        regimes: dict[int, str] = {}
        for year, year_bars in by_year.items():
            key = f"{symbol.upper()}:{year}"
            if key in self.year_regime_overrides:
                regimes[year] = self.year_regime_overrides[key]
                continue
            regimes[year] = str(classify_year_regime_bars(year_bars)["regime"])
        return regimes

    def _normalize_year_regime_overrides(self, raw: Any) -> dict[str, str]:
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

    def _volatility_bucket_from_snapshot(self, snapshot: MarketSnapshot) -> str:
        if snapshot.volatility_20 >= 0.024:
            return "high"
        if snapshot.volatility_20 <= 0.010:
            return "low"
        return "medium"

    def _volatility_bucket_from_bars(self, bars: list[Bar]) -> str:
        if len(bars) < 22:
            return "medium"
        returns: list[float] = []
        for index in range(1, len(bars)):
            previous = bars[index - 1].close
            if previous > 0:
                returns.append((bars[index].close - previous) / previous)
        if not returns:
            return "medium"
        avg = sum(returns) / len(returns)
        variance = sum((item - avg) ** 2 for item in returns) / len(returns)
        volatility = variance ** 0.5
        if volatility >= 0.024:
            return "high"
        if volatility <= 0.010:
            return "low"
        return "medium"

    def _volatility_bucket_label(self, snapshot: MarketSnapshot) -> str:
        return {"low": "低", "medium": "中", "high": "高"}[self._volatility_bucket_from_snapshot(snapshot)]

    def _positive_probability(self, model: Any, features: list[float]) -> float:
        probabilities = self._class_probabilities(model, features, 2)
        return probabilities[1]

    def _class_probabilities(self, model: Any, features: list[float], class_count: int) -> list[float]:
        raw = list(model.predict_proba([features])[0])
        classes = list(getattr(model, "classes_", range(len(raw))))
        if len(raw) == class_count and classes == list(range(class_count)):
            return [float(item) for item in raw]
        probabilities = [0.0 for _ in range(class_count)]
        for class_label, probability in zip(classes, raw):
            try:
                index = int(class_label)
            except (TypeError, ValueError):
                continue
            if 0 <= index < class_count:
                probabilities[index] = float(probability)
        total = sum(probabilities)
        if total <= 0:
            return [1.0 / class_count for _ in range(class_count)]
        return [item / total for item in probabilities]

    def _signal_from_probability(self, snapshot: MarketSnapshot, probability: float, samples: int, scope: str) -> Signal:
        profile = self._profile_for_snapshot(snapshot)
        buy_threshold, sell_threshold = self._thresholds(profile)
        trend = self._trend(snapshot)
        buy_threshold, sell_threshold = self._regime_adjusted_thresholds(snapshot, buy_threshold, sell_threshold, profile)
        risk_sell_threshold = self._risk_sell_threshold(buy_threshold, sell_threshold)
        label = f"{self.model_label}-{scope}"
        regime_blocked = self._regime_blocks_buy(snapshot, probability, buy_threshold, profile)
        risk_blocked = (
            snapshot.rsi_14 > profile.overbought_rsi
            or snapshot.volatility_20 > profile.max_volatility * 1.25
            or regime_blocked
        )
        label_suffix = f"自动{profile.label}" if self.auto_strategy else profile.label
        regime_note = self._market_regime_note(snapshot, profile, regime_blocked)
        if probability >= buy_threshold and not risk_blocked:
            side = Side.BUY
            confidence = min(0.86, max(0.61, probability))
            reason = (
                f"{label} 预测上涨概率 {probability:.1%}，样本 {samples} 条；"
                f"{label_suffix}{regime_note}；趋势{trend:.2%}，5日动量{snapshot.momentum_5:.2%}，RSI {snapshot.rsi_14:.1f}"
            )
        elif probability <= sell_threshold or (risk_blocked and probability <= risk_sell_threshold):
            side = Side.SELL
            confidence = min(0.84, max(0.61, 1 - probability))
            reason = (
                f"{label} 预测上涨概率偏低 {probability:.1%}{regime_note}；"
                f"趋势{trend:.2%}，波动{snapshot.volatility_20:.2%}，RSI {snapshot.rsi_14:.1f}"
            )
        else:
            side = Side.HOLD
            confidence = min(0.68, 0.5 + abs(probability - 0.5))
            reason = (
                f"{label} 预测上涨概率 {probability:.1%}，未达到{label_suffix}买卖阈值{regime_note}；"
                f"趋势{trend:.2%}，5日动量{snapshot.momentum_5:.2%}"
            )
        return Signal(snapshot.symbol, side, round(confidence, 3), reason)

    def _signal_from_trend_probabilities(
        self,
        snapshot: MarketSnapshot,
        probabilities: list[float],
        samples: int,
        scope: str,
    ) -> Signal:
        profile = self._profile_for_snapshot(snapshot)
        buy_threshold, sell_threshold = self._thresholds(profile)
        trend = self._trend(snapshot)
        buy_threshold, sell_threshold = self._regime_adjusted_thresholds(snapshot, buy_threshold, sell_threshold, profile)
        risk_sell_threshold = self._risk_sell_threshold(buy_threshold, sell_threshold)
        label = f"{self.model_label}-{scope}-五分类"
        weighted_score = (
            probabilities[4] * 2.0
            + probabilities[3]
            - probabilities[1]
            - probabilities[0] * 2.0
        )
        normalized_score = (weighted_score + 2.0) / 4.0
        up_probability = probabilities[3] + probabilities[4]
        down_probability = probabilities[0] + probabilities[1]
        regime_blocked = self._regime_blocks_buy(snapshot, normalized_score, buy_threshold, profile)
        risk_blocked = (
            snapshot.rsi_14 > profile.overbought_rsi
            or snapshot.volatility_20 > profile.max_volatility * 1.25
            or regime_blocked
        )
        label_suffix = f"自动{profile.label}" if self.auto_strategy else profile.label
        regime_note = self._market_regime_note(snapshot, profile, regime_blocked)
        probability_text = (
            f"大涨{probabilities[4]:.1%}/小涨{probabilities[3]:.1%}/"
            f"横盘{probabilities[2]:.1%}/小跌{probabilities[1]:.1%}/大跌{probabilities[0]:.1%}"
        )
        if normalized_score >= buy_threshold and not risk_blocked:
            side = Side.BUY
            confidence = min(0.86, max(0.61, normalized_score))
            reason = (
                f"{label} 加权分 {normalized_score:.2f}，上涨合计 {up_probability:.1%}，样本 {samples} 条；"
                f"{label_suffix}{regime_note}；{probability_text}；趋势{trend:.2%}，RSI {snapshot.rsi_14:.1f}"
            )
        elif normalized_score <= sell_threshold or (risk_blocked and normalized_score <= risk_sell_threshold):
            side = Side.SELL
            confidence = min(0.84, max(0.61, 1 - normalized_score))
            reason = (
                f"{label} 加权分偏低 {normalized_score:.2f}，下跌合计 {down_probability:.1%}{regime_note}；"
                f"{probability_text}；波动{snapshot.volatility_20:.2%}，RSI {snapshot.rsi_14:.1f}"
            )
        else:
            side = Side.HOLD
            confidence = min(0.68, 0.5 + abs(normalized_score - 0.5))
            reason = (
                f"{label} 加权分 {normalized_score:.2f}，未达到{label_suffix}买卖阈值{regime_note}；"
                f"{probability_text}；趋势{trend:.2%}"
            )
        return Signal(snapshot.symbol, side, round(confidence, 3), reason)

    def _thresholds(self, profile: StrategyProfile | None = None) -> tuple[float, float]:
        profile = profile or self.profile
        if self.model_name == "mlp":
            if profile.key == "aggressive":
                return self._custom_thresholds(0.60, 0.28)
            if profile.key == "balanced":
                return self._custom_thresholds(0.60, 0.30)
            return self._custom_thresholds(0.64, 0.32)
        if self.model_name == "cnn":
            if profile.key == "aggressive":
                return self._custom_thresholds(0.58, 0.26)
            if profile.key == "balanced":
                return self._custom_thresholds(0.60, 0.30)
            return self._custom_thresholds(0.64, 0.34)
        if self.model_name == "xgboost":
            if profile.key == "aggressive":
                return self._custom_thresholds(0.50, 0.18)
            if profile.key == "balanced":
                return self._custom_thresholds(0.57, 0.22)
            return self._custom_thresholds(0.62, 0.22)
        if self.model_name == "hist_gradient_boosting":
            if profile.key == "aggressive":
                return self._custom_thresholds(0.57, 0.25)
            if profile.key == "balanced":
                return self._custom_thresholds(0.60, 0.22)
            return self._custom_thresholds(0.63, 0.20)
        if profile.key == "aggressive":
            return self._custom_thresholds(0.40, 0.20)
        if profile.key == "balanced":
            return self._custom_thresholds(0.57, 0.42)
        return self._custom_thresholds(0.61, 0.44)

    def _risk_sell_threshold(self, buy_threshold: float, sell_threshold: float) -> float:
        return min(sell_threshold + 0.03, buy_threshold - 0.10)

    def _regime_adjusted_thresholds(
        self, snapshot: MarketSnapshot, buy_threshold: float, sell_threshold: float, profile: StrategyProfile | None = None
    ) -> tuple[float, float]:
        profile = profile or self.profile
        weak_score = self._weak_market_score(snapshot, profile)

        if profile.key == "conservative" and weak_score >= 2:
            base_adjustment = 0.055
            extra_adjustment = min(0.055, 0.018 * max(0, weak_score - 2))
            if self._bear_rebound_setup(snapshot, profile, weak_score):
                base_adjustment *= 0.45
                extra_adjustment *= 0.45
            adjustment = base_adjustment + extra_adjustment
            buy_threshold = min(0.88, buy_threshold + adjustment)
            sell_threshold = min(buy_threshold - 0.07, sell_threshold + min(0.12, adjustment + 0.025))
            return buy_threshold, max(0.01, sell_threshold)

        market_regime = infer_strategy_key(snapshot)
        if market_regime == "aggressive":
            buy_threshold, sell_threshold = self._bull_adjusted_thresholds(
                snapshot, buy_threshold, sell_threshold, profile
            )
        elif market_regime == "balanced" or self._looks_sideways(snapshot, profile):
            buy_threshold, sell_threshold = self._sideways_adjusted_thresholds(
                snapshot, buy_threshold, sell_threshold, profile
            )

        if self.model_name != "mlp":
            return buy_threshold, sell_threshold
        trigger_score, base_adjustment, max_extra_adjustment, max_sell_adjustment = self._mlp_regime_guard(profile)
        if weak_score < trigger_score:
            return buy_threshold, sell_threshold

        adjustment = base_adjustment + min(max_extra_adjustment, 0.02 * (weak_score - trigger_score))
        buy_threshold = min(0.86, buy_threshold + adjustment)
        sell_threshold = min(buy_threshold - 0.08, sell_threshold + min(max_sell_adjustment, adjustment))
        return buy_threshold, max(0.01, sell_threshold)

    def _mlp_regime_blocks_buy(
        self, snapshot: MarketSnapshot, score: float, buy_threshold: float, profile: StrategyProfile | None = None
    ) -> bool:
        return self._regime_blocks_buy(snapshot, score, buy_threshold, profile)

    def _regime_blocks_buy(
        self, snapshot: MarketSnapshot, score: float, buy_threshold: float, profile: StrategyProfile | None = None
    ) -> bool:
        profile = profile or self.profile
        weak_score = self._weak_market_score(snapshot, profile)
        if profile.key == "conservative" and weak_score >= 3:
            if not self._bear_rebound_setup(snapshot, profile, weak_score):
                return True
            return score < min(0.89, buy_threshold + 0.055)
        if (
            infer_strategy_key(snapshot) == "balanced" or self._looks_sideways(snapshot, profile)
        ) and self._sideways_range_state(snapshot) == "upper":
            return score < min(0.88, buy_threshold + 0.045)
        if self.model_name != "mlp":
            return False
        if profile.key == "aggressive":
            required_weak_score = 5
        elif profile.key == "balanced":
            required_weak_score = 4
        else:
            required_weak_score = 4
        if weak_score < required_weak_score:
            return False
        return score < min(0.84, buy_threshold + 0.06)

    def _mlp_regime_guard(self, profile: StrategyProfile) -> tuple[int, float, float, float]:
        if profile.key == "aggressive":
            return 4, 0.035, 0.03, 0.05
        if profile.key == "balanced":
            return 3, 0.045, 0.04, 0.08
        return 2, 0.07, 0.06, 0.12

    def _mlp_weak_score(self, snapshot: MarketSnapshot, profile: StrategyProfile | None = None) -> int:
        return self._weak_market_score(snapshot, profile)

    def _weak_market_score(self, snapshot: MarketSnapshot, profile: StrategyProfile | None = None) -> int:
        profile = profile or self.profile
        trend = self._trend(snapshot)
        price_vs_sma20 = self._price_vs_sma20(snapshot)
        weak_score = 0
        if trend < -0.005:
            weak_score += 1
        if snapshot.momentum_20 < -0.03:
            weak_score += 1
        if snapshot.momentum_5 < -0.02:
            weak_score += 1
        if snapshot.change_pct < -0.01:
            weak_score += 1
        if snapshot.volatility_20 > profile.max_volatility:
            weak_score += 1
        if price_vs_sma20 < -0.035:
            weak_score += 1
        return weak_score

    def _bear_rebound_setup(
        self, snapshot: MarketSnapshot, profile: StrategyProfile | None = None, weak_score: int | None = None
    ) -> bool:
        profile = profile or self.profile
        weak_score = self._weak_market_score(snapshot, profile) if weak_score is None else weak_score
        if weak_score < 2:
            return False
        setup_score = 0
        if 28 <= snapshot.rsi_14 <= 55:
            setup_score += 1
        if snapshot.momentum_5 > -0.022:
            setup_score += 1
        if snapshot.change_pct > -0.008:
            setup_score += 1
        if self._price_vs_sma5(snapshot) > -0.035:
            setup_score += 1
        if snapshot.volatility_20 <= profile.max_volatility * 1.20:
            setup_score += 1
        if snapshot.volume_ratio_20 <= 2.8:
            setup_score += 1
        return setup_score >= 5

    def _market_regime_note(self, snapshot: MarketSnapshot, profile: StrategyProfile, blocked: bool) -> str:
        weak_score = self._weak_market_score(snapshot, profile)
        if profile.key != "conservative" or weak_score < 2:
            market_regime = infer_strategy_key(snapshot)
            if market_regime == "aggressive" and self._bull_market_score(snapshot, profile) >= 3:
                return "；牛市趋势持有校准"
            if market_regime == "balanced" or self._looks_sideways(snapshot, profile):
                range_state = self._sideways_range_state(snapshot)
                if blocked and range_state == "upper":
                    return "；震荡区间高位不追"
                if range_state == "lower":
                    return "；震荡区间低位校准"
                if range_state == "upper":
                    return "；震荡区间高位校准"
            return ""
        if blocked:
            if self._bear_rebound_setup(snapshot, profile, weak_score):
                return "；弱市反弹概率不足"
            return "；弱市防守未见可控反弹"
        if self._bear_rebound_setup(snapshot, profile, weak_score):
            return "；弱市反弹条件通过"
        return "；弱市提高买入门槛"

    def _bull_adjusted_thresholds(
        self, snapshot: MarketSnapshot, buy_threshold: float, sell_threshold: float, profile: StrategyProfile
    ) -> tuple[float, float]:
        strength = self._bull_market_score(snapshot, profile)
        if strength < 3:
            return buy_threshold, sell_threshold
        sell_adjustment = min(0.08, 0.035 + 0.012 * max(0, strength - 3))
        buy_adjustment = 0.012 if profile.key == "conservative" else 0.02
        if self._is_overextended(snapshot, profile):
            buy_threshold = min(0.92, buy_threshold + 0.035)
            sell_adjustment *= 0.45
        else:
            buy_threshold = max(0.05, buy_threshold - buy_adjustment)
        sell_threshold = max(0.01, sell_threshold - sell_adjustment)
        return max(buy_threshold, sell_threshold + 0.05), sell_threshold

    def _sideways_adjusted_thresholds(
        self, snapshot: MarketSnapshot, buy_threshold: float, sell_threshold: float, profile: StrategyProfile
    ) -> tuple[float, float]:
        range_state = self._sideways_range_state(snapshot)
        if range_state == "lower":
            buy_threshold = max(0.05, buy_threshold - 0.03)
            sell_threshold = max(0.01, sell_threshold - 0.025)
        elif range_state == "upper":
            buy_threshold = min(0.92, buy_threshold + 0.045)
            sell_threshold = min(buy_threshold - 0.055, sell_threshold + 0.06)
        else:
            buy_threshold = min(0.90, buy_threshold + 0.01)
            sell_threshold = max(0.01, sell_threshold - 0.005)
        return max(buy_threshold, sell_threshold + 0.05), sell_threshold

    def _bull_market_score(self, snapshot: MarketSnapshot, profile: StrategyProfile) -> int:
        score = 0
        trend = self._trend(snapshot)
        if trend > 0.006:
            score += 1
        if snapshot.momentum_20 > 0.03:
            score += 1
        if snapshot.momentum_5 > 0.004:
            score += 1
        if self._price_vs_sma20(snapshot) > 0.012:
            score += 1
        if snapshot.volatility_20 <= profile.max_volatility * 1.2:
            score += 1
        if snapshot.rsi_14 < min(86, profile.overbought_rsi + 4):
            score += 1
        return score

    def _sideways_range_state(self, snapshot: MarketSnapshot) -> str:
        price_vs_sma20 = self._price_vs_sma20(snapshot)
        if price_vs_sma20 <= -0.014 or snapshot.rsi_14 <= 42:
            return "lower"
        if price_vs_sma20 >= 0.018 or snapshot.rsi_14 >= 68 or snapshot.change_pct >= 0.018:
            return "upper"
        return "middle"

    def _looks_sideways(self, snapshot: MarketSnapshot, profile: StrategyProfile) -> bool:
        trend = self._trend(snapshot)
        price_vs_sma20 = self._price_vs_sma20(snapshot)
        if self._bull_market_score(snapshot, profile) >= 4:
            return False
        if self._weak_market_score(snapshot, profile) >= 4:
            return False
        return (
            abs(snapshot.momentum_20) <= 0.035
            and abs(price_vs_sma20) <= 0.055
            and abs(trend) <= 0.028
            and snapshot.volatility_20 <= profile.max_volatility * 1.25
        )

    def _is_overextended(self, snapshot: MarketSnapshot, profile: StrategyProfile) -> bool:
        return (
            snapshot.rsi_14 >= min(88, profile.overbought_rsi + 2)
            or snapshot.change_pct >= max(0.025, profile.reversal_change)
            or self._price_vs_sma20(snapshot) >= 0.09
        )

    def _trend(self, snapshot: MarketSnapshot) -> float:
        return snapshot.sma_5 / snapshot.sma_20 - 1.0 if snapshot.sma_20 else 0.0

    def _price_vs_sma5(self, snapshot: MarketSnapshot) -> float:
        return snapshot.last_price / snapshot.sma_5 - 1.0 if snapshot.sma_5 else 0.0

    def _price_vs_sma20(self, snapshot: MarketSnapshot) -> float:
        return snapshot.last_price / snapshot.sma_20 - 1.0 if snapshot.sma_20 else 0.0

    def _profile_for_snapshot(self, snapshot: MarketSnapshot) -> StrategyProfile:
        if not self.auto_strategy:
            return self.profile
        return strategy_profile_with_overrides(
            infer_strategy_key(snapshot),
            max_buys_per_day=self.profile.max_buys_per_day,
        )

    def _fallback(self, snapshots: list[MarketSnapshot], reason: str) -> list[Signal]:
        return [
            Signal(signal.symbol, signal.side, signal.confidence, f"{signal.reason} ({reason}; fallback)")
            for signal in self.fallback.decide(snapshots)
        ]

    def _fallback_one(self, snapshot: MarketSnapshot, reason: str) -> Signal:
        signal = self.fallback.decide([snapshot])[0]
        return Signal(signal.symbol, signal.side, signal.confidence, f"{signal.reason} ({reason}; fallback)")

    def _normalize_model_name(self, model_name: str | None) -> str:
        raw = str(model_name or "").strip().lower().replace("-", "_")
        if raw in {"mlp", "neural_network", "neuralnetwork", "nn", "mlpclassifier"}:
            return "mlp"
        if raw in {"cnn", "cnn1d", "1d_cnn", "conv1d", "one_d_cnn", "onedcnn"}:
            return "cnn"
        if raw in {"xgboost", "xgb", "xgbclassifier"}:
            return "xgboost"
        if raw in {"hist_gradient_boosting", "histgradientboosting", "hgb", "boosting"}:
            return "hist_gradient_boosting"
        return "random_forest"

    def _normalize_training_mode(self, mode: Any) -> str:
        raw = str(mode or "").strip().lower().replace("-", "_")
        if raw in {"self", "self_first", "single_first", "single_auto", "own", "own_first"}:
            return "self"
        if raw in {"classified", "regime", "regime_classified", "by_regime", "year_regime"}:
            return "classified"
        if raw in {"mixed", "all", "all_mixed", "general_all"}:
            return "mixed"
        if raw in {"single", "single_symbol", "per_symbol", "symbol"}:
            return "single"
        if raw in {"auto", "adaptive"}:
            return "auto"
        return "general"

    def _normalize_target_mode(self, target: Any) -> str:
        raw = str(target or "").strip().lower().replace("-", "_")
        if raw in {"trend5", "five_class", "five_classes", "multiclass", "multi_class", "probability", "trend_probability"}:
            return "trend5"
        return "binary"

    def _custom_thresholds(self, default_buy: float, default_sell: float) -> tuple[float, float]:
        buy = self._param_float("ml_buy_threshold", default_buy, 0.01, 0.99)
        sell = self._param_float("ml_sell_threshold", default_sell, 0.01, 0.99)
        if sell >= buy:
            sell = max(0.01, buy - 0.05)
        return buy, sell

    def _xgb_preferred_device(self) -> str:
        requested = self._param_device("xgb_device", "auto")
        if requested == "cpu":
            return "cpu"
        if requested == "cuda":
            return "cuda" if _torch_cuda_available() else "cpu"
        return "cuda" if _torch_cuda_available() else "cpu"

    def _param_float(self, key: str, default: float, minimum: float, maximum: float) -> float:
        raw = self.params.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        return min(max(value, minimum), maximum)

    def _param_int(self, key: str, default: int, minimum: int, maximum: int) -> int:
        raw = self.params.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            return default
        return min(max(value, minimum), maximum)

    def _param_bool(self, key: str, default: bool) -> bool:
        raw = self.params.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on", "checked"}

    def _param_device(self, key: str, default: str) -> str:
        raw = str(self.params.get(key, default)).strip().lower()
        if raw in {"cuda", "gpu"}:
            return "cuda"
        if raw in {"cpu"}:
            return "cpu"
        return "auto"


class OpenAISignalProvider:
    def __init__(self, api_key: str, model: str) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def decide(self, snapshots: list[MarketSnapshot]) -> list[Signal]:
        data = [snapshot.__dict__ for snapshot in snapshots]
        prompt = (
            "You are a conservative trading signal generator for a paper-trading bot. "
            "Return only JSON with this shape: "
            '{"signals":[{"symbol":"AAPL","side":"buy|sell|hold","confidence":0.0,"reason":"short reason"}]}. '
            "Prefer hold when evidence is weak. Do not mention financial advice. "
            f"Market snapshots: {json.dumps(data, ensure_ascii=False)}"
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        signals: list[Signal] = []
        for item in payload.get("signals", []):
            side = Side(item.get("side", "hold").lower())
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
            signals.append(
                Signal(
                    symbol=str(item["symbol"]).upper(),
                    side=side,
                    confidence=confidence,
                    reason=str(item.get("reason", "AI signal")),
                )
            )
        return signals


class CodexSignalProvider:
    def __init__(
        self,
        codex_bin: str,
        model: str | None = None,
        timeout_seconds: int = 90,
        strategy: str | None = None,
        max_buys_per_day: int | None = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.profile = strategy_profile_with_overrides(strategy, max_buys_per_day=max_buys_per_day)
        self.fallback = HeuristicSignalProvider(self.profile.key, max_buys_per_day=max_buys_per_day)

    def decide(self, snapshots: list[MarketSnapshot]) -> list[Signal]:
        prompt = self._build_prompt(snapshots)
        command = [
            self.codex_bin,
            "exec",
            "--json",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-rules",
            "--ignore-user-config",
            "--disable",
            "plugins",
        ]
        if self.model:
            command.extend(["--model", self.model])
        command.append("-")

        try:
            result = subprocess.run(
                command,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except Exception as exc:
            return self._fallback(snapshots, f"Codex failed: {exc}")

        if result.returncode != 0:
            return self._fallback(snapshots, f"Codex exit {result.returncode}: {self._extract_error_message(result.stdout, result.stderr)}")

        try:
            payload = self._extract_payload(result.stdout)
            return self._guard_signals(snapshots, self._signals_from_payload(payload))
        except Exception as exc:
            return self._fallback(snapshots, f"Codex parse failed: {exc}")

    def _build_prompt(self, snapshots: list[MarketSnapshot]) -> str:
        data = [snapshot.__dict__ for snapshot in snapshots]
        return (
            f"You are a {self.profile.label} multi-factor signal generator for a paper-trading stock simulator.\n"
            "This is not real-money trading. Use the selected style strictly.\n"
            "Use trend, momentum_5, momentum_20, rsi_14, volatility_20, and volume_ratio_20 together.\n"
            f"Style={self.profile.label}: max {self.profile.max_buys_per_day} buy signal(s) per day; "
            f"buy only if RSI is roughly {self.profile.rsi_min:.0f}-{self.profile.rsi_max:.0f}, "
            f"volatility_20 below {self.profile.max_volatility:.2%}, and momentum is strong enough.\n"
            "Prefer hold when evidence is weak. Avoid chasing when RSI is above the style limit or one-day moves look exhausted.\n"
            "Prefer sell when trend breaks down, momentum turns negative, or risk looks elevated.\n"
            "If several symbols look similar, choose the strongest one and hold the weaker ones.\n"
            "Do not add repeated buy signals just because yesterday was also positive.\n"
            "Return only valid JSON with this exact shape:\n"
            '{"signals":[{"symbol":"600519.SH","side":"buy|sell|hold","confidence":0.0,"reason":"short Chinese reason"}]}\n'
            "Use only the provided numeric market snapshots. Do not use tools. Do not include markdown.\n"
            f"Market snapshots: {json.dumps(data, ensure_ascii=False)}\n"
        )

    def _extract_payload(self, output: str) -> dict:
        last_text: str | None = None
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item") or {}
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                last_text = item.get("text")
        if not last_text:
            raise ValueError("No Codex agent message found")
        return json.loads(self._strip_code_fence(last_text))

    def _extract_error_message(self, stdout: str, stderr: str) -> str:
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "error" and event.get("message"):
                return str(event["message"])
            error = event.get("error") or {}
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
        text = (stderr or stdout).strip().splitlines()
        return text[-1] if text else "unknown error"

    def _signals_from_payload(self, payload: dict) -> list[Signal]:
        signals: list[Signal] = []
        for item in payload.get("signals", []):
            side = Side(str(item.get("side", "hold")).lower())
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
            signals.append(
                Signal(
                    symbol=str(item["symbol"]).upper(),
                    side=side,
                    confidence=round(confidence, 3),
                    reason=str(item.get("reason", "Codex signal")),
                )
            )
        if not signals:
            raise ValueError("Codex returned no signals")
        return signals

    def _guard_signals(self, snapshots: list[MarketSnapshot], signals: list[Signal]) -> list[Signal]:
        snapshot_by_symbol = {snapshot.symbol: snapshot for snapshot in snapshots}
        guarded: list[Signal] = []
        buy_candidates: list[tuple[float, int]] = []
        for signal in signals:
            snapshot = snapshot_by_symbol.get(signal.symbol)
            if signal.side != Side.BUY or snapshot is None:
                guarded.append(signal)
                continue
            if not self._buy_allowed(snapshot):
                guarded.append(
                    Signal(
                        signal.symbol,
                        Side.HOLD,
                        min(signal.confidence, 0.55),
                        f"AI 买入被过滤：趋势/动量/波动条件不够强。原理由：{signal.reason}",
                    )
                )
                continue
            guarded.append(signal)
            buy_candidates.append((signal.confidence, len(guarded) - 1))

        if len(buy_candidates) > self.profile.max_buys_per_day:
            keep = {
                index
                for _, index in sorted(buy_candidates, key=lambda item: item[0], reverse=True)[
                    : self.profile.max_buys_per_day
                ]
            }
            guarded = [
                signal
                if index in keep or signal.side != Side.BUY
                else Signal(signal.symbol, Side.HOLD, 0.55, f"AI 买入被过滤：今日已有更强买入标的。原理由：{signal.reason}")
                for index, signal in enumerate(guarded)
            ]
        if not any(signal.side == Side.BUY for signal in guarded):
            guarded = self._add_local_strong_buy(snapshots, guarded)
        return guarded

    def _add_local_strong_buy(self, snapshots: list[MarketSnapshot], signals: list[Signal]) -> list[Signal]:
        local_buys = [
            signal
            for signal in self.fallback.decide(snapshots)
            if signal.side == Side.BUY and self._buy_allowed(next(item for item in snapshots if item.symbol == signal.symbol))
        ]
        if not local_buys:
            return signals
        best = max(local_buys, key=lambda signal: signal.confidence)
        updated: list[Signal] = []
        replaced = False
        for signal in signals:
            if signal.symbol == best.symbol:
                updated.append(
                    Signal(
                        best.symbol,
                        Side.BUY,
                        max(best.confidence, 0.72),
                        f"Codex未买入，本地强信号补充：{best.reason}",
                    )
                )
                replaced = True
            else:
                updated.append(signal)
        if not replaced:
            updated.append(
                Signal(
                    best.symbol,
                    Side.BUY,
                    max(best.confidence, 0.72),
                    f"Codex未买入，本地强信号补充：{best.reason}",
                )
            )
        return updated

    def _buy_allowed(self, snapshot: MarketSnapshot) -> bool:
        trend = snapshot.sma_5 / snapshot.sma_20 - 1.0 if snapshot.sma_20 else 0.0
        continuation = (
            trend > self.profile.trend_min
            and snapshot.momentum_5 > self.profile.momentum_5_min
            and snapshot.momentum_20 > -0.02
            and self.profile.rsi_min <= snapshot.rsi_14 <= self.profile.rsi_max
            and snapshot.volatility_20 < self.profile.max_volatility
        )
        reversal = (
            -0.025 <= trend <= max(0.004, self.profile.trend_min)
            and snapshot.momentum_5 > self.profile.reversal_momentum_5
            and snapshot.momentum_20 > 0
            and snapshot.change_pct > self.profile.reversal_change
            and max(35, self.profile.rsi_min - 3) <= snapshot.rsi_14 <= min(68, self.profile.rsi_max)
            and snapshot.volatility_20 < min(self.profile.max_volatility, 0.055)
        )
        return continuation or reversal

    def _fallback(self, snapshots: list[MarketSnapshot], reason: str) -> list[Signal]:
        signals = self.fallback.decide(snapshots)
        return [
            Signal(signal.symbol, signal.side, signal.confidence, f"{signal.reason} ({reason}; fallback)")
            for signal in signals
        ]

    def _strip_code_fence(self, text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
        return cleaned.strip()
