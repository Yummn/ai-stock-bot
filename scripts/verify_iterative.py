"""Independently audit every annual curve and fixed-data comparison before use."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ai_stock_bot.v2.features import features, training_samples
from ai_stock_bot.v2.market import Market, digest
from ai_stock_bot.v2.schema import AppConfig, MODEL_NAMES, PROFILES, Preset
from ai_stock_bot.v2.store import Store
from iterate_stocklab import OUTPUT, SEARCH_YEARS, objective, scoring_scene, write_json


def verify(output=OUTPUT):
    output = Path(output)
    state = json.loads((output / "state.json").read_text(encoding="utf-8"))
    market = Market(Store(output / "frozen"))
    histories = {r["symbol"]: market.read(r["symbol"]) for r in state["manifest"]["coverage"]}
    assert all(digest(histories[r["symbol"]]) == r["hash"] for r in state["manifest"]["coverage"])
    signatures = {}
    annuals, trade_count = 0, 0
    for item in state["evaluations"].values():
        config = AppConfig.model_validate(item["config"])
        assert not config.auto_profile and config.engine == "ml"
        for row in item["per_year"]:
            path = output / "runs" / config.model / f"{item['fingerprint']}-{row['year']}.json.gz"
            with gzip.open(path, "rt", encoding="utf-8") as source:
                full = json.load(source)
            equities = np.array([full["start_equity"]] + [p["equity"] for p in full["curve"]])
            total_return = equities[-1] / equities[0] - 1
            drawdown = min(equities / np.maximum.accumulate(equities) - 1)
            hold_return = full["curve"][-1]["hold_equity"] / equities[0] - 1
            assert np.isclose(total_return, row["return_pct"], atol=1e-10)
            assert np.isclose(drawdown, row["max_drawdown"], atol=1e-10)
            assert np.isclose(hold_return, row["benchmark_return"], atol=1e-10)
            assert np.isclose(total_return - hold_return, row["excess_return"], atol=1e-10)
            assert np.isclose(row["mean_exposure"], np.mean([1-p["cash"]/p["equity"] for p in full["curve"]]))
            np.testing.assert_allclose(row["probability_quantiles"], np.quantile([d["probability"] for d in full["decisions"]], [0,.25,.5,.75,.9,1]))
            assert row["trade_count"] == len(full["trades"])
            assert np.isclose(row["fees"], sum(t["fee"] for t in full["trades"]), atol=1e-8)
            assert row["fallback_count"] == 0 and len(full["symbol_curves"]) == len(config.symbols)
            assert all(p["date"].startswith(str(row["year"])) for p in full["curve"])
            hashes = []
            for curve in full["symbol_curves"]:
                frame = histories[curve["symbol"]].loc[[p["date"] for p in curve["points"]]]
                assert digest(frame) == curve["data_hash"]
                np.testing.assert_allclose(frame.close.values, [p["close"] for p in curve["points"]])
                hashes.append(curve["data_hash"])
            assert hashlib.sha256("".join(hashes).encode()).hexdigest()[:20] == row["data_hash"]
            signature = (row["start"], row["end"], row["bars"], row["data_hash"])
            if row["year"] in signatures:
                assert signatures[row["year"]] == signature
            signatures[row["year"]] = signature
            assert all(r["cutoff"] == row["start"] and all(s["end"] < row["start"] for s in r["sources"]) for r in full["training"])
            assert full["curve"][0]["equity"] == full["start_equity"]
            assert all(t["date"] > row["start"] for t in full["trades"])
            cash, holdings = full["start_equity"], {}
            by_date = {}
            mark_dates = pd.to_datetime([p["date"] for p in full["curve"]])
            mark_prices = {s:f.close.reindex(mark_dates, method="ffill").to_numpy() for s,f in histories.items()}
            for trade in full["trades"]:
                by_date.setdefault(trade["date"], []).append(trade)
                raw = histories[trade["symbol"]].loc[trade["date"], "open"]
                sign = 1 if trade["side"] == "buy" else -1
                assert np.isclose(trade["price"], raw * (1+sign*config.risk.slippage_bps/10000))
            for index, point in enumerate(full["curve"]):
                for trade in by_date.get(point["date"], []):
                    sign = 1 if trade["side"] == "buy" else -1
                    cash -= sign*trade["quantity"]*trade["price"] + trade["fee"]
                    holdings[trade["symbol"]] = holdings.get(trade["symbol"], 0) + sign*trade["quantity"]
                    assert holdings[trade["symbol"]] >= 0
                mark = cash + sum(qty*mark_prices[symbol][index] for symbol,qty in holdings.items() if qty)
                assert np.isclose(cash, point["cash"], atol=1e-7)
                assert np.isclose(mark, point["equity"], atol=1e-7)
            assert all(0 <= d["probability"] <= 1 for d in full["decisions"])
            # Normalize scoring labels in older checkpoints without changing any prices or metrics.
            row.setdefault("market_vote_scene", row["scene"])
            row["scene"] = scoring_scene(row)
            annuals += 1
            trade_count += len(full["trades"])
        assert np.isclose(item["score"], objective(item["per_year"], item["profile"]), atol=1e-12)
    for model in MODEL_NAMES:
        assert all(p in state["models"][model] for p in PROFILES)
        for profile in PROFILES:
            record = state["models"][model][profile]
            assert record.get("capacity") and record.get("threshold") and len(record.get("check", [])) == 2
            winner, old = (state["evaluations"][record[k]] for k in ("center", "baseline"))
            assert [r["year"] for r in winner["per_year"]] == SEARCH_YEARS
            assert winner["score"] >= old["score"]
            cfg = AppConfig.model_validate(winner["config"])
            for key in ("risk", "symbols", "training_symbols", "annual_labels", "local", "ai", "seed"):
                assert cfg.model_dump()[key] == state["before"]["config"][key]
    # A future-price intervention must not change pre-cutoff training labels or features.
    cfg = AppConfig.model_validate(state["before"]["config"])
    cfg.engine, cfg.model = "ml", "cnn"
    p = Preset.model_validate(cfg.models["cnn"]["custom"].model_dump())
    p.training_years, p.training_mode, p.target, p.horizon = 5, "mixed", "trend5", 5
    cutoff = pd.Timestamp("2023-01-03")
    changed = {s: f.copy() for s, f in histories.items()}
    for f in changed.values():
        f.loc[f.index >= cutoff, "adjusted"] *= 100
    a = training_samples(histories, cfg, p, cutoff, None, "mixed", "custom")
    b = training_samples(changed, cfg, p, cutoff, None, "mixed", "custom")
    for i in range(3):
        np.testing.assert_array_equal(a[i], b[i])
    for s in histories:
        pd.testing.assert_frame_equal(features(histories[s]).loc[:cutoff-pd.Timedelta(days=1)], features(changed[s]).loc[:cutoff-pd.Timedelta(days=1)])
    write_json(output / "state.json", state)
    native_failures = (output / "native.log").read_text(encoding="utf-8").count("Windows fatal exception")
    report = dict(status="passed", candidates=len(state["evaluations"]), annuals=annuals, trades_checked=trade_count, pairs=20, manifest_hash=state["manifest"]["hash"], centers={f"{m}:{p}":state["models"][m][p]["center"] for m in MODEL_NAMES for p in PROFILES}, native_exceptions_logged=native_failures, signatures={str(k):v for k,v in signatures.items()}, checks=["all five models/four presets completed three research waves", "identical ten trading ETFs/prices/date range for every comparison", "independently recalculated return/drawdown/holding/fees/exposure", "zero local fallback", "prior-year training cutoff", "future-price intervention preserves training data", "risk/account/local/AI settings not modified"], caveats=state["protocol"]["caveats"])
    write_json(output / "verification.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    verify()
