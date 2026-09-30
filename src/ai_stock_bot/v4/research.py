"""Offline-only candidate signals. Execution is always the production Portfolio engine.

This module deliberately does not change Plan, its fingerprint, or frozen paper books.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from ..v2.features import FEATURES, features
from .actions import units
from .allocation import exposure_budget
from .forecast import sequence_array
from .portfolio import Portfolio, capped_weights, statistics
from .schema import ASSETS, Plan
from .model_recipes import ModelSpec, RiskNormalizedSpec, model_specs

RESEARCH_VERSION = "combinations-1"


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def target_scales(histories, horizon):
    """Only feature-date volatility scales labels; future volatility is forbidden."""
    return np.concatenate([(frame.adjusted.pct_change(fill_method=None).rolling(60).std(ddof=0)
        * np.sqrt(horizon)).clip(lower=.01).to_numpy() for frame in histories.values()])


def research_features(frame, symbol, extended=True):
    result = features(frame)[FEATURES].copy()
    if extended:
        close = frame.adjusted
        returns = close.pct_change(fill_method=None)
        for horizon in (60, 120, 252):
            result[f"momentum{horizon}"] = close.pct_change(horizon, fill_method=None)
        for horizon in (60, 120):
            result[f"price_sma{horizon}"] = close / close.rolling(horizon).mean() - 1
        result["volatility60"] = returns.rolling(60).std(ddof=0)
        result["vol_ratio"] = result.volatility / result.volatility60.replace(0, np.nan)
        result["drawdown252"] = close / close.rolling(252).max() - 1
        result["daily_range"] = (frame.high - frame.low) / frame.close
        for index, group in enumerate(dict.fromkeys(a["group"] for a in ASSETS.values())):
            result[f"group_{index}"] = float(ASSETS[symbol]["group"] == group)
    return result.replace([np.inf, -np.inf], np.nan)


def dataset(histories, spec):
    """Label maturity, not just the feature date, controls training eligibility."""
    arrays, targets, dates, ends, symbols = [], [], [], [], []
    for symbol, frame in histories.items():
        x = sequence_array(research_features(frame, symbol, spec.extended).to_numpy(), spec.window)
        normalized_open = frame.open * units(symbol, frame)
        cash = (frame.get("dividend", pd.Series(0., index=frame.index)) * units(symbol, frame)).cumsum()
        entitled = cash.shift(-spec.horizon - 1) - cash.shift(-1)
        # The fixed label cost is independent of subsequent execution stress scenarios.
        target = (normalized_open.shift(-spec.horizon - 1) * (1 - .0005) * (1 - .0003) + entitled) / (
            normalized_open.shift(-1) * (1 + .0005) * (1 + .0003)) - 1
        ending = pd.Series(frame.index, index=frame.index).shift(-spec.horizon - 1)
        arrays.append(x)
        targets.append(target.to_numpy())
        dates.append(frame.index.as_unit("ns").asi8)
        ends.append(pd.DatetimeIndex(ending).as_unit("ns").asi8)
        symbols.extend([symbol] * len(frame))
    return (np.concatenate(arrays), np.concatenate(targets), np.concatenate(dates),
            np.concatenate(ends), np.array(symbols))


def chronological_masks(dates, ends, cutoff, years):
    cutoff = pd.Timestamp(cutoff)
    start = cutoff - pd.DateOffset(years=years)
    mature = (dates >= start.value) & (dates < cutoff.value) & (ends < cutoff.value) & (ends >= dates)
    unique = np.unique(dates[mature])
    if len(unique) < 100:
        return mature, np.zeros_like(mature), np.zeros_like(mature), None
    split = unique[int(len(unique) * .8)]
    return mature, mature & (ends < split), mature & (dates >= split), split


def estimator(spec):
    if spec.model == "hgb":
        from sklearn.ensemble import HistGradientBoostingRegressor
        return HistGradientBoostingRegressor(max_iter=spec.iterations, max_leaf_nodes=spec.leaves,
            min_samples_leaf=spec.leaf_samples, learning_rate=.04, l2_regularization=spec.regularization,
            early_stopping=False, random_state=42)
    if spec.model == "ridge":
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.linear_model import Ridge
        return make_pipeline(StandardScaler(), Ridge(alpha=spec.regularization))
    if spec.model == "extra":
        from sklearn.ensemble import ExtraTreesRegressor
        return ExtraTreesRegressor(n_estimators=spec.iterations, max_depth=5,
            min_samples_leaf=spec.leaf_samples, max_features=.7, n_jobs=2, random_state=42)
    if spec.model == "xgb":
        from xgboost import XGBRegressor
        return XGBRegressor(n_estimators=spec.iterations, max_depth=3, learning_rate=.04,
            min_child_weight=30, reg_lambda=spec.regularization, subsample=.8, colsample_bytree=.8,
            tree_method="hist", n_jobs=2, random_state=42, objective="reg:squarederror")
    if spec.model == "cnn":
        from .cnn import TemporalCNN
        return TemporalCNN(epochs=spec.epochs)
    raise ValueError(spec.model)


def build_panel(histories, spec, output, identity, start="2019-12-01", end="2026-09-30"):
    """Cache only causal, month-out-of-sample predictions, with auditable fit receipts."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps({"version": RESEARCH_VERSION, "source": identity,
        "spec": asdict(spec)}, sort_keys=True).encode()).hexdigest()
    x, y, dates, ends, symbols = dataset(histories, spec)
    normalized = isinstance(spec, RiskNormalizedSpec)
    scales = target_scales(histories, spec.horizon) if normalized else np.ones(len(y))
    fit_y = y / scales
    finite = np.isfinite(x).all(axis=tuple(range(1, x.ndim)))
    finite &= np.isfinite(scales) & (scales > 0)
    receipts, rows = [], []
    for cutoff in pd.date_range(start, end, freq="MS"):
        path = output / f"{cutoff:%Y-%m}.json"
        if path.exists():
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if receipt["key"] != key:
                raise ValueError("Model inputs changed; use a new research directory")
        else:
            mature, train, valid, split = chronological_masks(dates, ends, cutoff, spec.years)
            mature &= finite & np.isfinite(y)
            train &= finite & np.isfinite(y)
            valid &= finite & np.isfinite(y)
            receipt = {"key": key, "cutoff": str(cutoff.date()), "samples": int(mature.sum()),
                "train_samples": int(train.sum()), "validation_samples": int(valid.sum()),
                "label_end": str(pd.Timestamp(ends[mature].max()).date()) if mature.any() else None,
                "status": "baseline", "predictions": [], "reason": "insufficient mature samples"}
            if mature.sum() >= 800 and train.sum() >= 400 and valid.sum() >= 50:
                with threadpool_limits(limits=2):
                    model = estimator(spec).fit(x[train], fit_y[train])
                    prediction = model.predict(x[valid]) * scales[valid]
                    mse = float(np.mean((prediction - y[valid]) ** 2))
                    zero = float(np.mean(y[valid] ** 2))
                    mean = float(np.mean((y[valid] - y[train].mean()) ** 2))
                    ic = pd.Series(prediction).rank().corr(pd.Series(y[valid]).rank())
                    # Daily IC is diagnostic, never a hindsight optimization target.
                    cross = pd.DataFrame({"date": dates[valid], "y": y[valid], "p": prediction})
                    daily_ic = []
                    for _, group in cross.groupby("date"):
                        if len(group) >= 3 and group.p.nunique() > 1 and group.y.nunique() > 1:
                            daily_ic.append(group.p.rank().corr(group.y.rank()))
                    accepted = mse < zero and np.isfinite(ic) and ic > 0
                    if normalized:
                        accepted = accepted and mse < mean
                    receipt.update(validation_start=str(pd.Timestamp(split).date()), validation_mse=mse,
                        zero_mse=zero, mean_mse=mean, rank_ic=float(ic) if np.isfinite(ic) else None,
                        cross_section_ic=float(np.mean(daily_ic)) if daily_ic else None,
                        status="model" if accepted else "baseline", reason="passed" if accepted else "validation rejected")
                    if accepted:
                        model = estimator(spec).fit(x[mature], fit_y[mature])
                        selected = finite & (dates >= cutoff.value) & (dates < (cutoff + pd.offsets.MonthBegin()).value)
                        predicted = model.predict(x[selected]) * scales[selected]
                        if not np.isfinite(predicted).all():
                            raise ValueError("Non-finite model output")
                        receipt["predictions"] = [{"date": str(pd.Timestamp(d).date()), "symbol": s, "value": float(p)}
                            for d, s, p in zip(dates[selected], symbols[selected], predicted)]
                        if spec.model == "cnn":
                            receipt["device"] = model.device
                    del model
            write_json(path, receipt)
            print(f"FIT {spec.model} {cutoff:%Y-%m} {receipt['status']} n={receipt['samples']}", flush=True)
        rows.extend(receipt["predictions"])
        receipts.append({k: v for k, v in receipt.items() if k != "predictions"})
    panel = {"key": key, "spec": asdict(spec), "rows": rows, "reports": receipts}
    write_json(output / "panel.json", panel)
    return panel


