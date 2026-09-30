"""Read-only checks for balanced experiments, including rejected candidates."""
import argparse

import numpy as np
import pandas as pd

from audit_resilient_execution import block_difference
from refine_balanced_core import promotion_checks
from research_balanced_preset import sha
from research_core_profiles import checks
from research_resilient_execution import ROOT, SNAPSHOT, SOURCE, load, write


def audit(directory):
    spec, result = load(directory/"protocol.json"), load(directory/"results.json")
    assert result.get("completed_at")
    assert sha(SNAPSHOT) == spec["market_sha256"] and sha(SOURCE) == spec["forecast_sha256"]
    for file, digest in spec["source_sha256"].items():
        assert sha(directory/"source"/file) == digest
    files = list((directory/"cases").glob("*.result.json"))
    assert len(files) == 2*len(spec["cases"])+12*len(result["finalists"])
    decisions = 0
    for file in files:
        data = load(file)
        request = load(file.with_name(file.name.replace(".result.json", ".json")))
        p = request["plan"]
        assert p["research_multiple"] == 1 and p["target_volatility"] == .1
        assert p["max_exposure"] == .98 and p["max_weight"] == .4 and p["drawdown_pause"] == .12
        eq = np.r_[p["starting_cash"], [r["equity"] for r in data["curve"]]]
        assert request["period"][0] <= data["start"] <= data["end"] <= request["period"][1]
        dates = [r["date"] for r in data["curve"]]
        assert dates == sorted(set(dates))
        net = (eq[-1]-data["exit_cost"])/eq[0]
        years = ((pd.Timestamp(data["end"])-pd.Timestamp(data["start"])).days+1)/365.25
        np.testing.assert_allclose(
            [net-1, net**(1/years)-1, (eq/np.maximum.accumulate(eq)-1).min()],
            [data["liquidation_return"], data["liquidation_cagr"], data["max_drawdown"]], atol=1e-10)
        np.testing.assert_allclose(eq[0]+sum(data["price_pnl"].values())+data["dividends_paid"]
            +data["receivables"]-data["fees"]-data["interest_paid"], eq[-1], atol=1e-6)
        assert data["interest_paid"] == 0 and not data["insolvent"]
        assert all(t["signal_date"] < t["date"] for t in data["trades"])
        assert all(not r.get("label_end") or r["label_end"] < r["cutoff"] for r in data["training"])
        for d in data["decisions"]:
            risk = d.get("risk_budget", {})
            assert risk.get("status") != "failed"
            if risk.get("status") == "optimized":
                assert risk["estimated_volatility"] <= risk["target_volatility"]+1e-5
                assert risk["target_gross"] <= p["max_exposure"]+1e-6
                decisions += 1
    old = load(directory/"cases/full-old-balanced-base.result.json")
    prior = load(ROOT/spec["baseline_reference"])
    np.testing.assert_allclose([r["equity"] for r in old["curve"]], [r["equity"] for r in prior["curve"]], atol=1e-7)
    winner = result["locked"]["balanced"]
    verdict = checks(result["finalists"][winner], result["independent_years"][winner], spec["gates"]["balanced"])
    promotion = promotion_checks(result["finalists"][winner], result["finalists"]["old-balanced"], all(verdict.values()))
    assert result["gates"]["balanced"]["checks"] == verdict
    assert result["promotion"] == promotion
    assert result["promoted"] == (all(promotion.values()) and result["separation"])
    new = load(directory/"cases"/f"full-{winner}-base.result.json")
    record = {"passed": True, "replays": len(files), "core_decisions_checked": decisions,
        "baseline_reproduced": True, "winner": winner, "promotion": promotion,
        "promoted": result["promoted"], "forward_proven": False,
        "uncertainty": [block_difference(new, old, n) for n in (20, 60)],
        "caveat": "Previously researched history; intervals are exploratory and not selection-adjusted."}
    write(directory/"audit.json", record)
    print(record, flush=True)


if __name__ == "__main__":
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    audit(parser.parse_args().directory.resolve())
