"""Independent arithmetic and provenance checks for the completed release."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research_combinations import ROOT, SNAPSHOT, load
from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.research import write_json
from ai_stock_bot.v4.schema import Plan, fingerprint


def main():
    count = 0
    source_hash = hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()
    for name in ("execution-upgrade-20260930", "execution-risk-20260930"):
        directory = ROOT/"docs/reports"/name
        protocol = load(directory/"protocol.json")
        assert protocol["identity"] == source_hash
        assert load(directory/"results.json")["completed_at"]
        for path in (directory/"cases").glob("*.result.json"):
            result = load(path)
            request = load(path.with_name(path.name.replace(".result.json", ".json")))
            start = request["candidate"]["plan"]["starting_cash"]
            values = np.array([start] + [r["equity"] for r in result["curve"]])
            drawdown = float(np.min(values/np.maximum.accumulate(values)-1))
            exit_cost = result["exit_cost"] if "exit_cost" in result else result["estimated_exit_cost"]
            liquidated = values[-1]-exit_cost
            years = ((pd.Timestamp(result["end"])-pd.Timestamp(result["start"])).days+1)/365.25
            expected = (liquidated/start)**(1/years)-1
            np.testing.assert_allclose(result["liquidation_cagr"], expected, atol=1e-12)
            np.testing.assert_allclose(result["liquidation_return"], liquidated/start-1, atol=1e-12)
            np.testing.assert_allclose(result["max_drawdown"], drawdown, atol=1e-12)
            assert pd.Timestamp(result["start"]) >= pd.Timestamp(request["period"][0])
            assert pd.Timestamp(result["end"]) <= pd.Timestamp(request["period"][1])
            assert len({r["date"] for r in result["curve"]}) == len(result["curve"])
            count += 1
        for model, digest in protocol["panels"].items():
            path = directory/"models"/model/"panel.json"
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            panel = load(path)
            assert all(not r["label_end"] or r["label_end"] < r["cutoff"] for r in panel["reports"])
    assert count == 180
    store = Store(ROOT/"data/v4-intraday-preview")
    saved = store.meta("investment_settings")
    plan = Plan.model_validate(saved["plan"])
    release = load(ROOT/"docs/reports/execution-release-20260930/verified-plan.json")
    assert fingerprint(plan) == release["plan_key"]
    study = store.meta("portfolio_observation")
    assert study["id"] == "c1891fe5031642d2996e65874f726a99"
    assert Plan.model_validate(study["plan"]).execution_policy == "legacy"
    assert Plan.model_validate(study["plan"]).model_recipe == "legacy"
    assert not store.meta("portfolio_state")
    output = {"replays_recalculated": count, "snapshot_hash": source_hash,
        "saved_revision": saved["revision"], "matching_evidence": True,
        "frozen_observation_id": study["id"], "no_paper_or_live_trades_placed": True}
    write_json(ROOT/"docs/reports/execution-release-20260930/audit.json", output)
    print(json.dumps(output))


if __name__ == "__main__":
    main()
