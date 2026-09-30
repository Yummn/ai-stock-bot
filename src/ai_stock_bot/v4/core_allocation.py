"""Versioned unlevered core/tilt allocation, with cash as the residual asset."""
import numpy as np
from scipy.optimize import minimize

from .schema import ASSETS


def core_allocation(plan, rows, returns, date, equity, peak, positions, estimates=None, horizon=20):
    receipt = {"policy": plan.allocation_policy, "status": "cash", "target_gross": 0.,
               "forecast_is_estimate": True, "cash_return_assumption": 0.}
    if not rows or equity <= 0:
        return {}, receipt
    symbols = [r["symbol"] for r in rows]
    history = returns.loc[:date, symbols].tail(252).dropna()
    if len(history) < 60:
        return {}, {**receipt, "reason": "核心组合风险样本不足，保留现金"}
    covariances = []
    for window in (20, 60):
        cov = history.tail(window).cov().to_numpy()*252
        covariances.append(.75*cov + .25*np.diag(np.diag(cov)))
    if not all(np.isfinite(c).all() for c in covariances):
        return {}, {**receipt, "reason": "核心组合风险估计无效，保留现金"}
    mu = np.mean([history.tail(n).mean().to_numpy()*252 for n in (60, 120, 252) if len(history) >= n], axis=0)*.5
    for i, symbol in enumerate(symbols):
        forecast = (estimates or {}).get(symbol)
        if forecast is not None and np.isfinite(forecast):
            mu[i] = .5*mu[i] + .5*forecast*252/horizon
    # The core is a soft anchor, not a compulsory holding or an upper bound.
    core = np.array([plan.max_exposure*plan.allocation[ASSETS[r["symbol"]]["group"]]*r["strength"] for r in rows])
    caps = np.full(len(rows), plan.max_weight)
    current = np.array([positions.get(s, {}).get("quantity", 0)*positions.get(s, {}).get("price", 0.)/equity for s in symbols])
    risky = np.array([float(ASSETS[s]["group"] != "国债") for s in symbols])
    risk_limit = .35 if plan.allocation_policy == "defensive_v1" else plan.max_exposure
    drawdown = max(0., 1-equity/peak) if peak > 0 else 1.
    scale = min(1., max(.25, 2*(1-drawdown/plan.drawdown_pause)))
    limit = plan.target_volatility*scale
    n = len(rows)
    periods = {"monthly": 12, "weekly": 52, "daily": 252}[plan.rebalance]
    costs = (np.maximum(plan.commission_rate, plan.min_commission/np.maximum(equity*caps, 1.)) + plan.slippage_bps/10000)*periods
    matrix = np.zeros((2*n+2, 2*n))
    matrix[:n, :n], matrix[:n, n:] = -np.eye(n), np.eye(n)
    matrix[n:2*n, :n], matrix[n:2*n, n:] = np.eye(n), np.eye(n)
    matrix[-2, :n], matrix[-1, :n] = -1., -risky
    offset = np.r_[current, -current, plan.max_exposure*scale, risk_limit*scale]
    covariance = covariances[-1]

    def objective(z):
        w = z[:n]
        return (.5*plan.risk_aversion*(w@covariance@w) - mu@w
                + .5*plan.core_anchor*np.sum((w-core)**2) + costs@z[n:])

    def gradient(z):
        return np.r_[plan.risk_aversion*covariance@z[:n]-mu+plan.core_anchor*(z[:n]-core), costs]

    constraints = [{"type": "ineq", "fun": lambda z: matrix@z+offset, "jac": lambda z: matrix}]
    for cov in covariances:
        constraints.append({"type": "ineq", "fun": lambda z, c=cov: limit**2-z[:n]@c@z[:n],
                            "jac": lambda z, c=cov: np.r_[-2*c@z[:n], np.zeros(n)]})
    result = minimize(objective, np.r_[np.zeros(n), current], jac=gradient, method="SLSQP",
        bounds=[(0., float(v)) for v in caps]+[(0., None)]*n, constraints=constraints,
        options={"maxiter": 200, "ftol": 1e-10})
    if (not result.success or not np.isfinite(result.x).all() or np.min(matrix@result.x+offset) < -1e-7
            or any(result.x[:n]@c@result.x[:n] > limit**2+1e-7 for c in covariances)):
        return {}, {**receipt, "status": "failed", "reason": "核心组合求解失败，保留现金", "solver_message": str(result.message)}
    weights = np.clip(result.x[:n], 0, caps)
    weights[weights < 1e-7] = 0.
    receipt.update(status="optimized", target_gross=float(weights.sum()),
        estimated_volatility=float(max(np.sqrt(max(0., weights@c@weights)) for c in covariances)),
        target_volatility=limit, risky_exposure=float(risky@weights), risky_limit=risk_limit*scale,
        core_weights=dict(zip(symbols, map(float, core))), expected_returns=dict(zip(symbols, map(float, mu))),
        reason="核心分散配置与模型倾斜共同优化；不借款，未投入部分保留现金，国债仍计入波动风险")
    return {s: float(w) for s, w in zip(symbols, weights) if w > 0}, receipt
