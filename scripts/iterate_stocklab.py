"""Frozen-data, feedback-driven research for all twenty model/preset pairs."""
from __future__ import annotations

import argparse
import faulthandler
import gzip
import hashlib
import json
import sys
import subprocess
import time
import traceback
from pathlib import Path

import numpy as np
import requests

from ai_stock_bot.v2.backtest import metrics, run_backtest
from ai_stock_bot.v2.market import Market, digest
from ai_stock_bot.v2.schema import AppConfig, JobRequest, MODEL_NAMES, PROFILES, Preset
from ai_stock_bot.v2.store import Store, dumps

SEARCH_YEARS = [2021, 2022, 2023, 2024]
CHECK_YEARS = [2025]
OUTPUT = Path("data/v2/research/iterative-2026-09-27")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    # Windows readers can briefly deny an atomic rename without allowing deletion sharing.
    for attempt in range(20):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.1)


def scoring_scene(row):
    # An ex-post benchmark label is used only for evaluating a completed experiment.
    value = row["benchmark_return"]
    return "bull" if value >= .1 else "bear" if value <= -.05 else "sideways"


def objective(rows, profile):
    target = {"aggressive": "bull", "balanced": "sideways", "conservative": "bear", "custom": None}[profile]
    dd_weight = {"aggressive": .35, "balanced": .7, "conservative": 1.2, "custom": .7}[profile]
    def value(row):
        score = row["return_pct"] + .35 * row["excess_return"] + dd_weight * row["max_drawdown"]
        if profile == "aggressive" and scoring_scene(row) == "bull":
            score -= .03 * max(0., 1 - row["mean_exposure"] / .15)
        return score
    special = [r for r in rows if scoring_scene(r) == target]
    other = [r for r in rows if scoring_scene(r) != target]
    average = lambda subset: float(np.mean([value(r) for r in subset]))
    pooled = average(rows)
    weighted = pooled if profile == "custom" else .5 * (average(special) if special else pooled) + .2 * (average(other) if other else pooled) + .3 * pooled
    return weighted + .15 * min(r["return_pct"] for r in rows)


def release_acceptance(profile, reference, winner, old_check, new_check):
    if winner["score"] <= reference["score"] + .002:
        return False, "no material gain"
    if new_check["score"] >= old_check["score"] - .05:
        return True, "multi-year gain and later-year acceptance"
    # A defensive preset can trade bull-market return for demonstrably lower drawdown.
    reduction = all(new["return_pct"] >= 0 and abs(new["max_drawdown"]) <= .5*abs(old["max_drawdown"]) and abs(old["max_drawdown"])-abs(new["max_drawdown"]) >= .005 for old,new in zip(old_check["per_year"], new_check["per_year"]))
    if profile in {"balanced", "conservative"} and reduction:
        return True, "lower drawdown accepted with bull-return tradeoff"
    return False, "later-year regression: retain reference"


def manual_presets(original, model, profile):
    parameters = {
        "random_forest": dict(n_estimators=160, max_depth=6, min_samples_leaf=16, class_weight=False),
        "hist_gradient_boosting": dict(max_iter=160, learning_rate=.035, max_leaf_nodes=9, min_samples_leaf=24, l2_regularization=.4),
        "xgboost": dict(n_estimators=160, max_depth=3, learning_rate=.04, subsample=.8, colsample_bytree=.9, min_child_weight=6., reg_lambda=2.),
        "mlp": dict(layer_size=48, layer_count=1, epochs=65, learning_rate=.0008, weight_decay=.002, dropout=.1, batch_size=256),
        "cnn": dict(window=20, channels=16, kernel_size=3, layers=1, epochs=60, learning_rate=.0008, weight_decay=.002, dropout=.1, batch_size=256),
    }
    thresholds = {"aggressive": (.51, .36), "balanced": (.55, .44), "conservative": (.59, .49), "custom": (.54, .43)}
    candidates = []
    for label, horizon, target, years, mode in [
        ("手调A：短期通用", 1, "binary", 3, "mixed"),
        ("手调B：五日通用", 5, "binary", 5, "mixed"),
        ("手调C：五日分类走势", 5, "trend5", 3, "classified"),
    ]:
        p = original.model_copy(deep=True)
        p.training_years, p.training_mode, p.horizon, p.target = years, mode, horizon, target
        p.min_samples = 160
        p.buy_threshold, p.sell_threshold = thresholds[profile]
        p.params.update(parameters[model])
        if target == "trend5":
            p.buy_threshold += .02
        candidates.append((label, p))
    return candidates


