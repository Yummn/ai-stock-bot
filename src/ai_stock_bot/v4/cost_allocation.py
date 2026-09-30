"""Versioned, causal allocation with explicit financing and turnover penalties.

The convex objective follows the return/risk/trading/holding-cost separation in
Cvxportfolio. Execution remains in our next-open, lot- and settlement-aware engine.
"""
import numpy as np
from scipy.optimize import minimize

from .schema import ASSETS


def cost_allocation(plan, weights, returns, date, equity, positions, estimates=None, horizon=None):
    multiple = plan.research_multiple
    requested = sum(weights.values()) * multiple
    receipt = {"policy": "cost-aware-v1", "requested_gross": requested,
               "status": "cash", "target_gross": 0., "forecast_is_estimate": True}
    if not weights or equity <= 0:
        return {}, receipt
    symbols = list(weights)
    history = returns.loc[:date, symbols].tail(252).dropna()
    if len(history) < 60:
        return {}, {**receipt, "reason": "成本决策历史不足，保留现金"}
    covariance = history.tail(60).cov().to_numpy() * 252
    covariance = .75 * covariance + .25 * np.diag(np.diag(covariance))
    if not np.isfinite(covariance).all():
        return {}, {**receipt, "reason": "风险估计无效，保留现金"}
    # Shrink noisy trailing returns toward cash; no future bar or label is used.
    mu = np.mean([history.tail(n).mean().to_numpy() * 252
                  for n in (60, 120, 252) if len(history) >= n], axis=0) * .5
    for i, symbol in enumerate(symbols):
        value = (estimates or {}).get(symbol)
        if value is not None and np.isfinite(value):
            mu[i] = .5 * mu[i] + .5 * float(value) * 252 / (horizon or plan.horizon)
    upper = np.array([weights[s] * (1 if ASSETS[s]["group"] == "国债" else multiple)
                      for s in symbols])
    current = np.array([positions.get(s, {}).get("quantity", 0)
                        * positions.get(s, {}).get("price", 0.) / equity for s in symbols])
    periods = {"daily": 252, "weekly": 52, "monthly": 12}[plan.rebalance]
    cost = (np.maximum(plan.commission_rate,
                       plan.min_commission / np.maximum(equity * upper, 1.))
            + plan.slippage_bps / 10000) * periods
    n = len(symbols)
    # Epigraph variables make absolute turnover and positive debt differentiable.
    matrix = np.zeros((2 * n + 1, 2 * n + 1))
    matrix[:n, :n] = -np.eye(n)
    matrix[:n, n:2*n] = np.eye(n)
    matrix[n:2*n, :n] = np.eye(n)
    matrix[n:2*n, n:2*n] = np.eye(n)
    matrix[-1, :n], matrix[-1, -1] = -1., 1.
    offset = np.r_[current, -current, 1.]
    risk_aversion = .5 / plan.target_volatility

    def objective(z):
        w = z[:n]
        return (.5 * risk_aversion * (w @ covariance @ w) - mu @ w
                + cost @ z[n:2*n] + plan.research_annual_rate * z[-1])

    def gradient(z):
        return np.r_[risk_aversion * covariance @ z[:n] - mu,
                     cost, plan.research_annual_rate]

    initial = np.minimum(current, upper)
    z = np.r_[initial, np.abs(initial-current), max(0., initial.sum()-1)]
    result = minimize(objective, z, jac=gradient, method="SLSQP",
        bounds=[(0., float(x)) for x in upper] + [(0., None)] * (n+1),
        constraints={"type": "ineq", "fun": lambda x: matrix @ x + offset,
                     "jac": lambda x: matrix}, options={"maxiter": 100, "ftol": 1e-10})
    valid = (result.success and np.isfinite(result.x).all()
             and np.min(matrix @ result.x + offset) >= -1e-7
             and np.all(result.x[:n] <= upper + 1e-7) and np.all(result.x[:n] >= -1e-7))
    if not valid:
        return {}, {**receipt, "status": "failed", "reason": "成本决策求解失败，保留现金",
                    "solver_message": str(result.message)}
    w = np.clip(result.x[:n], 0., upper)
    w[w < 1e-7] = 0.
    receipt.update(status="optimized", target_gross=float(w.sum()),
        estimated_return=float(mu @ w), estimated_volatility=float(np.sqrt(max(0., w @ covariance @ w))),
        estimated_annual_interest=float(max(0., w.sum()-1) * plan.research_annual_rate),
        expected_returns=dict(zip(symbols, map(float, mu))),
        reason="扣除预估融资和调仓成本后分配仓位；国债不放大，未分配部分保留现金")
    return {s: float(v / multiple) for s, v in zip(symbols, w) if v > 0}, receipt
