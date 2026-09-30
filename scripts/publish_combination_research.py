"""Publish finished evidence and a reproducible review; never apply candidate plans."""
import argparse
from importlib.metadata import version, PackageNotFoundError
import json
from pathlib import Path
import platform

from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.research import write_json
from ai_stock_bot.v4.research_report import KEY, compact_result, display_name, publication
from ai_stock_bot.v4.service import settings


def percent(value):
    return f"{value * 100:.2f}%" if value is not None else "n/a"


def main(args):
    report = publication(args.research)
    corrected = json.loads((args.benchmark / "results.json").read_text(encoding="utf-8"))
    correction_protocol = json.loads((args.benchmark / "protocol.json").read_text(encoding="utf-8"))
    if not corrected.get("completed_at") or correction_protocol["identity"] != report["source_hash"]:
        raise ValueError("Missing completed, same-source benchmark correction")
    reference = next(r for r in report["rows"] if r["id"] == "hold-1x")
    report["benchmark_correction"] = {"protocol": correction_protocol, "original_reference": reference["periods"],
        "reason": "原持有对照的8%调仓门槛漏掉了7.5%的首次仓位；研究对照改为完整建仓，旧冻结观察不改写。"}
    reference["name"] = "1x 完整建仓持有"
    reference["candidate"] = correction_protocol["candidate"]
    reference["periods"] = {p: {c: compact_result(row[c]) for c in ("base", "stress")}
        for p, row in corrected["periods"].items()}
    for row in report["rows"]:
        row["gate"]["beats_hold"] = all(row["periods"][p]["base"]["liquidation_return"] > reference["periods"][p]["base"]["liquidation_return"] for p in ("later", "continuous"))
        row["gate"]["passed"] = all(v for k, v in row["gate"].items() if k != "passed")
    # Reference gates describe the corrected reference too, not the discarded account.
    reference["gate"]["return_target"] = all(reference["periods"][p]["base"]["liquidation_cagr"] >= .20 for p in ("later", "continuous"))
    reference["gate"]["drawdown"] = all(reference["periods"][p]["base"]["max_drawdown"] >= -.20 for p in reference["periods"])
    reference["gate"]["positive_completed_years"] = all(r["return_pct"] > 0 for r in reference["periods"]["continuous"]["base"]["per_year"] if r["year"] < 2026)
    reference["gate"]["passed"] = False
    report["replays"] += len(list((args.benchmark / "cases").glob("*.result.json")))
    report["failed_attempts"] += sum(sum(r["exit_code"] != 0 for r in json.loads(p.read_text(encoding="utf-8"))) for p in (args.benchmark / "logs").glob("*.json"))
    supplemental = json.loads((args.supplement / "results.json").read_text(encoding="utf-8"))
    supplemental_protocol = json.loads((args.supplement / "protocol.json").read_text(encoding="utf-8"))
    if not supplemental.get("completed_at"):
        raise ValueError("Supplemental research has not completed")
    if supplemental_protocol["identity"] != report["source_hash"]:
        raise ValueError("Source snapshot differs")
    for key, periods in supplemental["finalists"].items():
        complete = all(p in periods for p in ("continuous", "later", "partial"))
        if not complete:
            continue
        checks = {"complete": True,
            "return_target": all(periods[p]["base"]["liquidation_cagr"] >= .20 for p in ("later", "continuous")),
            "drawdown": all(periods[p]["base"]["max_drawdown"] >= -.20 for p in periods),
            "stress_profitable": all(periods[p]["stress"]["liquidation_return"] > 0 for p in periods),
            "positive_completed_years": all(r["return_pct"] > 0 for r in periods["continuous"]["base"]["per_year"] if r["year"] < 2026),
            "positive_partial": periods["partial"]["base"]["liquidation_return"] > 0,
            "beats_hold": all(periods[p]["base"]["liquidation_return"] > reference["periods"][p]["base"]["liquidation_return"] for p in ("later", "continuous")),
            "solvent": all(not periods[p][c]["insolvent"] for p in periods for c in ("base", "stress"))}
        candidate = periods["continuous"]["candidate"]
        report["rows"].append({"id": key, "name": "放宽风险 · " + display_name(candidate), "family": "risk_budget",
            "candidate": candidate, "gate": {**checks, "passed": all(checks.values())},
            "periods": {p: {c: compact_result(row[c]) for c in ("base", "stress")} for p, row in periods.items()}})
    for key, row in supplemental["development"].items():
        report["development"].append({"id": key, "name": "放宽风险 · " + display_name(row["candidate"]),
            "candidate": row["candidate"], **{c: {k: v for k, v in row[c].items() if k != "curve"} for c in ("base", "stress")}})
    report["supplemental_protocol"] = supplemental_protocol
    report["candidate_count"] += supplemental_protocol["candidate_count"]
    report["replays"] += len(list((args.supplement / "cases").glob("*.result.json")))
    report["failures"] += supplemental["failures"]
    report["failed_attempts"] += sum(sum(r["exit_code"] != 0 for r in json.loads(p.read_text(encoding="utf-8")))
        for p in (args.supplement / "logs").glob("*.json"))
    report["passed"] = [r["id"] for r in report["rows"] if r["gate"]["passed"]]
    report["verdict"] = "仅通过历史研究筛选，尚不能证明未来盈利" if report["passed"] else "没有方案通过全部筛选条件，不自动替换当前计划"
    report["completed_at"] = max(report["completed_at"], supplemental["completed_at"])
    report["completed_at"] = max(report["completed_at"], corrected["completed_at"])
    versions = {}
    for package in ("numpy", "pandas", "scikit-learn", "xgboost", "torch", "pydantic"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "not installed in report runtime"
    report["report_environment"] = {"python": platform.python_version(), "packages": versions}
    report["neural_runtime"] = ".venv/Scripts/python.exe; actual device in monthly model receipts"
    write_json(args.research / "publication.json", report)
    store = Store(args.data_dir)
    before = settings(store)
    observation = store.meta("portfolio_observation")
    store.set_meta(KEY, report)
    assert settings(store) == before
    assert store.meta("portfolio_observation") == observation
    lines = ["# 组合、模型、频率与杠杆研究", "", f"完成时间：{report['completed_at']}", "",
        f"共 {report['candidate_count']} 个候选，{report['replays']} 次账户回放。历史门槛通过 {len(report['passed'])} 个，未完成任务 {len(report['failures'])} 个。",
        "", "**这轮研究已完成，但未证明能够稳定加杠杆赚快钱。历史盈利不等于实盘优势。当前投资计划和冻结前向账户未被替换。**", "",
        "## 检验方法", "",
        "先用2020–2022开发期的压力净年化减去半个最大回撤排序，各类锁定一个方案，再检查2023–2025、2026年内独立起步和2020–2026连续账户。后段结果不用于改选。",
        "数据已在此前被研究过，不能称为全新盲测。固定六只ETF，并非历史全市场。新高风险分支是在看过第一轮开发期结果后提出，单独冻结协议，不与原始假设混称。",
        "收盘生成信号，下一交易日开盘成交；100份单位、T+1/0、佣金、滑点、成交量限制、分红、拆分、借款利息和维保均复用现有引擎。压力情景为交易成本翻倍，融资利率由8%提高到12%。",
        "测量纠错：原持有对照把每只中国ETF的7.5%初始仓位套用了8%调仓门槛，实际只买了3只资产。本轮另跑6次完整建仓对照（首次买入不设调仓门槛），所有超额收益与门槛按正确的6只持有基准重算。原始证据和冻结观察未改写。",
        "预测标签为下一开盘买入、经过5或20个交易日后开盘退出的成本后收益。月初重新训练，标签必须已经完成；训练标签结束早于验证开始。验证不通过的月份回退趋势，不把其收益归功于模型。",
        "", "## 完整账户比较", "",
        "2020–2026-09-28连续账户；年化与累计净收益计入期末估算平仓费。年内收益表未额外模拟年末平仓。", "",
        "| 方案 | 年化 | 最大回撤 | 压力年化 | 2026独立起步 | 平均总仓位 |", "|---|---:|---:|---:|---:|---:|"]
    for row in report["rows"]:
        full = row["periods"]["continuous"]
        lines.append(f"| {row['name']} | {percent(full['base']['liquidation_cagr'])} | {percent(full['base']['max_drawdown'])} | {percent(full['stress']['liquidation_cagr'])} | {percent(row['periods']['partial']['base']['liquidation_return'])} | {percent(full['base']['mean_exposure'])} |")
    lines += ["", "## 机器学习月份采用率", "", "| 模型 | 采用 / 月份 | 标签期限 | 训练窗口 |", "|---|---:|---:|---:|"]
    for model in report["models"].values():
        if not model.get("failed"):
            lines.append(f"| {model['name']} | {model['accepted']} / {model['months']} | {model['spec']['horizon']}日 | {model['spec']['years']}年 |")
    lines += ["", "## 审计与复现", "",
        "- 全候选、失败、逐年表、风险门槛、冻结参数和月份训练回执保留在同目录JSON；界面验证页的“独立研究”可查看与下载摘要。",
        "- 数据快照：`docs/reports/daily-4-7-frozen/market.json.gz`。",
        f"- SHA256：`{report['source_hash']}`。",
        "- 研究模块不会下单、启动实盘或更改用户计划。", "",
        "```powershell", "$env:PYTHONPATH = (Join-Path (Get-Location).Path 'src')",
        ".venv-runtime-check\\Scripts\\python.exe scripts/research_combinations.py --output docs/reports/combinations-20260930",
        ".venv-runtime-check\\Scripts\\python.exe scripts/research_risk_budget.py --output docs/reports/risk-budget-20260930",
        "```", "", "原目录可断点续跑；改动数据、模型参数或实现后必须换目录，不覆盖旧协议。GPU CNN使用原`.venv`，普通回放使用`.venv-runtime-check`。", "",
        "## 参考来源", "",
        "借鉴的是研究方法，而不是直接移植某项目的收益承诺：Qlib将预测、组合构建和执行分开；时间序列验证按时间顺序隔离；简单正则模型作为复杂模型的对照。", ""]
    lines += [f"- [{url.split('/')[2]}]({url})" for url in report["protocol"]["sources"]]
    (args.research / "REVIEW.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"candidates": report["candidate_count"], "replays": report["replays"],
        "passed": report["passed"], "failures": report["failures"], "settings_revision_unchanged": before["revision"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    main(parser.parse_args())