def model_neighbors(preset, model):
    result = []
    for more in (False, True):
        p = preset.model_copy(deep=True)
        factor = 1.3 if more else .75
        if model == "random_forest":
            p.params["n_estimators"] = int(round(p.params["n_estimators"] * factor))
            p.params["max_depth"] = max(2, p.params["max_depth"] + (1 if more else -1))
            p.params["min_samples_leaf"] = max(2, int(round(p.params["min_samples_leaf"] / factor)))
        elif model == "hist_gradient_boosting":
            p.params["max_iter"] = int(round(p.params["max_iter"] * factor))
            p.params["max_leaf_nodes"] = max(3, int(round(p.params["max_leaf_nodes"] * factor)))
            p.params["l2_regularization"] = max(.01, p.params["l2_regularization"] / factor)
        elif model == "xgboost":
            p.params["n_estimators"] = int(round(p.params["n_estimators"] * factor))
            p.params["learning_rate"] *= .85 if more else 1.1
            p.params["min_child_weight"] = max(1., p.params["min_child_weight"] / factor)
            p.params["reg_lambda"] = max(.01, p.params["reg_lambda"] / factor)
        else:
            width = "layer_size" if model == "mlp" else "channels"
            p.params[width] = max(8, int(round(p.params[width] * factor)))
            p.params["weight_decay"] = max(.00001, p.params["weight_decay"] / factor)
            p.params["learning_rate"] *= .85 if more else 1.15
            if model == "cnn":
                p.params["window"] = max(8, int(round(p.params["window"] * factor)))
        result.append(("学习二轮：容量增加" if more else "学习二轮：容量减少", p))
    return result


def threshold_neighbors(preset, rows):
    result = []
    for buy_delta, sell_delta, label in [(-.025, 0, "买入放宽"), (.025, 0, "买入收紧"), (0, -.06, "持有更耐心"), (0, .06, "卖出更果断")]:
        p = preset.model_copy(deep=True)
        p.buy_threshold = round(float(np.clip(p.buy_threshold + buy_delta, .01, .99)), 4)
        p.sell_threshold = round(float(np.clip(p.sell_threshold + sell_delta, 0, p.buy_threshold - .01)), 4)
        result.append(("学习三轮：" + label, p))
    if sum(r["trade_count"] for r in rows) < 12:
        p = preset.model_copy(deep=True)
        # Diagnose saturation from search-only probability distributions, never the check year.
        p.buy_threshold = round(float(np.clip(np.median([r["probability_quantiles"][3] for r in rows]) + .01, .01, .99)), 4)
        p.sell_threshold = max(0., min(p.sell_threshold, p.buy_threshold - .08))
        result.append(("学习三轮：低参与度校正", p))
    return result


def inactive_repairs(original, model, profile):
    result = []
    for horizon in (5, 10):
        p = manual_presets(original, model, profile)[1][1]
        p.horizon, p.training_years, p.training_mode = horizon, 3, "mixed"
        p.buy_threshold, p.sell_threshold = {"aggressive":(.53,.42), "balanced":(.54,.50), "conservative":(.60,.54), "custom":(.53,.49)}[profile]
        if model == "cnn":
            p.params.update(window=12, channels=24, layers=2, learning_rate=.001, weight_decay=.001)
        elif model == "mlp":
            p.params.update(layer_size=64, layer_count=2, learning_rate=.001, weight_decay=.001)
        result.append((f"反馈补调：{horizon}日机会与及时退出", p))
    return result


