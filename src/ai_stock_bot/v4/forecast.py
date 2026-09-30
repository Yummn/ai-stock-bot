from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from ..v2.features import FEATURES, features
from .schema import ENGINE_VERSION
from .actions import units
from .cnn import fit_cnn


def read_cache(path, expected):
    if not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as cache:
            report = json.loads(str(cache['report']))
            predicted = cache['predicted']
        if predicted.shape != (expected,) or report.get('status') not in {'model','baseline'}:
            raise ValueError('Incomplete model cache')
        return report,predicted
    except (ValueError, OSError, KeyError, zipfile.BadZipFile):
        path.unlink(missing_ok=True)
        return None


def sequence_array(values, window):
    data = np.asarray(values, dtype=np.float64 if window == 1 else np.float32)
    if window == 1:
        return data
    result = np.full((len(data), window, data.shape[1]), np.nan, dtype=np.float32)
    if len(data) >= window:
        result[window - 1:] = np.lib.stride_tricks.sliding_window_view(
            data, window, axis=0
        ).transpose(0, 2, 1)
    return result


def samples(histories, plan, cutoff, window=1, classification=False):
    xs, ys, dates, ends = [], [], [], []
    start = pd.Timestamp(cutoff) - pd.DateOffset(years=plan.training_years)
    slip = plan.slippage_bps / 10000
    for symbol, frame in histories.items():
        past = frame.loc[frame.index < pd.Timestamp(cutoff)]
        x = features(past)[FEATURES]
        if classification:
            # Preserve the old binary close-return target; execution still uses next open.
            future = past.adjusted.shift(-plan.horizon) / past.adjusted - 1
            y = (future > .001).astype(float).where(future.notna())
            label_end = pd.Series(past.index, index=past.index).shift(-plan.horizon)
        else:
            # Labels are executable next-open returns; both opens must precede cutoff.
            normalized_open = past.open * units(symbol, past)
            dividends = (past.get('dividend', pd.Series(0.,index=past.index)) * units(symbol,past)).cumsum()
            entitled = dividends.shift(-plan.horizon - 1) - dividends.shift(-1)
            y = (normalized_open.shift(-plan.horizon - 1) * (1 - slip) * (1 - plan.commission_rate) + entitled) / (normalized_open.shift(-1) * (1 + slip) * (1 + plan.commission_rate)) - 1
            label_end = pd.Series(past.index, index=past.index).shift(-plan.horizon - 1)
        data = sequence_array(x.to_numpy(), window)
        finite = np.isfinite(data).all(axis=tuple(range(1, data.ndim)))
        valid = (past.index >= start) & finite & np.isfinite(y) & label_end.notna()
        xs.append(data[valid])
        ys.append(y.loc[valid].to_numpy())
        dates.append(past.index[valid].as_unit("ns").asi8)
        ends.append(pd.DatetimeIndex(label_end.loc[valid]).as_unit("ns").asi8)
    if not xs:
        shape = (0, len(FEATURES)) if window == 1 else (0, window, len(FEATURES))
        return np.empty(shape), np.array([]), np.array([], dtype=np.int64), np.array([], dtype=np.int64)
    return np.concatenate(xs), np.concatenate(ys), np.concatenate(dates), np.concatenate(ends)


