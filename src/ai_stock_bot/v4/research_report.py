"""Publish a completed offline research run without applying any of its parameters."""
from __future__ import annotations

import json
from pathlib import Path

KEY = "portfolio_combination_research"
NAMES = {
    "legacy": "双周期趋势", "multi": "多周期趋势", "breakout": "通道突破",
    "pullback": "趋势内回调", "allocation": "不限趋势的分散组合", "hold": "1x 买入持有",
    "old_ml": "现有梯度树组合", "hgb5_old": "梯度树 · 原特征 / 5日",
    "hgb20_old": "梯度树 · 原特征 / 20日", "hgb20": "梯度树 · 扩展特征",
    "hgb20_5y": "梯度树 · 5年训练", "hgb20_regularized": "梯度树 · 加强正则",
    "ridge20": "岭回归", "extra20": "随机化树", "xgb20": "XGBoost", "cnn20": "1D-CNN",
}
FREQUENCIES = {"daily": "每日", "weekly": "每周", "monthly": "每月"}


def display_name(candidate):
    name = NAMES[candidate.get("model", candidate["family"])]
    if candidate.get("policy"):
        name += " + 趋势过滤" if candidate["policy"] == "trend" else " · 独立信号"
    plan = candidate["plan"]
    return f"{name} / {FREQUENCIES[plan['rebalance']]} / {plan['research_multiple']}x"


def compact_result(result):
    value = {k: v for k, v in result.items() if k not in {"curve", "training_receipts", "positions", "trades"}}
    curve = result.get("curve", [])
    # Only chart display is sampled; returns, risk, gates and yearly rows use every bar.
    value["curve"] = [{"date": r["date"], "equity": r["equity"]} for i, r in enumerate(curve)
        if i % 5 == 0 or i == len(curve) - 1]
    return value


def publication(directory):
    directory = Path(directory)
    protocol = json.loads((directory / "protocol.json").read_text(encoding="utf-8"))
    results = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    if not results.get("completed_at"):
        raise ValueError("Only completed research can be published")
    rows = []
    for key, periods in results["finalists"].items():
        if not all(p in periods for p in ("continuous", "later", "partial")):
            continue
        candidate = periods["continuous"]["candidate"]
        rows.append({"id": key, "name": display_name(candidate), "family": candidate.get("model", candidate["family"]),
            "candidate": candidate, "gate": results["gates"][key],
            "periods": {p: {cost: compact_result(row[cost]) for cost in ("base", "stress")}
                for p, row in periods.items()}})
    models = {}
    for name in protocol["models"]:
        panel = directory / "models" / name / "panel.json"
        if not panel.exists():
            models[name] = {"name": NAMES[name], "failed": True}
            continue
        reports = json.loads(panel.read_text(encoding="utf-8"))["reports"]
        models[name] = {"name": NAMES[name], "months": len(reports),
            "accepted": sum(r["status"] == "model" for r in reports), "spec": protocol["models"][name],
            "reports": reports}
    development = [{"id": k, "name": display_name(v["candidate"]), "candidate": v["candidate"],
        "base": {f: x for f, x in v["base"].items() if f != "curve"},
        "stress": {f: x for f, x in v["stress"].items() if f != "curve"}}
        for k, v in results["development"].items()]
    completed = len(list((directory / "cases").glob("*.result.json")))
    recovered_attempts = sum(sum(r["exit_code"] != 0 for r in json.loads(p.read_text(encoding="utf-8")))
        for p in (directory / "logs").glob("*.json"))
    return {"id": directory.name, "completed_at": results["completed_at"], "source_hash": protocol["identity"],
        "engine": protocol["engine"], "protocol": protocol, "rows": rows, "models": models,
        "development": development, "locked": results["locked"], "candidate_count": protocol["candidate_count"],
        "replays": completed, "failed_attempts": recovered_attempts,
        "failures": results["failures"], "passed": results["historical_gate_passed"],
        "goal_proven": False, "applied": False,
        "verdict": "仅通过历史研究筛选，尚不能证明未来盈利" if results["historical_gate_passed"] else "没有方案通过全部筛选条件，不自动替换当前计划"}