def summarize(result):
    stats = metrics(result["curve"], result["start_equity"])
    if not np.isclose(stats["return_pct"], result["return_pct"]) or not np.isclose(stats["max_drawdown"], result["max_drawdown"]):
        raise RuntimeError("Curve and reported metrics disagree")
    if result["fallback_count"]:
        raise RuntimeError("Local-rule fallback is not a valid ML comparison")
    values = np.array([d["probability"] for d in result["decisions"]])
    exposure = [1 - p["cash"] / p["equity"] for p in result["curve"]]
    keys = ("year", "scene", "start", "end", "bars", "data_hash", "return_pct", "benchmark_return", "excess_return", "max_drawdown", "trade_count", "fallback_count", "fees")
    row = {k: result[k] for k in keys}
    row["market_vote_scene"], row["scene"] = row["scene"], scoring_scene(row)
    row.update(mean_exposure=float(np.mean(exposure)), probability_quantiles=np.quantile(values, [0, .25, .5, .75, .9, 1]).tolist(), buy_signals=sum(d["side"] == "buy" for d in result["decisions"]), sell_signals=sum(d["side"] == "sell" for d in result["decisions"]), devices=sorted({r.get("device", "cpu") for r in result["training"]}), symbol_count=len(result["symbol_curves"]))
    return row


