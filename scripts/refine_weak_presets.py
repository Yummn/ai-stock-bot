"""Focused preset research against an immutable pre-optimization baseline."""
from __future__ import annotations

import argparse
import faulthandler
from contextlib import closing
import hashlib
import gzip
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(variable, "2")

import numpy as np
import requests

from iterate_stocklab import Iteration, model_neighbors, objective, summarize, threshold_neighbors, write_json
from ai_stock_bot.v2.market import Market, digest
from ai_stock_bot.v2.backtest import run_backtest
from ai_stock_bot.v2.evaluation import read_gzip, write_gzip
from ai_stock_bot.v2.schema import AppConfig, JobRequest, MODEL_NAMES, PROFILES, Preset
from ai_stock_bot.v2.store import Store, dumps

OUTPUT = Path("data/v3/research/weak-2026-09-28")
API = "http://127.0.0.1:8768/api/v2"


class Focused(Iteration):
    def __init__(self, output=OUTPUT):
        self.output = Path(output)
        self.path = self.output / "state.json"
        self.store = Store(self.output / "compute")
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def prepare(self):
        if self.state:
            self.load()
            return
        live = Store(Path("data/v3"))
        before = requests.get(API + "/settings", timeout=20).json()
        config = AppConfig.model_validate(before["config"])
        backup = self.output / "before.sqlite3"
        with live.connect() as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
        defaults = live.capture_defaults(before["revision"])
        write_json(self.output / "saved-defaults.json", defaults)
        write_json(self.output / "before.json", before)
        previous = json.loads(Path("data/v2/research/iterative-2026-09-27/state.json").read_text(encoding="utf-8"))
        diagnosis, targets = [], []
        for model in MODEL_NAMES:
            weak = []
            for profile in PROFILES:
                record = previous["models"][model][profile]
                item = previous["evaluations"][record["center" if record.get("adopted") else "baseline"]]
                if config.models[model][profile].model_dump() != item["config"]["models"][model][profile]:
                    raise RuntimeError(f"Historical evidence no longer matches {model}/{profile}")
                checks = {v["origin"]: previous["evaluations"][v["fingerprint"]] for v in record["check"]}
                later = checks["center" if record.get("adopted") else "baseline"]
                row = dict(model=model, profile=profile, score=item["score"], mean_return=item["mean_return"],
                           per_year=item["per_year"] + later["per_year"], reference="iterative-2026-09-27")
                diagnosis.append(row)
                if profile != "aggressive":
                    weak.append(row)
            targets.append([model, min(weak, key=lambda r: r["score"])["profile"]])
        if ["cnn", "conservative"] not in targets:
            targets.append(["cnn", "conservative"])
        market, frozen = Market(live), Market(Store(self.output / "frozen"))
        coverage = []
        for symbol in dict.fromkeys(config.symbols + config.training_symbols):
            frame = market.read(symbol, "2010-01-01", "2025-12-31")
            if len(frame) < 300 or frame.index[-1].year != 2025:
                raise RuntimeError(f"Insufficient cached history for {symbol}")
            frozen.write(symbol, frame, "frozen local OHLC snapshot")
            copy = frozen.read(symbol)
            coverage.append(dict(symbol=symbol, first=str(copy.index[0].date()), last=str(copy.index[-1].date()),
                                 bars=len(copy), hash=digest(copy), providers=sorted(frame.source.unique())))
        manifest = dict(coverage=coverage, hash=hashlib.sha256(dumps(coverage).encode()).hexdigest())
        self.state = dict(before=before, account_before=live.meta("account"), defaults=defaults, manifest=manifest,
                          evaluations={}, models={}, failures=[], diagnosis=diagnosis, targets=targets,
                          protocol=dict(search_years=[2021,2022,2023,2024], check_years=[2025],
                                        stress_years=[2022,2024], stress_seed=43, cost_multiplier=2,
                                        caveat="All historical years were previously examined. Checks are regression gates, not pristine holdouts."))
        self.save()
        write_json(self.output / "manifest.json", manifest)
        self.load()
        self.report()

    def search_pair(self, model, profile, expand=False, participation=False):
        records = self.state["models"].setdefault(model, {})
        prior = records.get(profile, {})
        if prior.get("participation") or prior.get("expanded") and not participation or prior.get("checked") and not expand:
            return
        original = self.config.models[model][profile]
        baseline = self.evaluate(model, profile, "当前默认参照", original, 0)
        capacity = [baseline] + [self.evaluate(model, profile, label, p, 1) for label, p in model_neighbors(original, model)]
        center = max(capacity, key=lambda v: v["score"])
        preset = Preset.model_validate(center["config"]["models"][model][profile])
        candidates = capacity + [self.evaluate(model, profile, label, p, 2) for label, p in threshold_neighbors(preset, center["per_year"])]
        if expand:
            variations = [("预测跨度", "horizon", 5 if original.horizon == 1 else 1),
                          ("训练窗口", "training_years", 5 if original.training_years == 3 else 3),
                          ("样本场景", "training_mode", "classified" if original.training_mode == "mixed" else "mixed"),
                          ("分类目标", "target", "trend5" if original.target == "binary" else "binary")]
            for label, key, value in variations:
                p = original.model_copy(deep=True)
                setattr(p, key, value)
                candidates.append(self.evaluate(model, profile, "结构复核：" + label, p, 5))
            if model == "cnn" and original.params["window"] < 8:
                for index, (label, p) in enumerate(model_neighbors(original, model)):
                    p.params["window"] = max(2, round(original.params["window"] * (.75 if index == 0 else 1.3)))
                    candidates.append(self.evaluate(model, profile, "短序列复核：" + label, p, 5))
        if participation:
            p = original.model_copy(deep=True)
            p.allow_add = not original.allow_add
            candidates.append(self.evaluate(model, profile, "参与度对照：允许加仓" if p.allow_add else "参与度对照：不再加仓", p, 6))
        winner = max(candidates, key=lambda v: v["score"])
        chosen = Preset.model_validate(winner["config"]["models"][model][profile])
        reference_check = self.evaluate(model, profile, "后段参照", original, 3, [2025])
        selected_check = self.evaluate(model, profile, "后段候选", chosen, 3, [2025])
        previous = self.config
        try:
            self.config = previous.model_copy(deep=True)
            self.config.seed = 43
            self.config.risk.commission_rate *= 2
            self.config.risk.slippage_bps *= 2
            reference_stress = self.evaluate(model, profile, "双倍费用与新种子参照", original, 4, [2022,2024])
            selected_stress = self.evaluate(model, profile, "双倍费用与新种子候选", chosen, 4, [2022,2024])
        finally:
            self.config = previous
        checks = {
            "search_improved": winner["score"] > baseline["score"] + .003,
            "worst_year_retained": winner["worst_return"] >= baseline["worst_return"] - .01,
            "later_score_retained": selected_check["score"] >= reference_check["score"] - .015,
            "later_drawdown_retained": selected_check["per_year"][0]["max_drawdown"] >= reference_check["per_year"][0]["max_drawdown"] - .02,
            "stress_score_retained": selected_stress["score"] >= reference_stress["score"] - .005,
            "stress_drawdown_retained": min(r["max_drawdown"] for r in selected_stress["per_year"]) >= min(r["max_drawdown"] for r in reference_stress["per_year"]) - .02,
        }
        if profile == "conservative":
            checks["defensive_drawdown_retained"] = min(r["max_drawdown"] for r in winner["per_year"]) >= min(r["max_drawdown"] for r in baseline["per_year"]) - .005
        records[profile] = dict(baseline=baseline["fingerprint"], center=winner["fingerprint"],
                                candidates=[v["fingerprint"] for v in candidates], checked=True, gates=checks,
                                adopted=all(checks.values()), check_baseline=reference_check["fingerprint"],
                                check_center=selected_check["fingerprint"], stress_baseline=reference_stress["fingerprint"],
                                stress_center=selected_stress["fingerprint"], expanded=expand,
                                previous_round=prior if expand else None, participation=participation)
        self.save()
        self.report()
        print(f"FINISHED {model}/{profile}: adopted={all(checks.values())} gates={checks}", flush=True)

    def evaluate(self, model, profile, label, preset, wave, years=(2021,2022,2023,2024)):
        if model in {"mlp", "cnn"}:
            config = self.config.model_copy(deep=True)
            config.engine, config.model, config.profile, config.auto_profile = "ml", model, profile, False
            config.models[model][profile] = preset
            config = AppConfig.model_validate(config.model_dump())
            fp = hashlib.sha256(dumps(dict(config=config.model_dump(), years=list(years), manifest=self.state["manifest"]["hash"])).encode()).hexdigest()
            if fp not in self.state["evaluations"]:
                request = self.output / "requests" / f"{fp}.json"
                write_json(request, {"config":config.model_dump(), "fingerprint":fp})
                for year in years:
                    path = self.output / "runs" / model / f"{fp}-{year}.json.gz"
                    if path.exists():
                        continue
                    for attempt in range(2):
                        print(f"ISOLATED {model}/{profile} {label} {year}", flush=True)
                        completed = subprocess.run([sys.executable, __file__, "--phase", "annual", "--output", str(self.output), "--request", str(request), "--year", str(year)])
                        if completed.returncode == 0 and path.exists():
                            break
                        self.state["failures"].append(dict(model=model, profile=profile, year=year, fingerprint=fp, attempt=attempt+1, exit_code=completed.returncode))
                        self.save()
                        if attempt:
                            raise RuntimeError(f"Annual calculation failed: {model}/{profile}/{year}")
        return super().evaluate(model, profile, label, preset, wave, list(years))

    def annual(self, request_path, year):
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        config = AppConfig.model_validate(request["config"])
        path = self.output / "runs" / config.model / f"{request['fingerprint']}-{year}.json.gz"
        log = self.output / "logs" / f"{request['fingerprint']}-{year}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as output:
            faulthandler.enable(file=output)
            result = run_backtest(self.store, JobRequest(year=year, config=config), histories={s:f.loc[:f"{year}-12-31"] for s,f in self.histories.items()})
            summarize(result)
            write_gzip(path, result)
            faulthandler.disable()

    def report(self):
        lines = ["# 弱项参数复核与优化", "", "原启用参数已另存为恢复默认基线，优化不覆盖此基线。", "",
                 "2021–2024 选参数；每轮选完后对优胜者做2025验收，不参与候选排名；另做双倍费用、随机种子43的2022/2024压力复核。所有年份都曾被研究，多轮复核仍有适应验收集的风险，不是全新样本外证据。", "",
                 "每种模型选择非激进档中原评分最低的一档，并额外检查低参与度的CNN保守。其余档位不因收益较低而一律改成激进。追加一轮预测周期、训练窗口、样本场景与分类目标的单因素复核；验收标准不变。CNN短窗口再检查真正的双向窗口变化。最后单独对照允许加仓开关，金额、份数、仓位和止损止盈上限不变。", "",
                 "| 模型 | 档位 | 旧评分 | 新评分 | 2021–24均值：旧→新 | 2025：旧→新 | 是否采用 |", "|---|---|---:|---:|---|---|---|"]
        for model, profile in self.state.get("targets", []):
            record = self.state.get("models", {}).get(model, {}).get(profile)
            if not record or not record.get("checked"):
                continue
            old, new = (self.state["evaluations"][record[k]] for k in ("baseline", "center"))
            a, b = (self.state["evaluations"][record[k]] for k in ("check_baseline", "check_center"))
            lines.append(f"| {MODEL_NAMES[model]} | {PROFILES[profile]} | {old['score']:.4f} | {new['score']:.4f} | {old['mean_return']:.2%} → {new['mean_return']:.2%} | {a['mean_return']:.2%} → {b['mean_return']:.2%} | {'采用' if record['adopted'] else '保留原参数'} |")
        lines += ["", "## 诊断与参数变化", ""]
        for model, profile in self.state.get("targets", []):
            r = self.state.get("models", {}).get(model, {}).get(profile)
            if not r or not r.get("checked"):
                continue
            old, new = (self.state["evaluations"][r[k]] for k in ("baseline", "center"))
            exposure = np.mean([v["mean_exposure"] for v in old["per_year"]])
            trades = np.mean([v["trade_count"] for v in old["per_year"]])
            issue = "资金参与度低；低收益与长期低仓位同时出现，不能只据此判定模型无效。" if exposure < .05 else "成交较密集，需结合持有时间、信号噪声与费用检查，交易次数本身不证明因果。" if trades > 150 else "跨年份优势不稳定，局部改进必须经过后段与压力检查。"
            lines.append(f"- {MODEL_NAMES[model]} / {PROFILES[profile]}：调参四年平均仓位 {exposure:.2%}，平均每年 {trades:.1f} 笔。{issue}")
            if r["adopted"]:
                old_drawdown = min(v["max_drawdown"] for v in old["per_year"])
                new_drawdown = min(v["max_drawdown"] for v in new["per_year"])
                lines.append(f"  - 调参年份最大回撤：{old_drawdown:.2%} → {new_drawdown:.2%}；收益与回撤需要一起比较。")
                a, b = (v["config"]["models"][model][profile] for v in (old, new))
                for key in a:
                    if key == "params":
                        for field in a[key]:
                            if a[key][field] != b[key][field]:
                                lines.append(f"  - `{field}`：{a[key][field]} → {b[key][field]}")
                    elif a[key] != b[key]:
                        lines.append(f"  - `{key}`：{a[key]} → {b[key]}")
            else:
                names = {"search_improved":"跨年评分改善幅度不足", "worst_year_retained":"最差年份退步过大", "later_score_retained":"2025收益与风险评分退步过大", "later_drawdown_retained":"2025回撤增加过大", "stress_score_retained":"联合压力评分退步过大", "stress_drawdown_retained":"联合压力回撤增加过大", "defensive_drawdown_retained":"保守档回撤增加过大"}
                lines.append("  - 未通过：" + "、".join(names[k] for k, ok in r["gates"].items() if not ok) + "；未覆盖当前设置。")
        lines += ["", f"累计 {len(self.state.get('evaluations', {}))} 次候选评价，{sum(len(v['per_year']) for v in self.state.get('evaluations', {}).values())} 次年度模拟。", "",
                  "完整默认参数 saved-defaults.json；原数据库 before.sqlite3；二十档旧证据与筛选依据、候选参数、逐年参与度、概率分布及验收门槛见 state.json。冻结行情和来源见 manifest.json/frozen。", "",
                  "门槛：搜索评分至少提高0.003，最差年度不能倒退超过1个百分点；2025评分最多倒退0.015、回撤最多增加2个百分点；压力评分最多倒退0.005、回撤最多增加2个百分点；保守档搜索最差回撤最多增加0.5个百分点。", "",
                  "这些是候选验收标准，不是对可调参数添加硬边界。不保证未来收益。", "",
                  "资金口径：单笔1万元与2500份同时限制成交。按模拟器100份一手的规则，1元标的单笔约2500元；不开重复加仓时不会自动补满1万元。高于100元的标的在1万元预算内买不起100份。此轮不放宽账户风控。", "",
                  "训练目标：当前二分类标签是未来收盘涨幅超过0.1%，并非扣除费用后、从下一交易日开盘成交起算的净利润。五分类也使用固定涨幅分界。机会分与真实净收益不是同一个量；只调阈值存在局限，本轮未修改标签或特征口径。", "",
                  "运行记录：扩展MLP训练窗口期间出现过Tensor TypeError及Windows 0xC0000005退出。研究工具随后改为神经网络逐年独立进程并复用完整结果；这不等于证明原生库崩溃根因已经修复，也不改变网页自动学习的进程实现。"]
        self.output.mkdir(parents=True, exist_ok=True)
        (self.output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    def verify(self):
        annuals = 0
        for item in self.state["evaluations"].values():
            config = AppConfig.model_validate(item["config"])
            rows = []
            for row in item["per_year"]:
                path = self.output / "runs" / config.model / f"{item['fingerprint']}-{row['year']}.json.gz"
                with gzip.open(path, "rt", encoding="utf-8") as source:
                    full = json.load(source)
                actual = summarize(full)
                assert actual == row, f"Summary mismatch: {path}"
                assert full["seed"] == config.seed
                assert len(full["symbol_curves"]) == len(config.symbols)
                assert row["data_hash"] == self.state["year_hashes"][str(row["year"])]
                assert f"{row['year']}-01-01" <= row["start"] <= row["end"] <= f"{row['year']}-12-31"
                rows.append(actual)
                annuals += 1
            assert np.isclose(objective(rows, config.profile), item["score"])
        for model, profile in self.state["targets"]:
            r = self.state["models"][model][profile]
            assert r["checked"] and r.get("expanded") and r.get("participation"), "All planned checks must finish"
            assert r["adopted"] == all(r["gates"].values())
            assert self.state["evaluations"][r["center"]]["score"] == max(self.state["evaluations"][fp]["score"] for fp in r["candidates"])
        write_json(self.output / "verification.json", dict(status="passed", candidates=len(self.state["evaluations"]), annuals=annuals, manifest_hash=self.state["manifest"]["hash"]))

    def publish(self):
        if self.state.get("published_at"):
            print("Research already published; no settings changed.")
            return
        self.verify()
        current = requests.get(API + "/settings", timeout=20).json()
        if current != self.state["before"]:
            raise RuntimeError("Settings changed; do not overwrite user edits")
        live = Store(Path("data/v3"))
        if live.defaults() != self.state["defaults"]:
            raise RuntimeError("Saved defaults changed")
        config = AppConfig.model_validate(current["config"])
        changes = []
        for model, profile in self.state["targets"]:
            record = self.state["models"][model][profile]
            if not record.get("checked"):
                raise RuntimeError("Incomplete research")
            chosen = "center" if record["adopted"] else "baseline"
            result = self.state["evaluations"][record[chosen]]
            if record["adopted"]:
                config.models[model][profile] = Preset.model_validate(result["config"]["models"][model][profile])
                changes.append([model, profile])
            check = self.state["evaluations"][record["check_" + chosen]]
            request = JobRequest(kind="learn", profiles=[profile], years=[2021,2022,2023,2024], check_years=[2025], config=AppConfig.model_validate(result["config"]))
            job_id = hashlib.sha256(f"{self.output.resolve()}:{model}:{profile}".encode()).hexdigest()[:32]
            fingerprints = list(dict.fromkeys(record["candidates"]))
            message = "候选已通过复核" if record["adopted"] else "候选未通过稳健性验收，保留原参数"
            summary = {"best": {profile: {**result, "check_years": check["per_year"]}}, "total":len(fingerprints), "rounds":3, "protocol":self.state["protocol"], "acceptance":record["gates"]}
            with live.connect() as connection:
                connection.execute("INSERT INTO jobs(id,kind,status,progress,message,request,created,updated,result) VALUES(?,'learn','done',100,?,?,?,?,?) ON CONFLICT(id) DO NOTHING", (job_id, message, dumps(request.model_dump()), time.time(), time.time(), dumps(summary)))
            for fp in fingerprints:
                live.save_trial(job_id, fp, self.state["evaluations"][fp])
            record["job_id"] = job_id
        if live.meta("account") != self.state["account_before"]:
            raise RuntimeError("Account changed; investigate before applying")
        if changes:
            response = requests.put(API + "/settings", json={"config":config.model_dump(), "revision":current["revision"]}, timeout=20)
            response.raise_for_status()
            self.state["applied_revision"] = response.json()["revision"]
        self.state["changes"] = changes
        self.state["published_at"] = time.time()
        self.save()
        self.report()
        print(json.dumps({"changes":changes,"defaults_revision":self.state["defaults"]["source_revision"]}), flush=True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["capture", "search", "expand", "participation", "pair", "annual", "publish"], default="capture")
    parser.add_argument("--expand", action="store_true")
    parser.add_argument("--participation", action="store_true")
    parser.add_argument("--output", default=str(OUTPUT))
    parser.add_argument("--model")
    parser.add_argument("--profile")
    parser.add_argument("--request")
    parser.add_argument("--year", type=int)
    args = parser.parse_args()
    runner = Focused(args.output)
    runner.prepare()
    if args.phase == "annual":
        runner.annual(args.request, args.year)
    elif args.phase == "pair":
        runner.search_pair(args.model, args.profile, args.expand, args.participation)
    elif args.phase in {"search", "expand", "participation"}:
        for model, profile in runner.state["targets"]:
            for attempt in range(2):
                command = [sys.executable, __file__, "--phase", "pair", "--output", str(runner.output), "--model", model, "--profile", profile]
                if args.phase in {"expand", "participation"}:
                    command.append("--expand")
                if args.phase == "participation":
                    command.append("--participation")
                result = subprocess.run(command)
                if result.returncode == 0:
                    break
                print(f"Pair process failed: {model}/{profile} exit={result.returncode}, attempt={attempt+1}", flush=True)
                if attempt:
                    raise SystemExit(result.returncode)
    elif args.phase == "publish":
        runner.publish()


if __name__ == "__main__":
    main()
