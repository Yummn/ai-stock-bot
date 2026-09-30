"""Reproducible, read-only-account research using the production portfolio engine."""
import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict
from io import StringIO
from statistics import pstdev

import pandas as pd

from ..v2.evaluation import read_gzip, write_gzip
from .data import audit, load_histories
from .model_recipes import ModelTuning, RECIPE_LABELS, TUNABLE_FIELDS, model_specs, resolved_spec
from .portfolio import simulate
from .schema import ENGINE_VERSION, Plan, fingerprint

LAB_VERSION = "research-workspace-1"
PARAMETER_LABELS = {"years": "训练年数", "horizon": "预测跨度", "regularization": "正则强度",
    "leaf_samples": "叶节点样本", "leaves": "叶节点数", "iterations": "迭代数量", "window": "序列窗口", "epochs": "训练轮数"}
MODEL_LABELS = {"hgb": "HGB", "ridge": "Ridge", "extra": "Extra Trees", "xgb": "XGBoost", "cnn": "1D-CNN"}


def candidate_plan(base, recipe, tuning=None):
    values = {**base.model_dump(), "profile": "custom", "preset_version": 0, "auto_update": False,
              "model_tuning": None}
    if recipe in {"rotation", "hold"}:
        values.update(method=recipe)
    else:
        values.update(method="ml", model_recipe=recipe, model_tuning=tuning)
    return Plan.model_validate(values)


def candidates(base, study):
    rows, identities = [], set()

    def add(plan, label, role):
        key = fingerprint(plan)
        if key not in identities:
            identities.add(key)
            rows.append({"id": key, "label": label, "role": role, "plan": plan.model_dump()})

    add(base, "当前计划 · 基线", "baseline")
    add(candidate_plan(base, "hold"), "同配置持有", "benchmark")
    if study.mode == "compare":
        for recipe in study.recipes:
            add(candidate_plan(base, recipe), {"rotation": "趋势分散", "hold": "同配置持有"}.get(recipe, RECIPE_LABELS.get(recipe)), "comparison")
        return rows
    recipe = study.recipe
    spec = resolved_spec(recipe, study.tuning)
    fields = TUNABLE_FIELDS[spec.model]
    center = {key: getattr(spec, key) for key in fields}
    add(candidate_plan(base, recipe, center), MODEL_LABELS[spec.model] + " · 手动中心", "center")
    add(candidate_plan(base, recipe), RECIPE_LABELS[recipe] + " · 默认参照", "default")
    # Two-sided deterministic neighbors. Only estimator-valid/resource bounds apply.
    axes = {
        "hgb": [("regularization", .5), ("regularization", 2), ("leaf_samples", .6), ("leaf_samples", 1.5), ("leaves", .6), ("leaves", 1.6), ("iterations", .7), ("iterations", 1.5)],
        "ridge": [("regularization", .3), ("regularization", 3), ("years", .67), ("years", 1.67), ("horizon", .5), ("horizon", 1.5), ("regularization", .1), ("regularization", 10)],
        "extra": [("leaf_samples", .5), ("leaf_samples", 2), ("iterations", .6), ("iterations", 1.6), ("years", .67), ("years", 1.67), ("horizon", .5), ("horizon", 1.5)],
        "xgb": [("regularization", .4), ("regularization", 2.5), ("iterations", .6), ("iterations", 1.6), ("years", .67), ("years", 1.67), ("horizon", .5), ("horizon", 1.5)],
        "cnn": [("window", .6), ("window", 1.5), ("epochs", .6), ("epochs", 1.5), ("years", .67), ("years", 1.67), ("horizon", .5), ("horizon", 1.5)],
    }[spec.model]
    count = 1
    for key, factor in axes:
        if count >= study.budget:
            break
        value = center[key] * factor
        if isinstance(center[key], int):
            value = int(round(value))
        try:
            tuning = ModelTuning(**{**center, key: value})
        except ValueError:
            continue
        before = len(rows)
        add(candidate_plan(base, recipe, tuning), f"{PARAMETER_LABELS[key]} = {value:g}", "neighbor")
        count += len(rows) > before
    return rows


def selection_score(result):
    annual = [row["return_pct"] for row in result["per_year"]]
    score = result["annualized_return"] - .5 * abs(result["max_drawdown"]) - .25 * pstdev(annual)
    if not math.isfinite(score) or result.get("insolvent"):
        return None
    return score


def compact(result, start_year, end_year):
    years = {row["year"] for row in result.get("per_year", [])}
    if years != set(range(start_year, end_year + 1)):
        raise ValueError("测试年份不完整，不能计分")
    reports = result.get("training", [])
    return {**{k: v for k, v in result.items() if k not in {"decisions", "rejections", "trades", "positions", "training"}},
            "model_months": sum(r.get("status") == "model" for r in reports),
            "fallback_months": sum(r.get("status") != "model" for r in reports),
            "training": [{k: r.get(k) for k in ("cutoff", "label_end", "samples", "status", "rank_ic", "reason", "parameters")} for r in reports],
            "score": selection_score(result)}