class Iteration:
    def __init__(self, output=OUTPUT):
        self.output = Path(output)
        self.path = self.output / "state.json"
        self.store = Store(Path("data/v2"))
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def save(self):
        write_json(self.path, self.state)

    def prepare(self):
        if self.state:
            self.load()
            return
        before = requests.get("http://127.0.0.1:8765/api/v2/settings", timeout=30).json()
        config = AppConfig.model_validate(before["config"])
        market = Market(self.store)
        frozen = Market(Store(self.output / "frozen"))
        coverage = []
        for symbol in dict.fromkeys(config.symbols + config.training_symbols):
            frame = market.read(symbol, "2010-01-01", "2025-12-31").dropna(subset=["open", "high", "low"])
            if len(frame) < 300 or frame.index[-1].year != 2025:
                raise RuntimeError(f"{symbol}: cached history is not sufficient to freeze")
            frozen.write(symbol, frame, "frozen local OHLC snapshot")
            read = frozen.read(symbol)
            coverage.append(dict(symbol=symbol, first=str(read.index[0].date()), last=str(read.index[-1].date()), bars=len(read), hash=digest(read), providers=sorted(frame.source.unique())))
        manifest = dict(coverage=coverage, created=time.time(), universe=config.symbols, training_universe=config.training_symbols)
        manifest["hash"] = hashlib.sha256(dumps(coverage).encode()).hexdigest()
        self.state = dict(before=before, manifest=manifest, evaluations={}, models={}, failures=[], account_before=self.store.meta("account"), protocol=dict(search_years=SEARCH_YEARS, check_years=CHECK_YEARS, waves=["manual target/window/model changes", "bidirectional capacity learning", "threshold feedback"], caveats="2025 was previously used by this project: not a pristine unseen holdout. Daily next-open simulation, fees and slippage, no dividend reinvestment; current ETF universe has survivorship bias."))
        write_json(self.output / "before.json", before)
        write_json(self.output / "manifest.json", manifest)
        backup = config.model_copy(update={"name": "20档迭代调优前备份"})
        response = requests.post("http://127.0.0.1:8765/api/v2/plans", json=backup.model_dump(), timeout=30)
        response.raise_for_status()
        self.state["backup_plan"] = response.json()
        self.save()
        self.load()

    def load(self):
        self.config = AppConfig.model_validate(self.state["before"]["config"])
        frozen = Market(Store(self.output / "frozen"))
        self.histories = {r["symbol"]: frozen.read(r["symbol"]) for r in self.state["manifest"]["coverage"]}
        if any(digest(self.histories[r["symbol"]]) != r["hash"] for r in self.state["manifest"]["coverage"]):
            raise RuntimeError("Frozen history changed")

    def evaluate(self, model, profile, label, preset, wave, years=SEARCH_YEARS):
        cfg = self.config.model_copy(deep=True)
        cfg.engine, cfg.model, cfg.profile, cfg.auto_profile = "ml", model, profile, False
        cfg.models[model][profile] = preset
        cfg = AppConfig.model_validate(cfg.model_dump())
        fp = hashlib.sha256(dumps(dict(config=cfg.model_dump(), years=years, manifest=self.state["manifest"]["hash"])).encode()).hexdigest()
        if fp in self.state["evaluations"]:
            return self.state["evaluations"][fp]
        rows = []
        for year in years:
            path = self.output / "runs" / model / f"{fp}-{year}.json.gz"
            if path.exists():
                with gzip.open(path, "rt", encoding="utf-8") as source:
                    full = json.load(source)
            else:
                print(f"RUN {model}/{profile} {label} {year}", flush=True)
                full = run_backtest(self.store, JobRequest(year=year, config=cfg), histories={s: f.loc[:f"{year}-12-31"] for s, f in self.histories.items()})
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                with gzip.open(temporary, "wt", encoding="utf-8") as target:
                    target.write(dumps(full))
                temporary.replace(path)
            row = summarize(full)
            if row["symbol_count"] != len(cfg.symbols):
                raise RuntimeError(f"{model}/{profile}/{year}: incomplete universe")
            hashes = self.state.setdefault("year_hashes", {})
            if str(year) in hashes and hashes[str(year)] != row["data_hash"]:
                raise RuntimeError("Different market prices were compared")
            hashes[str(year)] = row["data_hash"]
            rows.append(row)
        item = dict(fingerprint=fp, round=wave, label=label, phase=label.split("：")[0], profile=profile, score=objective(rows, profile), complete=True, valid_years=len(years), expected_years=len(years), specialized_years=sum(scoring_scene(r) == {"aggressive":"bull","balanced":"sideways","conservative":"bear","custom":None}[profile] for r in rows), per_year=rows, config=cfg.model_dump(), mean_return=float(np.mean([r["return_pct"] for r in rows])), mean_excess=float(np.mean([r["excess_return"] for r in rows])), worst_return=min(r["return_pct"] for r in rows))
        self.state["evaluations"][fp] = item
        self.save()
        print(f"SCORE {model}/{profile} {label}: {item['score']:.4f}; returns={[round(r['return_pct']*100,2) for r in rows]}; trades={[r['trade_count'] for r in rows]}", flush=True)
        return item

    def search(self, model, phase="all"):
        result = self.state["models"].setdefault(model, {})
        if phase in {"manual", "all"}:
            for profile in PROFILES:
                if result.get(profile, {}).get("manual"):
                    continue
                base = self.config.models[model][profile]
                old = self.evaluate(model, profile, "原参数", base, 0)
                items = [old] + [self.evaluate(model, profile, label, p, 1) for label, p in manual_presets(base, model, profile)]
                result[profile] = dict(baseline=old["fingerprint"], center=max(items, key=lambda v:v["score"])["fingerprint"], manual=[i["fingerprint"] for i in items])
                self.save()
        if phase in {"refine", "all"}:
            for profile in PROFILES:
                selected = result[profile]
                if selected.get("threshold"):
                    continue
                center = self.state["evaluations"][selected["center"]]
                if not selected.get("repair") and sum(r["trade_count"] >= 4 for r in center["per_year"]) < 2:
                    p = Preset.model_validate(center["config"]["models"][model][profile])
                    items = [center] + [self.evaluate(model, profile, label, candidate, 2) for label, candidate in inactive_repairs(p, model, profile)]
                    center = max(items, key=lambda v:v["score"])
                    selected["repair"] = [i["fingerprint"] for i in items]
                    selected["center"] = center["fingerprint"]
                    self.save()
                if not selected.get("capacity"):
                    p = Preset.model_validate(center["config"]["models"][model][profile])
                    items = [center] + [self.evaluate(model, profile, label, candidate, 2) for label, candidate in model_neighbors(p, model)]
                    center = max(items, key=lambda v:v["score"])
                    selected["capacity"] = [i["fingerprint"] for i in items]
                    selected["center"] = center["fingerprint"]
                    self.save()
                p = Preset.model_validate(center["config"]["models"][model][profile])
                items = [center] + [self.evaluate(model, profile, label, candidate, 3) for label, candidate in threshold_neighbors(p, center["per_year"])]
                center = max(items, key=lambda v:v["score"])
                selected["threshold"] = [i["fingerprint"] for i in items]
                selected["center"] = center["fingerprint"]
                self.save()
                print(f"SELECT {model}/{profile}: {center['label']} {center['score']:.4f}", flush=True)
        self.report()

    def audit(self, model):
        for profile in PROFILES:
            record = self.state["models"][model][profile]
            rows = []
            for origin in ("baseline", "center"):
                chosen = self.state["evaluations"][record[origin]]
                preset = Preset.model_validate(chosen["config"]["models"][model][profile])
                item = self.evaluate(model, profile, "后段检查：" + origin, preset, 4, CHECK_YEARS)
                rows.append(dict(origin=origin, fingerprint=item["fingerprint"]))
            record["check"] = rows
            self.save()
        self.report()

    def feedback(self, model):
        if model != "cnn":
            return
        for profile in ("balanced", "custom"):
            record = self.state["models"][model][profile]
            if record.get("feedback"):
                continue
            center = self.state["evaluations"][record["center"]]
            if sum(r["trade_count"] >= 4 for r in center["per_year"]) >= 2:
                continue
            original = self.config.models[model][profile]
            candidates = []
            for window, horizon, years in [(20,1,3), (20,5,5), (3,1,3), (5,5,3)]:
                p = manual_presets(original, model, profile)[1 if horizon == 5 else 0][1]
                p.training_mode, p.training_years, p.horizon = "mixed", years, horizon
                p.params["window"] = window
                if window < 10:
                    p.params.update(channels=24, layers=1, learning_rate=.001, weight_decay=.001, dropout=.05, epochs=70)
                for buy, sell in [(.53,.48), (.56,.50), (.59,.53)]:
                    candidate = p.model_copy(deep=True)
                    candidate.buy_threshold, candidate.sell_threshold = buy, sell
                    candidates.append(self.evaluate(model, profile, f"反馈四轮：窗口{window} 预测{horizon}日 {buy}/{sell}", candidate, 4))
            chosen = max([center] + candidates, key=lambda v:v["score"])
            record["feedback"] = [v["fingerprint"] for v in candidates]
            record["center"] = chosen["fingerprint"]
            self.save()
            print(f"FEEDBACK SELECT {model}/{profile}: {chosen['label']}", flush=True)
        self.report()

    def publish(self):
        verification = json.loads((self.output / "verification.json").read_text(encoding="utf-8"))
        centers = {f"{m}:{p}":self.state["models"][m][p]["center"] for m in MODEL_NAMES for p in PROFILES}
        if verification["status"] != "passed" or verification["candidates"] != len(self.state["evaluations"]) or verification["manifest_hash"] != self.state["manifest"]["hash"] or verification["centers"] != centers:
            raise RuntimeError("Verification is absent or stale; do not publish unverified parameters")
        current = requests.get("http://127.0.0.1:8765/api/v2/settings", timeout=30).json()
        if current["revision"] != self.state["before"]["revision"]:
            raise RuntimeError("Settings changed during research; refusing to overwrite")
        cfg = AppConfig.model_validate(current["config"])
        changes = []
        for model in MODEL_NAMES:
            rounds = max(v["round"] for v in self.state["evaluations"].values() if v["config"]["model"] == model and [r["year"] for r in v["per_year"]] == SEARCH_YEARS)
            summary = dict(best={}, rounds=rounds, total=0, protocol=self.state["protocol"])
            for profile in PROFILES:
                record = self.state["models"][model][profile]
                if "threshold" not in record or "check" not in record:
                    raise RuntimeError("Every model/preset must finish all research stages")
                winner = self.state["evaluations"][record["center"]]
                old = self.state["evaluations"][record["baseline"]]
                check = {r["origin"]: self.state["evaluations"][r["fingerprint"]] for r in record["check"]}
                adopted, reason = release_acceptance(profile, old, winner, check["baseline"], check["center"])
                selected = winner if adopted else old
                record["adopted"] = adopted
                record["deployment_reason"] = reason
                cfg.models[model][profile] = Preset.model_validate(selected["config"]["models"][model][profile])
                selected_check = check["center" if adopted else "baseline"]["per_year"]
                summary["best"][profile] = {**selected, "check_years":selected_check, "deployment_reason":record["deployment_reason"]}
                if adopted:
                    changes.append([model, profile])
            request = JobRequest(kind="learn", config=cfg.model_copy(update={"model":model, "name":f"迭代验证 · {MODEL_NAMES[model]} · 四档"}), years=SEARCH_YEARS)
            job_id = hashlib.sha256((str(self.output) + model).encode()).hexdigest()[:32]
            with self.store.connect() as c:
                c.execute("INSERT OR REPLACE INTO jobs(id,kind,status,progress,message,request,created,updated,result) VALUES(?,'learn','done',100,?,?,?,?,?)", (job_id, "20档迭代研究完成（评分公式见本地报告）", dumps(request.model_dump()), time.time(), time.time(), dumps(summary)))
            items = [v for v in self.state["evaluations"].values() if v["config"]["model"] == model and [r["year"] for r in v["per_year"]] == SEARCH_YEARS]
            for item in items:
                self.store.save_trial(job_id, item["fingerprint"], item)
            summary["total"] = len(items)
            self.store.update_job(job_id, result=summary)
            self.state["models"][model]["job_id"] = job_id
        cfg.name = "五模型四档 · 迭代验证版"
        cfg.engine, cfg.auto_profile = "ml", False
        if self.store.meta("account") != self.state["account_before"]:
            raise RuntimeError("Paper account changed during research; investigate before deployment")
        response = requests.post("http://127.0.0.1:8765/api/v2/plans", json=cfg.model_dump(), timeout=30)
        response.raise_for_status()
        saved = requests.put("http://127.0.0.1:8765/api/v2/settings", json={"revision":current["revision"],"config":cfg.model_dump()}, timeout=30)
        saved.raise_for_status()
        self.state["applied_revision"] = saved.json()["revision"]
        self.state["changes"] = changes
        self.save()
        write_json(self.output / "ready.json", saved.json())
        self.report()

    def report(self):
        lines = ["# 五模型四档迭代研究", "", "2021–2024 选参数；2025 后段检查不用于候选排名，但用于发布验收。不是从未用过的留出集。", "", "三轮：手调预测周期/分类/窗口/模型 → 双向模型容量学习 → 根据参与度细调交易阈值。CNN 均衡、自定义另补第四轮短序列和买卖组合。20 组独立选择。", "", "所有候选共用不可变行情快照，按每年开始截断训练，次日开盘成交，含手续费滑点。账户和本地/AI 设置不变。", "", "评分：收益 + 0.35×超额 + 回撤系数×负回撤；回撤系数激进0.35/均衡0.7/保守1.2/自定义0.7。三档专项/其他/全部权重0.5/0.2/0.3，自定义全部平均，另加0.15×最差年收益。激进牛市低参与度有最高0.03扣分。", "", "评价标签按等金额持有基准年收益：≥10%强市、≤-5%弱市、其余震荡，仅用于事后评分。在线策略和训练标签算法不改。", "", "| 模型 | 档位 | 旧评分 | 候选评分 | 2021–24 旧平均 | 候选平均 | 2025 旧→候选 | 状态 |", "|---|---|---:|---:|---:|---:|---:|---|"]
        for model, group in self.state.get("models", {}).items():
            for profile in PROFILES:
                if profile not in group:
                    continue
                r = group[profile]
                old, new = (self.state["evaluations"][r[k]] for k in ("baseline", "center"))
                checks = {x["origin"]: self.state["evaluations"][x["fingerprint"]] for x in r.get("check", [])}
                audit = "待检查" if not checks else f"{checks['baseline']['mean_return']:.2%} → {checks['center']['mean_return']:.2%}"
                status = "已采用" if r.get("adopted") else "保留参照" if "adopted" in r else new["label"]
                lines.append(f"| {MODEL_NAMES[model]} | {PROFILES[profile]} | {old['score']:.4f} | {new['score']:.4f} | {old['mean_return']:.2%} | {new['mean_return']:.2%} | {audit} | {status} |")
        annuals = sum(len(v["per_year"]) for v in self.state["evaluations"].values())
        lines += ["", f"完成 {len(self.state['evaluations'])} 组候选评价，{annuals} 次年度模拟。失败重试单独记录，不算通过。", "", "完整参数、逐年收益/回撤/交易/平均仓位/概率分布见 state.json；完整曲线、交易和训练记录见 runs/*.json.gz；冻结行情与覆盖/来源指纹见 frozen/、manifest.json。", "", "仍有多重搜索过拟合风险、当前ETF池幸存者偏差、分红未模拟、涨跌分数未校准等限制。不能宣称全局最优或未来获利。"]
        self.output.mkdir(parents=True, exist_ok=True)
        (self.output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["prepare", "manual", "refine", "feedback", "audit", "publish", "all"], default="all")
    parser.add_argument("--models", nargs="+", choices=list(MODEL_NAMES), default=list(MODEL_NAMES))
    parser.add_argument("--output", default=str(OUTPUT))
    parser.add_argument("--isolated", action="store_true", help="Run one model per fresh process, retry a failed checkpoint once")
    args = parser.parse_args()
    if args.isolated and args.phase in {"manual", "refine", "feedback", "audit"}:
        for model in args.models:
            for attempt in range(2):
                completed = subprocess.run([sys.executable, __file__, "--phase", args.phase, "--models", model, "--output", args.output])
                if completed.returncode == 0:
                    break
                path = Path(args.output) / "state.json"
                state = json.loads(path.read_text(encoding="utf-8"))
                state["failures"].append(dict(model=model, phase=args.phase, attempt=attempt+1, exit_code=completed.returncode, recorded=time.time(), log="native.log"))
                write_json(path, state)
                if attempt == 1:
                    raise SystemExit(completed.returncode)
                print(f"RETRY {model}: interrupted checkpoint; prior annual results retained", flush=True)
        return
    runner = Iteration(args.output)
    runner.output.mkdir(parents=True, exist_ok=True)
    with (runner.output / "native.log").open("a", encoding="utf-8") as log:
        faulthandler.enable(file=log)
        try:
            runner.prepare()
            if args.phase in {"manual", "refine", "all"}:
                for model in args.models:
                    runner.search(model, args.phase)
            if args.phase in {"feedback", "all"}:
                for model in args.models:
                    runner.feedback(model)
            if args.phase in {"audit", "all"}:
                for model in args.models:
                    runner.audit(model)
            if args.phase in {"publish", "all"}:
                if args.phase == "all":
                    from verify_iterative import verify
                    verify(runner.output)
                    runner.prepare()
                runner.publish()
        except Exception:
            with (runner.output / "python-errors.log").open("a", encoding="utf-8") as errors:
                traceback.print_exc(file=errors)
            raise


if __name__ == "__main__":
    main()
