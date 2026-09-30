"""Independent numerical audit and paired block uncertainty, without model selection."""
import argparse
import copy
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from research_resilient_execution import ROOT, SNAPSHOT, SOURCE, load, write


def block_difference(candidate, baseline, length, samples=2000):
    if [r["date"] for r in candidate["curve"]] != [r["date"] for r in baseline["curve"]]:
        raise ValueError("Different curve dates")
    def logs(row):
        values = np.r_[100000., [p["equity"] for p in row["curve"]]]
        values[-1] -= row["exit_cost"]
        if (values <= 0).any():
            raise ValueError("Cannot bootstrap insolvent equity")
        return np.diff(np.log(values))
    a, b = logs(candidate), logs(baseline)
    rng = np.random.default_rng(20260930)
    blocks = int(np.ceil(len(a)/length))
    years = ((pd.Timestamp(candidate["end"])-pd.Timestamp(candidate["start"])).days+1)/365.25
    differences = []
    for _ in range(samples):
        starts = rng.integers(0, len(a), blocks)
        indices = ((starts[:, None]+np.arange(length)) % len(a)).ravel()[:len(a)]
        differences.append(np.exp(a[indices].sum()/years)-np.exp(b[indices].sum()/years))
    low, high = np.quantile(differences, [.025, .975])
    return {"block_sessions": length, "samples": samples, "low": float(low), "high": float(high),
            "selection_adjusted": False}


def audit(directory):
    protocol, results = load(directory/"protocol.json"), load(directory/"results.json")
    assert results.get("completed_at")
    assert hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest() == protocol["market_sha256"]
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == protocol["forecast_sha256"]
    replay_files = list((directory/"cases").glob("*.result.json"))
    expected = 2*(len(protocol["cases"])+3*len(results["finalists"]))
    assert len(replay_files) == expected
    for file in replay_files:
        row = load(file)
        request = load(file.with_name(file.name.replace(".result.json", ".json")))
        curve = row["curve"]
        assert request["period"][0] <= curve[0]["date"] <= curve[-1]["date"] <= request["period"][1]
        values = np.r_[request["plan"]["starting_cash"], [p["equity"] for p in curve]]
        years = ((pd.Timestamp(curve[-1]["date"])-pd.Timestamp(curve[0]["date"])).days+1)/365.25
        net = (values[-1]-row["exit_cost"])/values[0]
        np.testing.assert_allclose([row["liquidation_return"], row["liquidation_cagr"], row["max_drawdown"]],
            [net-1, net**(1/years)-1, (values/np.maximum.accumulate(values)-1).min()], atol=1e-10)
        reconciled = (values[0]+sum(row["price_pnl"].values())+row["dividends_paid"]+row["receivables"]
                      -row["fees"]-row["interest_paid"])
        np.testing.assert_allclose(reconciled, row["end_equity"], atol=1e-6)
        assert all(t["signal_date"] < t["date"] for t in row["trades"])
        for receipt in row["training"]:
            for item in [receipt, *receipt.get("components", {}).values()]:
                assert not item.get("label_end") or item["label_end"] < item["cutoff"]
        assert request["plan"]["research_multiple"] == 2
        assert request["plan"]["target_volatility"] == .3
        assert request["plan"]["max_exposure"] == .9
    baseline = "legacy-rebalance" if "legacy-rebalance" in results["finalists"] else "single-legacy"
    original = load(ROOT/"docs/reports/execution-risk-20260930/results.json")["finalists"]
    reference = original["hgb20_regularized-2x-monthly-v30"]
    for period in ("later", "partial", "continuous"):
        for cost in ("base", "stress"):
            before = reference[period][cost]
            now = load(directory/"cases"/f"{period}-{baseline}-{cost}.result.json")
            assert [r["date"] for r in now["curve"]] == [r["date"] for r in before["curve"]]
            np.testing.assert_allclose([r["equity"] for r in now["curve"]], [r["equity"] for r in before["curve"]], atol=1e-7)
    base = load(directory/"cases"/f"continuous-{baseline}-base.result.json")
    uncertainty, gates = {}, {}
    for key, periods in results["finalists"].items():
        row = load(directory/"cases"/f"continuous-{key}-base.result.json")
        uncertainty[key] = [block_difference(row, base, n) for n in (20, 60)]
        checks = {
            "return_target": all(periods[p]["base"]["liquidation_cagr"] >= .20 for p in ("later", "continuous")),
            "drawdown_limit": all(periods[p][s]["max_drawdown"] >= -.20 for p in periods for s in ("base", "stress")),
            "completed_years_positive": all(r["return_pct"] > 0 for r in row["per_year"] if r["year"] < 2026),
            "recent_positive": periods["partial"]["base"]["liquidation_return"] > 0,
            "stress_profitable": all(periods[p]["stress"]["liquidation_return"] > 0 for p in periods),
            "beats_hold": all(periods[p]["base"]["liquidation_cagr"] > original["hold-1x-corrected"][p]["base"]["liquidation_cagr"] for p in ("later", "continuous")),
            "solvent": all(not periods[p][s]["insolvent"] for p in periods for s in ("base", "stress")),
        }
        assert all(checks[k] == v for k, v in results["gates"][key]["checks"].items())
        gates[key] = {"checks": checks, "historical_passed": all(checks.values()), "forward_proven": False}
    result = {"passed": True, "replays": len(replay_files), "baseline_points_match": True,
              "gates": gates, "uncertainty": uncertainty,
              "note": "Paired circular block intervals are exploratory, not corrected for repeated candidate selection. No forward-profit proof."}
    write(directory/"audit.json", result)
    print(f"AUDIT {directory.name}: {len(replay_files)} reconciled replays, identical baseline, causal labels, no gate bypass", flush=True)


def publish(directories, data_dir):
    from ai_stock_bot.v2.store import Store
    from ai_stock_bot.v4.research_report import KEY, resilience_publication
    store = Store(data_dir)
    before = {key: store.meta(key) for key in ("investment_settings", "portfolio_state", "portfolio_observation")}
    report = copy.deepcopy(store.meta(KEY))
    if not report:
        raise ValueError("Original research report is missing")
    additions = [resilience_publication(d) for d in directories]
    ids = {r["id"] for r in additions}
    report["resilience_studies"] = [r for r in report.get("resilience_studies", []) if r["id"] not in ids]+additions
    store.set_meta(KEY, report)
    assert all(store.meta(key) == value for key, value in before.items())
    print("Published read-only comparisons; saved plan, account and frozen observation unchanged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--publish", type=Path)
    args = parser.parse_args()
    for directory in args.directories:
        audit(directory)
    if args.publish:
        publish(args.directories, args.publish)
