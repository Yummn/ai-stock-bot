from __future__ import annotations

import math

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

from .intraday_quality import continuous, first_executable


def features(rows, interval):
    scale = 2 if interval == "30m" else 1
    count = 20 * scale
    if len(rows) < count or not continuous(rows[-count:], interval):
        return None
    window = rows[-count:]
    close = np.array([float(r["close"]) for r in window])
    volume = np.array([float(r["volume"]) for r in window])
    if (volume <= 0).any():
        return None
    returns = np.diff(np.log(close))
    high = max(float(r["high"]) for r in window)
    low = min(float(r["low"]) for r in window)
    values = [close[-1] / close[-1 - lag * scale] - 1 for lag in (1, 2, 4, 8)]
    values += [close[-1] / np.mean(close[-period * scale:]) - 1 for period in (4, 8, 20)]
    values += [float(np.std(returns)), (high - low) / close[-1],
               (close[-1] - low) / max(high - low, 1e-9),
               volume[-1] / max(float(np.mean(volume)), 1.) - 1.]
    return values if all(math.isfinite(v) for v in values) else None


def training_rows(rows, plan):
    horizon = plan.intraday_horizon_hours * (2 if plan.research_interval == "30m" else 1)
    result = []
    for i in range(20 * (2 if plan.research_interval == "30m" else 1) - 1, len(rows)):
        vector = features(rows[:i+1], plan.research_interval)
        entry = first_executable(rows, i, plan.research_interval)
        if vector is None or entry is None or entry + horizon >= len(rows):
            continue
        exit_index = entry + horizon
        if not continuous(rows[i:exit_index+1], plan.research_interval):
            continue
        gross = float(rows[exit_index]["open"]) / float(rows[entry]["open"]) - 1
        result.append({"x": vector, "y": gross, "feature_time": rows[i]["stamp"],
                       "entry_time": rows[entry]["stamp"], "label_end": rows[exit_index]["stamp"]})
    return result


class IntradayForecast:
    """Daily rolling, purged-at-midnight regression; no random validation split."""
    def __init__(self, plan):
        self.plan = plan
        self.day = None
        self.models = []
        self.report = {}

    def at(self, rows):
        current = rows[-1]["stamp"]
        day = current[:10]
        if self.day != day:
            self.day = day
            self.models = []
            prefix = [r for r in rows if r["stamp"][:10] < day]
            days = sorted({r["stamp"][:10] for r in prefix})
            if days:
                start = days[-min(len(days), self.plan.intraday_training_sessions)]
                prefix = [r for r in prefix if r["stamp"][:10] >= start]
            examples = training_rows(prefix, self.plan)
            self.report = {"fit_day": day, "samples": len(examples), "status": "insufficient",
                           "last_label_end": examples[-1]["label_end"] if examples else None}
            if len(examples) >= self.plan.intraday_min_samples:
                split = int(len(examples) * .75)
                calibration = examples[split:]
                # Purge labels extending into the chronological calibration window.
                fitting = [r for r in examples[:split] if r["label_end"] < calibration[0]["feature_time"]]
                self.report.update(fit_samples=len(fitting), calibration_samples=len(calibration),
                                   fit_label_end=fitting[-1]["label_end"] if fitting else None,
                                   calibration_start=calibration[0]["feature_time"])
                if len(fitting) < 40 or len(calibration) < 20:
                    return {**self.report, "gross_prediction": None, "disagreement": None}
                x = np.array([item["x"] for item in fitting])
                # Percent units make regularization meaningful for small daily returns.
                y = np.array([item["y"] * 100 for item in fitting])
                with threadpool_limits(limits=2):
                    for seed in (17, 41, 73):
                        model = HistGradientBoostingRegressor(
                            max_iter=60, max_leaf_nodes=5, max_depth=3,
                            min_samples_leaf=max(20, len(fitting) // 10),
                            l2_regularization=10., learning_rate=.04, early_stopping=False,
                            random_state=seed)
                        # Deterministic time-block subsamples expose some fit instability.
                        indices = np.arange(len(fitting))
                        use = (indices // 8 + seed) % 5 != 0
                        model.fit(x[use], y[use])
                        self.models.append(model)
                    validation_x = np.array([item["x"] for item in calibration])
                    actual = np.array([item["y"] for item in calibration])
                    predicted = np.mean([model.predict(validation_x) / 100 for model in self.models], axis=0)
                    mae = float(np.mean(np.abs(predicted - actual)))
                    zero_mae = float(np.mean(np.abs(actual)))
                self.report.update(calibration_mae=mae, zero_forecast_mae=zero_mae,
                                   calibration_bias=float(np.mean(predicted - actual)),
                                   status="model" if mae < zero_mae else "no_edge")
        vector = features(rows, self.plan.research_interval)
        if not self.models or vector is None or self.report["status"] != "model":
            return {**self.report, "status": "missing_features" if vector is None else self.report["status"],
                    "gross_prediction": None, "disagreement": None}
        with threadpool_limits(limits=2):
            estimates = np.array([model.predict([vector])[0] / 100 for model in self.models])
        return {**self.report, "gross_prediction": float(estimates.mean()),
                "disagreement": float(estimates.std())}


class ForecastTape:
    """Reuse causal predictions only within one immutable research snapshot."""
    def __init__(self, rows, plan, progress=lambda *args: None):
        model = IntradayForecast(plan)
        self.predictions = {}
        for index, row in enumerate(rows):
            if index == 0 or rows[index-1]["stamp"][:10] != row["stamp"][:10]:
                progress(5 + 30 * index / len(rows), f"训练盘中回归：{row['stamp'][:10]}")
            self.predictions[row["stamp"]] = model.at(rows[:index+1])

    def at(self, rows):
        return self.predictions[rows[-1]["stamp"]]