class ResearchPortfolio(Portfolio):
    def __init__(self, store, plan, histories, family, panel=None, policy="model"):
        super().__init__(store, plan, histories, method="rotation")
        self.family, self.policy = family, policy
        self.forecast_horizon = (panel or {}).get("spec", {}).get("horizon", plan.horizon)
        self.predicted = {(r["date"], r["symbol"]): r["value"] for r in (panel or {}).get("rows", [])}
        self.reports = {r["cutoff"][:7]: r for r in (panel or {}).get("reports", [])}
        self.extra = {}
        for symbol, frame in histories.items():
            close = frame.adjusted
            self.extra[symbol] = pd.DataFrame({
                "close": close, "upper": close.shift(1).rolling(60).max(),
                "lower": close.shift(1).rolling(20).min(),
                "z": (close - close.rolling(20).mean()) / close.rolling(20).std(ddof=0),
                "mean": close.rolling(20).mean(),
            })

    def make_plan(self, date, force=False):
        base = super().make_plan(date, force)
        if self.family in {"legacy", "multi"} or self.state["margin_exit"]:
            return base
        report = self.reports.get(str(date)[:7]) if self.family == "ml" else None
        if self.family == "ml" and (not report or report["status"] != "model"):
            base["status"] = "baseline"
            base["model"] = report
            return base
        rows = base["rows"]
        for row in rows:
            symbol = row["symbol"]
            history = self.extra[symbol].loc[:date]
            if history.empty or row["volatility"] is None:
                row["eligible"] = False
                continue
            f = history.iloc[-1]
            held = self.account.positions.get(symbol, {}).get("quantity", 0) > 0
            row["strength"] = 1.
            if self.family == "breakout":
                row["eligible"] = bool(f.close > (f.lower if held else f.upper))
            elif self.family == "pullback":
                row["eligible"] = bool(row["eligible"] and (f.close < f["mean"] if held else f.z < -1))
                row["score"] = float(-f.z) if np.isfinite(f.z) else -1.
            elif self.family == "allocation":
                row["eligible"] = bool(np.isfinite(f.close))
            elif self.family == "ml":
                estimate = self.predicted.get((str(date.date()), symbol))
                row["estimate"] = estimate
                valid = estimate is not None and estimate > 0
                row["eligible"] = bool(valid and (row["eligible"] if self.policy == "trend" else True))
                row["score"] = estimate / max(row["volatility"], .05) if estimate is not None else -1.
            row["weight"] = 0.
        chosen, groups = [], set()
        for row in sorted(rows, key=lambda r: (-r["score"], r["symbol"])):
            if row["eligible"] and row["group"] not in groups and self.plan.allocation[row["group"]] > 0 and len(chosen) < self.plan.holdings:
                chosen.append(row)
                groups.add(row["group"])
        weights = capped_weights([{**r, "preference": self.plan.allocation[r["group"]] / max(r["volatility"], .05)}
            for r in chosen], self.plan.max_exposure, self.plan.max_weight)
        estimates = {r["symbol"]: r.get("estimate") for r in rows} if report else None
        weights, budget = self.allocate(weights, date, estimates, self.forecast_horizon)
        if self.state["cooldown"] > 0:
            weights = {}
            budget.update(target_gross=0., scale=0.)
        for row in rows:
            row["weight"] = weights.get(row["symbol"], 0.)
        return {**base, "weights": weights, "rows": rows, "risk_budget": budget,
            "status": "model" if report else "baseline", "model": report, "reason": f"research:{self.family}:{self.policy}"}


