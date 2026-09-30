"""Bounded, resumable parameter research against the running StockLab service."""
from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

import requests

from ai_stock_bot.v2.schema import AppConfig, MODEL_NAMES, PROFILES

SEARCH_YEARS = [2021, 2022, 2023]
AUDIT_YEARS = [2024, 2025]
PRESETS = ("aggressive", "balanced", "conservative")
DEFAULT_MODEL = "hist_gradient_boosting"
DEPLOYED_UPDATES = {("random_forest", "conservative"), ("hist_gradient_boosting", "balanced")}


def verify_audit(result):
    expected = {(year, source) for year in AUDIT_YEARS for source in ("baseline", "selected")}
    for profile in PRESETS:
        rows = result.get("audit", {}).get(profile, [])
        if len(rows) != len(expected) or {(r["year"], r["source"]) for r in rows} != expected:
            raise RuntimeError(f"{profile} lacks complete later-year comparisons")
        for year in AUDIT_YEARS:
            pair = [r for r in rows if r["year"] == year]
            if len({(r["start"], r["end"], r["bars"], r["data_hash"]) for r in pair}) != 1:
                raise RuntimeError(f"{profile}/{year} compared different market data")
            if any(r["fallback_count"] for r in pair):
                raise RuntimeError(f"{profile}/{year} silently used local fallback")


def manual_adjustments(original):
    config = copy.deepcopy(original)
    config["engine"], config["auto_profile"] = "ml", False
    params = {
        "random_forest": dict(n_estimators=180, max_depth=5, min_samples_leaf=24),
        "hist_gradient_boosting": dict(max_iter=120, learning_rate=.035, max_leaf_nodes=7, min_samples_leaf=30, l2_regularization=.5),
        "xgboost": dict(n_estimators=140, max_depth=3, learning_rate=.035, subsample=.8, colsample_bytree=.8, min_child_weight=8., reg_lambda=3.),
        "mlp": dict(layer_size=48, layer_count=2, epochs=50, learning_rate=.0008, weight_decay=.003, dropout=.15, batch_size=256, early_stopping=True),
        "cnn": dict(window=24, channels=16, kernel_size=3, layers=2, epochs=45, learning_rate=.0008, weight_decay=.003, dropout=.15, batch_size=256, early_stopping=True),
    }
    thresholds = {
        "random_forest": [(.52, .26), (.56, .35), (.60, .43)],
        "hist_gradient_boosting": [(.53, .27), (.57, .35), (.62, .44)],
        "xgboost": [(.53, .25), (.57, .34), (.62, .43)],
        "mlp": [(.53, .26), (.57, .35), (.62, .43)],
        "cnn": [(.53, .26), (.57, .35), (.62, .43)],
    }
    for model in MODEL_NAMES:
        for profile, (buy, sell) in zip(PRESETS, thresholds[model]):
            preset = config["models"][model][profile]
            preset.update(training_years=3, training_mode="mixed", target="binary", horizon=1, min_samples=160, buy_threshold=buy, sell_threshold=sell)
            preset["params"].update(params[model])
    return AppConfig.model_validate(config).model_dump()


