"""Reconcile 2026 losses to trades, asset marks, dividends and borrowing, read-only."""
import argparse
from pathlib import Path

import pandas as pd

from research_combinations import base_plan, get_histories, load
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.portfolio import Portfolio, statistics
from ai_stock_bot.v4.research import ResearchPortfolio, write_json
from ai_stock_bot.v4.schema import ASSETS


def main(args):
    histories = get_histories()
    store = Store(args.output / "isolated-store")
    plan = base_plan()
    panel = load(args.output / "models/hgb20_regularized/panel.json")
    models = {
        "frozen_2x": ResearchPortfolio(store, plan, histories, "multi"),
        "regularized_2x": ResearchPortfolio(store, plan, histories, "ml", panel, "trend"),
        "hold_1x": Portfolio(store, plan.model_copy(update={"research_multiple": 1, "drift_band": 0.}), histories, method="hold"),
    }
    dates = sorted(set.union(*(set(f.index) for f in histories.values())))
    prior = max(d for d in dates if d < pd.Timestamp("2026-01-01"))
    dates = [d for d in dates if pd.Timestamp("2026-01-01") <= d <= pd.Timestamp("2026-09-28")]
    result = {}
    for name, engine in models.items():
        engine.state["pending"] = engine.make_plan(prior)
        if engine.method == "hold":
            engine.state["hold_plan_key"] = engine.plan_key
        held = {s: 0 for s in histories}
        for date in dates:
            engine.step(date)
            for symbol in engine.account.positions:
                held[symbol] += 1
        attribution = []
        for symbol in histories:
            trades = [r for r in engine.account.trades if r["symbol"] == symbol]
            cashflows = sum((1 if r["side"] == "sell" else -1) * r["quantity"] * r["price"] - r["fee"] for r in trades)
            position = engine.account.positions.get(symbol, {"quantity": 0, "price": 0})
            pnl = cashflows + position["quantity"] * position["price"]
            attribution.append({"symbol": symbol, "name": ASSETS[symbol]["name"],
                "trading_pnl_excluding_dividends": pnl, "trades": len(trades), "held_sessions": held[symbol],
                "fees": sum(r["fee"] for r in trades),
                "market_total_return": float(histories[symbol].loc[dates[-1], "adjusted"] / histories[symbol].loc[prior, "adjusted"] - 1),
                "orders": trades})
        dividends = engine.state["dividends_paid"] + sum(r["amount"] for r in engine.state["receivables"])
        reconciled = sum(r["trading_pnl_excluding_dividends"] for r in attribution) + dividends - engine.state["interest_paid"]
        actual = engine.equity() - plan.starting_cash
        assert abs(reconciled - actual) < 1e-6, (name, reconciled, actual)
        stats = statistics(engine.state, plan.starting_cash)
        result[name] = {"statistics": stats, "assets": attribution, "dividends_earned": dividends,
            "interest": engine.state["interest_paid"], "reconciled_pnl": reconciled,
            "cooldown_days": sum(r["cooldown"] > 0 for r in engine.state["curve"]),
            "peak_exposure": max(r["exposure"] for r in engine.state["curve"]),
            "model_decisions": sum(r["status"] == "model" for r in engine.state["decisions"]),
            "decisions": engine.state["decisions"]}
        print(name, "pnl", round(actual, 2), "fees", round(stats["fees"], 2), "interest", round(stats["interest_paid"], 2), flush=True)
    write_json(args.output / "loss-attribution-2026-corrected.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