def publish(store, directory):
    result = publication(directory)
    store.set_meta(KEY, result)
    return result


def execution_publication(directory):
    directory = Path(directory)
    protocol = json.loads((directory / "protocol.json").read_text(encoding="utf-8"))
    results = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    if not results.get("completed_at") or len(results["development"]) != protocol["candidate_count"]:
        raise ValueError("Execution study has unfinished development cases")
    rows = []
    fields = ("start", "end", "liquidation_return", "liquidation_cagr", "max_drawdown",
              "interest_paid", "fees", "trade_count", "mean_exposure", "per_year", "insolvent")
    for key, periods in results["finalists"].items():
        if set(periods) != {"continuous", "later", "partial"}:
            raise ValueError("Execution study has unfinished final cases")
        candidate = periods["continuous"]["candidate"]
        plan = candidate["plan"]
        policy = "成本约束" if plan["execution_policy"] == "cost_aware" else "标准建仓"
        rows.append({"id": key, "name": f"{display_name(candidate)} / {policy} / 波动目标 {plan['target_volatility']:.0%}",
            "candidate": candidate, "periods": {p: {cost: {f: row[cost][f] for f in fields}
                       for cost in ("base", "stress")} for p, row in periods.items()}})
    expected = 2 * (len(results["development"]) + 3*len(rows))
    if len(list((directory / "cases").glob("*.result.json"))) != expected:
        raise ValueError("Execution replay count mismatch")
    return {"id": directory.name, "completed_at": results["completed_at"], "rows": rows,
        "candidate_count": len(results["development"]), "replays": expected,
        "source_hash": protocol["identity"], "protocol": protocol,
        "locked": results["locked"], "applied": False}


def resilience_publication(directory):
    directory = Path(directory)
    protocol = json.loads((directory/"protocol.json").read_text(encoding="utf-8"))
    results = json.loads((directory/"results.json").read_text(encoding="utf-8"))
    audit = json.loads((directory/"audit.json").read_text(encoding="utf-8"))
    if not results.get("completed_at") or not audit.get("passed"):
        raise ValueError("Only completed, independently audited research can be published")
    if set(results["development"]) != set(protocol["cases"]):
        raise ValueError("Missing development candidates")
    expected = 2*(len(protocol["cases"])+3*len(results["finalists"]))
    if audit["replays"] != expected:
        raise ValueError("Replay count mismatch")
    labels = {"legacy": "原信号", "trend_weighted": "模型仓位随趋势缩放", "confirmed": "模型需三周期确认",
              "consensus": "模型与兜底均需三周期确认", "fast_confirmed": "三周期加近期趋势确认",
              "rebalance": "月度复核", "weekly": "每周减仓复核", "daily": "每日减仓复核",
              "single": "原梯度树", "trees": "三树模型组合", "diverse": "五模型组合",
              "risk1y": "风险归一化 · 1年", "risk3y": "风险归一化 · 3年",
              "risk5y": "风险归一化 · 5年", "regular5y": "原目标 · 5年", "model": "梯度树"}
    def name(key):
        return " / ".join(labels.get(part, part) for part in key.split("-"))
    rows = []
    for key, periods in results["finalists"].items():
        if set(periods) != {"later", "partial", "continuous"}:
            raise ValueError("Missing final periods")
        rows.append({"id": key, "name": name(key), "periods": periods,
                     "gate": audit["gates"][key], "uncertainty": audit["uncertainty"][key]})
    titles = {"resilient-execution-1": "风险复核与趋势确认",
              "resilient-execution-2": "多模型与兜底对照",
              "resilient-normalization-3": "训练目标与窗口对照"}
    return {"id": directory.name, "title": titles[protocol["version"]],
        "candidate_count": len(protocol["cases"]), "replays": expected,
        "rows": rows, "development": [{"id": key, "name": name(key), **pair}
                                        for key, pair in results["development"].items()],
        "passed": [r["id"] for r in rows if r["gate"]["historical_passed"]],
        "completed_at": results["completed_at"], "applied": False, "goal_proven": False,
        "source_hash": protocol["market_sha256"],
        "limitations": protocol["limitations"], "selection": protocol["selection"]}
