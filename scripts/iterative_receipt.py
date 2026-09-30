"""Project the reviewed experiment evidence without private filesystem paths."""
import json
from pathlib import Path

from ai_stock_bot.v2.schema import MODEL_NAMES, PROFILES
from iterate_stocklab import OUTPUT, write_json


def main():
    state = json.loads((OUTPUT / "state.json").read_text(encoding="utf-8"))
    verification = json.loads((OUTPUT / "verification.json").read_text(encoding="utf-8"))
    rows = []
    for model in MODEL_NAMES:
        for profile in PROFILES:
            record = state["models"][model][profile]
            old = state["evaluations"][record["baseline"]]
            new = state["evaluations"][record["center"]]
            checks = {r["origin"]:state["evaluations"][r["fingerprint"]] for r in record["check"]}
            selected = new if record["adopted"] else old
            selected_check = checks["center" if record["adopted"] else "baseline"]
            rows.append(dict(model=MODEL_NAMES[model], profile=PROFILES[profile], adopted=record["adopted"], before_search_mean=old["mean_return"], deployed_search_mean=selected["mean_return"], before_2025=checks["baseline"]["mean_return"], deployed_2025=selected_check["mean_return"], selected_label=selected["label"]))
    caveats = ["2021–2024 用于选择参数，2025 用于发布验收。所有这些年份曾在旧项目使用过，不是从未看过的留出集。", "单种子、多次搜索仍可能过拟合；当前 ETF 池存在幸存者偏差；未模拟分红再投资。", "历史模拟不能保证未来获利，也不能保证每个档位在每一年都有正收益。", "研究中出现过计算进程中断，成功的重试结果接受完整校验；不代表底层原生库或驱动问题已彻底解决。"]
    receipt = {"schemaVersion":1,"items":[{"id":"all-models-presets","title":"五种模型、四档是否全部完成调优？","queries":[{"id":"research-verification","source":{"label":"冻结行情实验与独立验证","files":[{"label":"verification.json"},{"label":"manifest.json"}],"metricDefinitions":[{"label":"完成年度模拟","definition":"每组候选在一个完整自然年的独立模拟账户运行；失败尝试不计入已完成数量。"}],"filters":["五个模型","激进、均衡、保守、自定义四档","十个固定交易 ETF","次日开盘成交，计手续费和滑点"],"caveats":caveats,"evidenceFlow":[{"kind":"validation","title":"独立验证","detail":"逐年重新计算收益、最大回撤、手续费，按交易重建现金和持仓市值；检查全部行情指纹、日期、训练截止和未来价格干预。"}]},"reportingPeriod":"2021–2025 自然年","columns":["model_presets","candidates","annual_simulations","adopted_updates"],"rows":[{"model_presets":20,"candidates":verification["candidates"],"annual_simulations":verification["annuals"],"adopted_updates":len(state["changes"])}]}]},{"id":"deployed-parameters","title":"各模型档位采用了什么结果？","queries":[{"id":"deployed-comparisons","source":{"label":"发布参数与同数据参照","files":[{"label":"state.json"},{"label":"ready.json"}],"metricDefinitions":[{"label":"学习期平均收益","definition":"2021–2024 四次独立自然年收益率的算术平均，不是复利累计收益。"},{"label":"2025 收益","definition":"2025 全年在相同交易池、起始资产、费用与风控下的模拟收益率。"}],"caveats":caveats},"reportingPeriod":"学习 2021–2024；后段检查 2025","columns":[{"field":"model","label":"模型"},{"field":"profile","label":"档位"},{"field":"adopted","label":"采用新参数"},{"field":"before_search_mean","label":"旧学习期平均收益"},{"field":"deployed_search_mean","label":"发布学习期平均收益"},{"field":"before_2025","label":"旧2025收益"},{"field":"deployed_2025","label":"发布2025收益"},{"field":"selected_label","label":"发布候选"}],"rows":rows}]}]}
    write_json(OUTPUT / "sources.json", receipt)
    print(str(Path(OUTPUT / "sources.json").resolve()))


if __name__ == "__main__":
    main()
