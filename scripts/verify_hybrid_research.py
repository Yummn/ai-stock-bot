"""Small real-provider probes; never submits simulated or live orders."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from ai_stock_bot.v2.store import Store
from ai_stock_bot.v2.schema import AppConfig
from ai_stock_bot.v2.market import Market
from ai_stock_bot.v2.features import features, online_regimes
from ai_stock_bot.v2.execution import Account
from ai_stock_bot.v2.reference import MLReferences
from ai_stock_bot.v2.strategies import ai_decisions
from ai_stock_bot.v2.research import run_research

OUTPUT = Path("data/v3/research/all-references-2026-09-28")
sys.stdout.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--research", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    live = Store(Path("data/v3"))
    config = AppConfig.model_validate(live.settings()["config"])
    account_before = live.meta("account")
    settings_before = live.settings()
    if args.research:
        result = run_research(live, config, lambda p, m: print(p, m, flush=True))
        name = "research.json"
        print({"sources": len(result["sources"]), "topics": len(result["topics"]), "search_count": result["search_count"], "status": result["status"]}, flush=True)
    else:
        config.engine, config.auto_profile = "ai", False
        config.symbols = config.symbols[:1]
        for presets in config.models.values():
            for profile, preset in presets.items():
                if profile == "custom":
                    continue
                preset.training_years = 1
                preset.training_mode = "mixed"
                preset.min_samples = 20
                for key in ("n_estimators", "max_iter", "epochs"):
                    if key in preset.params:
                        preset.params[key] = 5
                if "device" in preset.params:
                    preset.params["device"] = "cpu"
        market = Market(live)
        histories = {s: market.read(s, end="2025-12-31") for s in dict.fromkeys(config.symbols + config.training_symbols)}
        cutoff = max(histories[s].index[histories[s].index < pd.Timestamp("2025-12-31")][-1] for s in config.symbols)
        references = MLReferences(Store(OUTPUT / "compute"), histories, config, cutoff, lambda m: print(m, flush=True))
        snapshots, inputs = [], {}
        for symbol in config.symbols:
            frame = histories[symbol].loc[histories[symbol].index <= cutoff]
            date = frame.index[-1]
            f = features(frame).iloc[-1]
            inputs[symbol] = references.at(symbol, date, str(online_regimes(features(frame))[-1]))
            snapshots.append((symbol, float(frame.close.iloc[-1]), f))
        assert all(len(v["references"]) == 15 and v["summary"]["available"] > 0 for v in inputs.values()), inputs
        result = ai_decisions(config, snapshots, Account(config.risk).decision_context(str(cutoff.date())), str(cutoff.date()), inputs)
        assert all(not v["fallback"] and v["source"] in {"codex", "openai"} for v in result.values()), result
        assert all(v["audit"]["input"]["ml_references"] for v in result.values())
        name = "hybrid.json"
        print({s: {k: v[k] for k in ("side", "source", "reference_alignment")} for s, v in result.items()}, flush=True)
    (OUTPUT / name).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    assert live.meta("account") == account_before and live.settings() == settings_before
    print("Account and enabled settings unchanged.", flush=True)


if __name__ == "__main__":
    main()
