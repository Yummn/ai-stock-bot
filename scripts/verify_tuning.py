"""Independently reconcile tuning metrics and create a compact evidence receipt."""
from __future__ import annotations

import json
import math
from pathlib import Path

from ai_stock_bot.v2.schema import MODEL_NAMES, PROFILES
from ai_stock_bot.v2.store import Store
from tune_stocklab import AUDIT_YEARS, PRESETS, Research, SEARCH_YEARS, verify_audit


def main():
    research = Research("http://127.0.0.1:8765", "data/v2/research/2026-09-27")
    store = Store(Path("data/v2"))
    state = research.state
    annual_count, changed, rows, hashes = 0, 0, [], {}
    for model in MODEL_NAMES:
        entry = state["results"][model]
        verify_audit(entry)
        for phase in ("baseline", "search"):
            job_id = state["jobs"][f"{model}:{phase}"]
            job = store.job(job_id, True)
            assert job["status"] == "done"
            request = job["request"]
            weights = request["weights"]
            total_weight = sum(weights.values())
            for trial in store.trials(job_id):
                assert trial["complete"] and trial["valid_years"] == len(SEARCH_YEARS)
                target = {"aggressive": "bull", "balanced": "sideways", "conservative": "bear"}[trial["profile"]]
                selected = [y for y in trial["per_year"] if y["scene"] == target]
                fallback = [y for y in trial["per_year"] if y["scene"] != target]
                def average(group):
                    return sum(y["return_pct"] + .5 * y["excess_return"] + .5 * y["max_drawdown"] for y in group) / len(group) if group else 0.
                expected = (weights["specialized"] * average(selected) + weights["fallback"] * average(fallback) + weights["composite"] * average(trial["per_year"])) / total_weight
                assert math.isclose(trial["score"], expected, abs_tol=1e-12)
                annual_count += len(trial["per_year"])
                for year in trial["per_year"]:
                    fingerprint = year["start"], year["end"], year["data_hash"]
                    hashes.setdefault((model, year["year"]), set()).add(fingerprint)
        for profile in PRESETS:
            chosen = entry["selected"][profile]
            changed += chosen["origin"] == "search"
            summary = {"model": MODEL_NAMES[model], "profile": PROFILES[profile], "selection": "调整版" if chosen["origin"] == "search" else "保留原参数"}
            for year in AUDIT_YEARS:
                for source in ("baseline", "selected"):
                    job = store.job(state["jobs"][f"{model}:{profile}:{source}:{year}"], True)
                    assert job["status"] == "done"
                    result = job["result"]
                    values = [result["start_equity"]] + [p["equity"] for p in result["curve"]]
                    peak, dd = values[0], 0.
                    for value in values:
                        peak = max(peak, value)
                        dd = min(dd, value / peak - 1)
                    assert math.isclose(result["return_pct"], values[-1] / values[0] - 1, abs_tol=1e-12)
                    assert math.isclose(result["max_drawdown"], dd, abs_tol=1e-12)
                    assert math.isclose(result["benchmark_return"], result["curve"][-1]["hold_equity"] / values[0] - 1, abs_tol=1e-12)
                    assert math.isclose(result["fees"], sum(t["fee"] for t in result["trades"]), abs_tol=1e-8)
                    assert result["trade_count"] == len(result["trades"])
                    summary[f"{source}_{year}"] = round(result["return_pct"] * 100, 2)
                    annual_count += 1
            rows.append(summary)
    assert all(len(v) == 1 for v in hashes.values()), "Different search candidates used different annual data"
    assert annual_count == 285
    verification = {"valid_annual_backtests": annual_count, "improved_search_presets": changed, "retained_search_presets": 15 - changed, "deployed_updates": 2, "retained_deployed_presets": 13, "metric_reconciliation": "passed", "within_model_price_hash_consistency": "passed", "no_local_fallback_in_later_years": True}
    research.write("verification.json", verification)
    hgb = state["results"]["hist_gradient_boosting"]
    performance = []
    for old, new in zip(hgb["baseline"]["best"]["balanced"]["per_year"], hgb["selected"]["balanced"]["per_year"]):
        performance.append({"year": new["year"], "before": round(old["return_pct"] * 100, 2), "after": round(new["return_pct"] * 100, 2), "holding": round(new["benchmark_return"] * 100, 2)})
    for year in AUDIT_YEARS:
        pair = {r["source"]: r for r in hgb["audit"]["balanced"] if r["year"] == year}
        performance.append({"year": year, "before": round(pair["baseline"]["return_pct"] * 100, 2), "after": round(pair["selected"]["return_pct"] * 100, 2), "holding": round(pair["selected"]["benchmark_return"] * 100, 2)})
    receipt = {"schemaVersion": 1, "items": [{
        "id": "model-parameter-validation", "title": "五种模型的分档参数验证", "queries": [{
            "id": "later-year-comparisons", "source": {
                "label": "StockLab 本机回测数据库", "files": [{"label": "stocklab.sqlite3"}, {"label": "state.json"}],
                "metricDefinitions": [{"label": "年度收益", "definition": "各年度均以十万元模拟资金重新开始，收益已扣模拟手续费和滑点；旧参数与调整版使用相同行情。"}],
                "filters": ["模型：RandomForest、HistGradientBoosting、XGBoost、MLP、1D-CNN", "档位：激进、均衡、保守", "交易池：同一组十只 ETF"],
                "caveats": ["2021–2023 年用于候选排名，2024、2025 年用于后段检查和最终发布判断；这些年份在旧项目中已经出现过，不是真正未见数据。", "表中是研究候选；默认仅采用 RF 保守、HGB 均衡两档，其余十三档保留原参数。", "同一模型内的新旧候选行情指纹一致，不同模型组的历史下载指纹有差异，因此不直接进行五模型排名。", "历史回测不保证未来收益；未模拟分红再投资。"],
                "evidenceFlow": [{"kind": "validation", "title": "指标复算", "detail": "285 个有效年度回测已核对；后段收益、最大回撤、手续费和交易次数从资产曲线及成交记录复算一致。同一模型内同一年不同候选的行情指纹一致。"}],
            }, "reportingPeriod": "2021–2025 自然年", "columns": [
                {"field": "model", "label": "模型"}, {"field": "profile", "label": "档位"}, {"field": "selection", "label": "研究候选来源"},
                {"field": "baseline_2024", "label": "2024 原收益（%）"}, {"field": "selected_2024", "label": "2024 候选收益（%）"},
                {"field": "baseline_2025", "label": "2025 原收益（%）"}, {"field": "selected_2025", "label": "2025 候选收益（%）"},
            ], "rows": rows,
        }]}, {"id": "default-balanced-performance", "title": "默认 HGB 均衡方案的五年模拟表现", "queries": [{
            "id": "hgb-balanced-years", "source": {"label": "StockLab HGB 分档验证", "files": [{"label": "state.json"}], "caveats": ["默认方案不是每年都盈利；搜索期与后段检查必须分开看。", "同一年度的买入持有基准为交易池等金额配置，不受策略单笔金额上限约束。"]},
            "reportingPeriod": "2021–2025 自然年", "columns": [{"field": "year", "label": "年份"}, {"field": "before", "label": "原收益（%）"}, {"field": "after", "label": "采用收益（%）"}, {"field": "holding", "label": "持有收益（%）"}], "rows": performance,
        }]}]}
    research.write("sources.json", receipt)
    print(json.dumps({**verification, "default_performance": performance}, ensure_ascii=False))


if __name__ == "__main__":
    main()