def run_study(store, job_id, request, progress):
    study, base = request.study, request.investment
    if study is None or base.research_interval != "daily":
        raise ValueError("此研究工作台只接受日线投资计划，不会自动改变盘中计划")
    rows = candidates(base, study)
    plans = {row["id"]: Plan.model_validate(row["plan"]) for row in rows}
    signature = hashlib.sha256(json.dumps({"study": study.model_dump(), "plans": rows,
        "version": LAB_VERSION, "engine": ENGINE_VERSION}, sort_keys=True).encode()).hexdigest()
    root = store.root / "research_studies" / job_id
    snapshot = root / "market.json.gz"
    start = dt.date(study.start_year, 1, 1)
    end = dt.date(study.check_end_year if study.mode == "tune" else study.end_year, 12, 31)
    if not snapshot.exists():
        warmup = max(plan.training_years for plan in plans.values())
        histories, checked = load_histories(store, base.model_copy(update={"training_years": warmup}), start, end, progress)
        frames = {s: f.to_json(orient="split", date_format="iso", double_precision=15) for s, f in histories.items()}
        identity = hashlib.sha256(json.dumps(frames, sort_keys=True).encode()).hexdigest()
        write_gzip(snapshot, {"signature": signature, "identity": identity, "frames": frames, "audit": checked})
    frozen = read_gzip(snapshot)
    if frozen["signature"] != signature:
        raise ValueError("研究版本或参数已变化，请新建任务，不可混用旧阶段")
    if hashlib.sha256(json.dumps(frozen["frames"], sort_keys=True).encode()).hexdigest() != frozen["identity"]:
        raise ValueError("冻结行情校验失败")
    histories = {s: pd.read_json(StringIO(text), orient="split") for s, text in frozen["frames"].items()}
    for frame in histories.values():
        frame.index = pd.to_datetime(frame.index)
    checked = audit(histories, base.symbols, start, end)
    if not checked["ok"]:
        raise ValueError("行情未通过检查：" + "；".join(checked["blockers"]))
    output = {"version": LAB_VERSION, "engine": ENGINE_VERSION, "study": study.model_dump(),
              "base_plan": base.model_dump(), "base_plan_key": fingerprint(base),
              "data_identity": frozen["identity"], "audit": checked, "candidates": rows,
              "winner_id": None, "complete": False, "errors": [],
              "score_rule": "年化收益 - 0.5 × 最大回撤绝对值 - 0.25 × 年收益标准差"}
    total = len(rows) + (3 if study.mode == "tune" else 0)
    completed = 0

    def stage(row, phase, first, last):
        nonlocal completed
        path = root / f"{row['id']}-{phase}.json.gz"
        stage_percent = 0.

        def stage_progress(percent, message):
            nonlocal stage_percent
            stage_percent = max(stage_percent, max(0, min(percent, 100)))
            progress(10 + 85 * (completed + stage_percent / 100) / total, f"{row['label']} · {message}")

        try:
            if path.exists():
                result = read_gzip(path)
            else:
                progress(10 + 85 * completed / total, f"{row['label']} · {first}–{last}")
                result = simulate(store, plans[row["id"]], histories, dt.date(first, 1, 1), dt.date(last, 12, 31),
                    progress=stage_progress)
                compact(result, first, last)
                write_gzip(path, result)
            row[phase] = compact(result, first, last)
        except Exception as exc:
            row[phase] = {"error": str(exc)[:1000]}
            output["errors"].append({"candidate": row["id"], "phase": phase, "error": str(exc)[:1000]})
        completed += 1
        store.update_job(job_id, result=output)

    for row in rows:
        stage(row, "selection", study.start_year, study.end_year)
    eligible = [row for row in rows if row["role"] != "benchmark" and row.get("selection", {}).get("score") is not None
                and (study.mode == "compare" or (row["plan"]["method"] == "ml" and row["plan"]["model_recipe"] == study.recipe))]
    if not output["errors"] and eligible:
        winner = max(eligible, key=lambda row: row["selection"]["score"])
        output["winner_id"] = winner["id"]
        if study.mode == "tune":
            # Lock the winner before opening later years; check results never re-rank it.
            for row in rows:
                if row["role"] in {"baseline", "benchmark"} or row["id"] == winner["id"]:
                    stage(row, "check", study.check_start_year, study.check_end_year)
    if output["errors"]:
        output["winner_id"] = None
        store.update_job(job_id, result=output)
        raise ValueError("部分候选计算失败，已保存完成阶段；可继续重试，失败候选未参与选优")
    output["complete"] = True
    return output


def candidate_draft(store, job_id, candidate_id):
    job = store.job(job_id, True)
    result = job.get("result") or {}
    if job["kind"] != "portfolio_research" or job["status"] != "done" or not result.get("complete"):
        raise ValueError("仅完整研究结果可发送到投资计划")
    row = next((r for r in result["candidates"] if r["id"] == candidate_id), None)
    if not row or row["role"] == "benchmark" or row.get("selection", {}).get("score") is None:
        raise ValueError("请选择有效策略候选")
    if result["study"]["mode"] == "tune" and not row.get("check"):
        raise ValueError("该候选未完成后续区间检验")
    plan = Plan.model_validate({**row["plan"], "profile": "custom", "preset_version": 0, "auto_update": False})
    return {"plan": plan.model_dump(), "label": row["label"], "source_job": job_id,
            "plan_key": fingerprint(plan), "base_plan_key": result["base_plan_key"]}
