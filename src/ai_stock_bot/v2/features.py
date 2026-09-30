from __future__ import annotations

import numpy as np
import pandas as pd

from .market import regime
from .schema import AppConfig, Preset

FEATURE_VERSION = "causal-v2-1"
FEATURES = ["change", "trend", "price_sma5", "price_sma20", "momentum5", "momentum20", "rsi", "volatility", "volume_ratio", "range_position", "drawdown"]


def features(frame: pd.DataFrame) -> pd.DataFrame:
    close, volume = frame.adjusted.astype(float), frame.volume.astype(float)
    change = close.pct_change(fill_method=None)
    sma5, sma20 = close.rolling(5).mean(), close.rolling(20).mean()
    delta = close.diff()
    gain, loss = delta.clip(lower=0).rolling(14).mean(), (-delta.clip(upper=0)).rolling(14).mean()
    rsi = (gain / (gain + loss).replace(0, np.nan)).fillna(.5)
    low, high = close.rolling(20).min(), close.rolling(20).max()
    return pd.DataFrame({
        "change": change, "trend": sma5 / sma20 - 1,
        "price_sma5": close / sma5 - 1, "price_sma20": close / sma20 - 1,
        "momentum5": close.pct_change(5, fill_method=None), "momentum20": close.pct_change(20, fill_method=None),
        "rsi": rsi, "volatility": change.rolling(20).std(ddof=0),
        "volume_ratio": volume / volume.rolling(20).mean().replace(0, np.nan),
        "range_position": ((close - low) / (high - low).replace(0, np.nan)).fillna(.5),
        "drawdown": close / close.rolling(60, min_periods=20).max() - 1,
    }, index=frame.index).replace([np.inf, -np.inf], np.nan)


def online_regimes(f: pd.DataFrame):
    return np.where((f.momentum20 > .04) & (f.trend > 0), "bull", np.where((f.momentum20 < -.04) | (f.drawdown < -.12), "bear", "sideways"))


def label_returns(frame, preset, risk):
    if preset.label_basis == "close_return":
        return frame.adjusted.shift(-preset.horizon) / frame.adjusted - 1
    # Signal at close(t), enter open(t+1), exit open(t+1+horizon).
    # Use raw opens, as the execution engine does; dividends are not simulated.
    opening = frame.open.where(np.isfinite(frame.open) & (frame.open > 0))
    slip = risk.slippage_bps / 10000
    entry = opening.shift(-1) * (1 + slip) * (1 + risk.commission_rate)
    exit_value = opening.shift(-1 - preset.horizon) * (1 - slip) * (1 - risk.commission_rate)
    return exit_value / entry - 1


def training_samples(histories: dict[str, pd.DataFrame], config: AppConfig, preset: Preset, cutoff: pd.Timestamp, symbol: str | None, mode: str, profile: str):
    xs, ys, dates, used = [], [], [], []
    window = int(preset.params.get("window", 1)) if config.model == "cnn" else 1
    start = cutoff - pd.DateOffset(years=preset.training_years)
    expected = {"aggressive": "bull", "conservative": "bear", "balanced": "sideways", "custom": "sideways"}[profile]
    for name, full in histories.items():
        if mode == "self" and name != symbol:
            continue
        frame = full.loc[full.index < cutoff]
        if frame.empty:
            continue
        f = features(frame)
        data = f[FEATURES].to_numpy(dtype=np.float32)
        future = label_returns(frame, preset, config.risk).to_numpy()
        labels = np.digitize(future, [-.015, -.003, .003, .015]) if preset.target == "trend5" else (future > .001).astype(int)
        scenes = online_regimes(f).astype(object)
        for year, part in frame.groupby(frame.index.year):
            if int(year) < cutoff.year and part.index[0].month <= 2 and part.index[-1].month >= 11:
                label = config.annual_labels.get(f"{name}:{year}", regime(part))
                scenes[frame.index.year == year] = label
        eligible = np.asarray(frame.index >= start) & np.isfinite(future)
        eligible[:window - 1] = False
        eligible[max(0, len(frame) - preset.horizon):] = False
        if mode == "classified" and profile != "custom":
            eligible &= scenes == expected
        finite = np.isfinite(data).all(axis=1)
        if window > len(data):
            continue
        if window > 1:
            windows = np.lib.stride_tricks.sliding_window_view(data, window, axis=0).transpose(0, 2, 1)
            finite_windows = np.lib.stride_tricks.sliding_window_view(finite, window).all(axis=1)
            eligible[window - 1:] &= finite_windows
        else:
            eligible &= finite
        indices = np.flatnonzero(eligible)
        count = len(indices)
        if count:
            xs.append(windows[indices - window + 1] if window > 1 else data[indices])
            ys.append(labels[indices])
            dates.append(frame.index.as_unit("ns").asi8[indices])
            used.append({"symbol": name, "samples": count, "start": str(max(start, frame.index[0]).date()), "end": str(frame.index[-1].date())})
    if not xs:
        return np.empty((0, len(FEATURES))), np.empty(0, dtype=int), np.empty(0, dtype=np.int64), used
    x, y, timestamps = np.concatenate(xs), np.concatenate(ys), np.concatenate(dates)
    order = np.argsort(timestamps, kind="stable")
    return x[order], y[order], timestamps[order], used


def prediction_samples(frame, model, preset):
    data = features(frame)[FEATURES].to_numpy(dtype=np.float32)
    if model != "cnn":
        return data
    window = int(preset.params["window"])
    output = np.full((len(data), window, len(FEATURES)), np.nan, dtype=np.float32)
    for i in range(window - 1, len(data)):
        output[i] = data[i - window + 1:i + 1]
    return output