def replay(store, plan, histories, start, end, family="multi", panel=None, policy="model"):
    common = sorted(set.union(*(set(histories[s].index) for s in plan.symbols)))
    dates = [d for d in common if pd.Timestamp(start) <= d <= pd.Timestamp(end)]
    previous = [d for d in common if d < pd.Timestamp(start)]
    if not dates or not previous:
        raise ValueError("Missing replay period or warmup")
    engine = ResearchPortfolio(store, plan, histories, family, panel, policy)
    engine.state["pending"] = engine.make_plan(previous[-1])
    for date in dates:
        engine.step(date)
    decisions = engine.state["decisions"]
    accepted = sum(r["status"] == "model" for r in decisions)
    model_dates = {r["date"] for r in decisions if r["status"] == "model"}
    initial = engine.reports.get(str(previous[-1])[:7])
    if initial and initial["status"] == "model":
        model_dates.add(str(previous[-1].date()))
    trades = engine.account.trades
    exit_cost = sum(max(plan.min_commission, p["quantity"] * p["price"] * plan.commission_rate)
        + p["quantity"] * p["price"] * plan.slippage_bps / 10000 for p in engine.account.positions.values())
    summary = statistics(engine.state, plan.starting_cash)
    liquidated = summary["end_equity"] - exit_cost
    years = ((dates[-1] - dates[0]).days + 1) / 365.25
    values = pd.Series([r["equity"] for r in engine.state["curve"]])
    rolling = values.pct_change(252).dropna()
    return {**summary, "start": str(dates[0].date()), "end": str(dates[-1].date()),
        "exit_cost": exit_cost, "liquidation_return": liquidated / plan.starting_cash - 1,
        "liquidation_cagr": (liquidated / plan.starting_cash) ** (1 / years) - 1 if liquidated > 0 else -1.,
        "rolling_year_min": float(rolling.min()) if len(rolling) else None,
        "rolling_year_positive": float((rolling > 0).mean()) if len(rolling) else None,
        "decision_count": len(decisions), "model_decisions": accepted,
        "model_signal_trades": sum(t.get("signal_date") in model_dates for t in trades),
        "rejections": len(engine.state["rejections"]), "curve": engine.state["curve"]}