class Forecasts:
    def __init__(self, store, histories, plan, progress=lambda *args: None, model="ml"):
        self.store, self.histories, self.plan, self.progress = store, histories, plan, progress
        self.model = model
        self.classification = model.endswith("_class")
        self.cnn = model.startswith("cnn")
        self.window = plan.cnn_window if self.cnn else 1
        self.frames = {s: features(f)[FEATURES] for s, f in histories.items()}
        self.inputs = {s: sequence_array(f.to_numpy(), self.window) for s, f in self.frames.items()}
        self.month = None
        self.predictions = {}
        self.report = None
        self.reports = []

    def at(self, date):
        month = str(date)[:7]
        if self.month != month:
            # The same cutoff is used after a live restart and in historical replay.
            self.fit(pd.Timestamp(date).to_period('M').start_time)
            self.month = month
        return {s: float(v.loc[date]) for s, v in self.predictions.items() if date in v.index and np.isfinite(v.loc[date])}, self.report

    def fit(self, cutoff):
        x, y, dates, ends = samples(self.histories, self.plan, cutoff, self.window, self.classification)
        report = {"cutoff": str(cutoff.date()), "samples": len(y), "label_end": str(pd.Timestamp(ends.max()).date()) if len(ends) else None,
                  "status": "baseline", "reason": "训练样本不足", "model": self.model,
                  "target": (f"未来 {self.plan.horizon} 个交易日收盘涨幅超过 0.1% 的概率" if self.classification else
                             f"未来 {self.plan.horizon} 个交易日的净持有收益（最低佣金另计）")}
        self.predictions = {}
        if len(y) >= self.plan.min_samples:
            unique = np.unique(dates)
            split = unique[max(1, int(len(unique) * .8))]
            train, valid = ends < split, dates >= split
            report.update(train_samples=int(train.sum()), validation_samples=int(valid.sum()))
            if train.sum() >= self.plan.min_samples // 2 and valid.sum() >= 50 and (not self.classification or np.unique(y[train]).size == 2):
                params = ({"window": self.window, "epochs": self.plan.cnn_epochs,
                           "channels": 16, "kernel": 3, "seed": 42}
                          if self.cnn else
                          {"max_iter": self.plan.model_iterations, "max_leaf_nodes": self.plan.model_leaves,
                           "learning_rate": self.plan.model_learning_rate, "l2_regularization": 1.,
                           "min_samples_leaf": 30, "early_stopping": False, "random_state": 42})
                keys = {"engine": ENGINE_VERSION, "model": self.model, "cutoff": str(cutoff), "params": params}
                key = hashlib.sha256(json.dumps(keys, sort_keys=True).encode() + x.tobytes() + y.tobytes() + dates.tobytes() + ends.tobytes()).hexdigest()
                directory = self.store.root / "portfolio_models"
                directory.mkdir(exist_ok=True)
                path = directory / f"{key}.npz"
                data = np.concatenate(list(self.inputs.values()))
                # Prediction inputs are part of the key; a later data revision cannot reuse old outputs.
                path = directory / f"{key}-{hashlib.sha256(data.tobytes()).hexdigest()[:16]}.npz"
                cached = read_cache(path,len(data))
                if cached is not None:
                    report,predicted = cached
                    report["cached"] = True
                else:
                    self.progress(15, f"滚动训练 {cutoff.date()} · {len(y)} 条已完成标签")
                    if self.cnn:
                        model, fallback = fit_cnn(x[train], y[train], self.plan.cnn_epochs, classifier=self.classification)
                        report.update(device=model.device, device_fallback=fallback)
                        validation = model.predict(x[valid])
                    else:
                        with threadpool_limits(limits=2):
                            from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
                            estimator = HistGradientBoostingClassifier if self.classification else HistGradientBoostingRegressor
                            model = estimator(**params).fit(x[train], y[train])
                            validation = model.predict_proba(x[valid])[:, 1] if self.classification else model.predict(x[valid])
                    mse = float(np.mean((validation - y[valid]) ** 2))
                    baseline = float(y[train].mean()) if self.classification else 0.
                    baseline_mse = float(np.mean((baseline - y[valid]) ** 2))
                    rank_ic = float(pd.Series(validation).rank().corr(pd.Series(y[valid]).rank()))
                    report.update(validation_mse=mse, zero_mse=baseline_mse, rank_ic=rank_ic if np.isfinite(rank_ic) else None,
                                  validation_start=str(pd.Timestamp(split).date()), cached=False,
                                  validation_metric="Brier" if self.classification else "MSE")
                    accepted = mse < baseline_mse and np.isfinite(rank_ic) and rank_ic > 0
                    report.update(status="model" if accepted else "baseline", reason="时间验证通过" if accepted else "预测误差或排序未优于基线，使用趋势组合")
                    predicted = np.full(len(data), np.nan)
                    if accepted:
                        if self.cnn:
                            model, fallback = fit_cnn(x, y, self.plan.cnn_epochs, classifier=self.classification)
                            report.update(device=model.device, device_fallback=fallback or report.get("device_fallback"))
                        else:
                            with threadpool_limits(limits=2):
                                model.fit(x, y)
                        finite = np.isfinite(data).all(axis=tuple(range(1, data.ndim)))
                        predicted[finite] = (model.predict(data[finite]) if self.cnn or not self.classification
                                             else model.predict_proba(data[finite])[:, 1])
                    with tempfile.NamedTemporaryFile(dir=directory,suffix='.tmp',delete=False) as output:
                        temporary = Path(output.name)
                        np.savez_compressed(output, predicted=predicted, report=np.array(json.dumps(report)))
                    temporary.replace(path)
                if report["status"] == "model":
                    offset = 0
                    for symbol, frame in self.frames.items():
                        self.predictions[symbol] = pd.Series(predicted[offset:offset + len(frame)], index=frame.index)
                        offset += len(frame)
        self.report = report
        self.reports.append(report)
