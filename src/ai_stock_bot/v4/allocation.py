"""Causal daily signals and a portfolio-level exposure budget."""
import math

import numpy as np
import pandas as pd


def trend_features(frame):
    close = frame.adjusted
    returns = close.pct_change(fill_method=None)
    horizons = (20, 60, 120, 252)
    momentum = pd.DataFrame({str(n): close.pct_change(n, fill_method=None) for n in horizons})
    ready = momentum.notna().all(axis=1)
    # Each horizon controls one quarter of capital, rather than an all-or-nothing gate.
    strength = (momentum > 0).mean(axis=1).where(ready)
    normalized = momentum.div(pd.Series({str(n): math.sqrt(n / 252) for n in horizons}))
    return pd.DataFrame({"strength": strength, "score": normalized.mean(axis=1).where(ready),
                         "volatility": returns.rolling(60).std(ddof=0) * math.sqrt(252)})


def exposure_budget(plan, weights, returns, date, equity, peak):
    requested = sum(weights.values()) * plan.research_multiple
    if not weights or plan.exposure_policy == "fixed":
        return weights, {"policy": plan.exposure_policy, "requested_gross": requested,
                         "target_gross": requested, "scale": 1., "estimated_volatility": None}
    symbols = list(weights)
    history = returns.loc[:date, symbols].tail(60).dropna()
    if len(history) < 40:
        return {}, {"policy": plan.exposure_policy, "requested_gross": requested,
                    "target_gross": 0., "scale": 0., "estimated_volatility": None,
                    "reason": "组合风险样本不足，暂不建仓"}
    vector = np.array([weights[s] * plan.research_multiple for s in symbols])
    estimates = []
    for window in (20, 60):
        covariance = history.tail(window).cov().to_numpy() * 252
        # Shrink toward individual variances; do not trust a brief low-correlation spell.
        covariance = .75 * covariance + .25 * np.diag(np.diag(covariance))
        estimates.append(math.sqrt(max(0., float(vector @ covariance @ vector))))
    estimated = max(*estimates, .03 * requested)
    risk_scale = min(1., plan.target_volatility / estimated) if estimated > 0 else 0.
    drawdown = max(0., 1 - equity / peak) if peak > 0 else 1.
    # De-risk before the account's hard pause, without treating that line as a loss guarantee.
    drawdown_scale = min(1., max(.25, 2 * (1 - drawdown / plan.drawdown_pause)))
    scale = min(risk_scale, drawdown_scale)
    adjusted = {s: w * scale for s, w in weights.items()}
    return adjusted, {"policy": plan.exposure_policy, "requested_gross": requested,
                      "target_gross": requested * scale, "scale": scale,
                      "estimated_volatility": estimated, "target_volatility": plan.target_volatility,
                      "risk_scale": risk_scale, "drawdown_scale": drawdown_scale,
                      "reason": "按组合波动和当前回撤降低目标仓位" if scale < 1 else "风险预算内运行"}