class Research:
    def __init__(self, url, output):
        self.url = url.rstrip("/") + "/api/v2"
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.path = self.output / "state.json"
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"jobs": {}}

    def api(self, method, path, **kwargs):
        response = requests.request(method, self.url + path, timeout=30, **kwargs)
        response.raise_for_status()
        return response.json()

    def write(self, name, value):
        path = self.output / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def save(self):
        self.write("state.json", self.state)

    def prepare(self):
        if "baseline" in self.state:
            return
        saved = self.api("GET", "/settings")
        self.state["baseline"], self.state["revision"] = saved["config"], saved["revision"]
        backup = copy.deepcopy(saved["config"])
        backup["name"] = "调参前备份 · 2026-09-27"
        self.state["backup_plan"] = self.api("POST", "/plans", json=backup)
        self.state["manual"] = manual_adjustments(saved["config"])
        self.state["protocol"] = {
            "search_years": SEARCH_YEARS, "audit_years": AUDIT_YEARS,
            "trials_per_profile": 4, "weights": {"specialized": .5, "fallback": .2, "composite": .3},
            "selection": "Select only by 2021-2023 scores; later years are never used to rank candidates.",
            "minimum_score_gain": .002,
            "caveats": "Later years were examined in earlier project research; this is not a pristine unseen holdout. Daily next-open fills, fees/slippage; no dividend reinvestment. Annual market labels are ex-post for scoring only.",
        }
        self.write("before.json", saved["config"])
        self.write("manual.json", self.state["manual"])
        self.save()

    def run_job(self, key, request):
        if key not in self.state["jobs"]:
            job = self.api("POST", "/jobs", json=request)
            self.state["jobs"][key] = job["id"]
            self.save()
        job_id = self.state["jobs"][key]
        last = 0.
        while True:
            status = self.api("GET", f"/jobs/{job_id}/status")
            if status["status"] == "done":
                detail = self.api("GET", f"/jobs/{job_id}")
                print(f"DONE {key}: {job_id}", flush=True)
                return detail["result"]
            if status["status"] in {"failed", "interrupted", "stopped"}:
                raise RuntimeError(f"{key}: {status['status']}: {status.get('error') or status['message']}")
            if time.monotonic() - last > 20:
                print(f"{key}: {status['progress']:.0f}% {status['message']}", flush=True)
                last = time.monotonic()
            time.sleep(2)

    def search(self, model):
        for phase, source, trials in [("baseline", "baseline", 1), ("search", "manual", 4)]:
            config = copy.deepcopy(self.state[source])
            config.update(engine="ml", model=model, auto_profile=False, name=f"调参验证 · {MODEL_NAMES[model]} · {phase}")
            result = self.run_job(f"{model}:{phase}", {"kind": "learn", "years": SEARCH_YEARS, "trials": trials, "continuous": False, "config": config})
            self.state.setdefault("results", {}).setdefault(model, {})[phase] = result
            self.save()
        results = self.state["results"][model]
        selected = {}
        for profile in PRESETS:
            old = results["baseline"]["best"].get(profile)
            new = results["search"]["best"].get(profile)
            if not old or not new:
                raise RuntimeError(f"{model}/{profile} lacks a complete multi-year result")
            chosen = new if new["score"] > old["score"] + .002 else old
            selected[profile] = {"origin": "search" if chosen is new else "baseline", **chosen}
            print(f"SELECT {model}/{profile}: {selected[profile]['origin']} {old['score']:.4f} -> {chosen['score']:.4f}", flush=True)
        self.state["results"][model]["selected"] = selected
        self.save()

    def audit(self, model):
        for profile in PRESETS:
            chosen = self.state["results"][model]["selected"][profile]
            rows = []
            for year in AUDIT_YEARS:
                for source in ("baseline", "selected"):
                    config = copy.deepcopy(self.state["baseline"] if source == "baseline" else chosen["config"])
                    config.update(engine="ml", model=model, profile=profile, auto_profile=False, name=f"后段验证 · {MODEL_NAMES[model]} · {PROFILES[profile]} · {source}")
                    result = self.run_job(f"{model}:{profile}:{source}:{year}", {"kind": "backtest", "year": year, "config": config})
                    training = result["training"]
                    if result["fallback_count"] or len(result["symbol_curves"]) != len(config["symbols"]):
                        raise RuntimeError(f"{model}/{profile}/{year}: fallback or incomplete trading universe")
                    rows.append({"source": source, **{k: result[k] for k in ("year", "scene", "start", "end", "bars", "data_hash", "return_pct", "benchmark_return", "excess_return", "max_drawdown", "trade_count", "fallback_count")}, "devices": sorted({r.get("device", "cpu") for r in training}), "training_samples": [r["samples"] for r in training]})
            self.state["results"][model].setdefault("audit", {})[profile] = rows
            self.save()

    def apply(self):
        if any("audit" not in self.state.get("results", {}).get(m, {}) for m in MODEL_NAMES):
            raise RuntimeError("All five models must finish before applying")
        for result in self.state["results"].values():
            verify_audit(result)
        current = self.api("GET", "/settings")
        selected_models = copy.deepcopy(self.state["baseline"]["models"])
        for model in MODEL_NAMES:
            for profile in PRESETS:
                chosen = self.state["results"][model]["selected"][profile]
                selected_models[model][profile] = chosen["config"]["models"][model][profile]
        deployed_models = copy.deepcopy(self.state["baseline"]["models"])
        for model, profile in DEPLOYED_UPDATES:
            deployed_models[model][profile] = selected_models[model][profile]
        if current["config"]["models"] not in (self.state["baseline"]["models"], selected_models, deployed_models):
            raise RuntimeError("Model parameters changed on another device; refusing to overwrite them")
        config = copy.deepcopy(current["config"])
        candidates = copy.deepcopy(config)
        candidates["models"] = selected_models
        candidates["name"] = "学习候选 · 未全量验收"
        if "candidate_plan" not in self.state:
            self.state["candidate_plan"] = self.api("POST", "/plans", json=candidates)
        for model in MODEL_NAMES:
            for profile in PRESETS:
                chosen = self.state["results"][model]["selected"][profile]
                config["models"][model][profile] = chosen["config"]["models"][model][profile] if (model, profile) in DEPLOYED_UPDATES else self.state["baseline"]["models"][model][profile]
        config.update(engine="ml", model=DEFAULT_MODEL, profile="balanced", auto_profile=False, name="多年份验证 · HGB 均衡")
        config = AppConfig.model_validate(config).model_dump()
        self.state["ready_plan"] = self.api("POST", "/plans", json=config)
        saved = self.api("PUT", "/settings", json={"config": config, "revision": current["revision"]})
        self.state["applied_revision"] = saved["revision"]
        self.state["deployment"] = {"updates": [list(v) for v in sorted(DEPLOYED_UPDATES)], "reason": "Expert deployment review uses later-year results as well as search-period results. It is not an untouched final holdout: most candidates materially regress in stronger markets or become inactive."}
        self.write("ready.json", saved["config"])
        self.write("candidates.json", candidates)
        self.save()
        self.report()

    def report(self):
        rows = ["# StockLab 参数调优验证", "", "搜索：2021–2023。后段检查：2024、2025（不参与候选排名，但用于最终发布判断）。", "", "金额、手续费、滑点、交易池保持一致；三个档位独立选择。后段年份在旧项目中曾使用过，不是真正从未见过的留出集。", "", "下表是搜索候选的表现，不是全部已经采用。只有随机森林保守档、HGB 均衡档默认采用调整版，其余十三档保留原参数；所有候选另外保存为实验方案。", "", "每个模型内新旧参数的行情指纹相同。不同模型组在历史行情重新拉取后存在指纹差异，因此不对五种模型直接排名。", "", "| 模型 | 档位 | 搜索选择 | 搜索旧评分 | 新评分 | 2024 旧→候选 | 2025 旧→候选 |", "|---|---|---|---:|---:|---:|---:|"]
        for model in MODEL_NAMES:
            result = self.state["results"][model]
            for profile in PRESETS:
                chosen = result["selected"][profile]
                audit = {(r["year"], r["source"]): r for r in result["audit"][profile]}
                changes = [f"{audit[y,'baseline']['return_pct']:.2%} → {audit[y,'selected']['return_pct']:.2%}" for y in AUDIT_YEARS]
                rows.append(f"| {MODEL_NAMES[model]} | {PROFILES[profile]} | {chosen['origin']} | {result['baseline']['best'][profile]['score']:.4f} | {chosen['score']:.4f} | {' | '.join(changes)} |")
        rows += ["", "默认选用 HistGradientBoosting 均衡：搜索期树模型中评分更好，且不依赖 GPU；不是用后段测试挑最高收益模型。自定义档、账户、本地和 AI 设置不改动。", "", "所有候选、各年度收益/回撤/交易次数保存在数据库；完整参数导出见 ready.json，原参数见 before.json。", "", "历史回测不保证未来收益；本次没有实盘下单，也没有调用大模型。"]
        (self.output / "REPORT.md").write_text("\n".join(rows) + "\n", encoding="utf-8")


def main():
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", default="data/v2/research/2026-09-27")
    parser.add_argument("--models", nargs="+", choices=list(MODEL_NAMES), default=list(MODEL_NAMES))
    parser.add_argument("--phase", choices=["prepare", "search", "audit", "apply", "all"], default="all")
    args = parser.parse_args()
    research = Research(args.url, args.output)
    research.prepare()
    if args.phase in {"search", "all"}:
        for model in args.models:
            research.search(model)
    if args.phase in {"audit", "all"}:
        for model in args.models:
            research.audit(model)
    if args.phase in {"apply", "all"}:
        research.apply()


if __name__ == "__main__":
    main()
