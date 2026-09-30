<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from "vue";
import {
  Check,
  ArrowUpRight,
  ArrowDownRight,
  FlaskConical,
  ChevronRight,
  Download,
} from "@lucide/vue";
import { pct, money, sceneName } from "../api";
const Plot = defineAsyncComponent(() => import("./Plot.vue"));
const MLReferencePanel = defineAsyncComponent(
  () => import("./MLReferencePanel.vue"),
);
const props = defineProps<{ job: any; catalog: any; expert: boolean }>();
const result = computed(() => props.job.result);
const symbol = ref("");
const page = ref(0);
const decisionPage = ref(0);
const aiDecisions = computed(() =>
  [...(result.value?.decisions || [])]
    .filter(
      (d: any) =>
        d.ml_reference && (!symbol.value || d.symbol === symbol.value),
    )
    .reverse(),
);
const actionName = (side: string) =>
  (({ buy: "买入", sell: "卖出", hold: "观望" }) as any)[side] || "不可用";
watch(symbol, () => {
  page.value = 0;
  decisionPage.value = 0;
});
const curve = computed(
  () =>
    result.value?.symbol_curves.find((c: any) => c.symbol === symbol.value) ||
    result.value?.symbol_curves[0],
);
const selectedSymbol = computed(() => curve.value?.symbol);
const filteredTrades = computed(() =>
  (result.value?.trades || []).filter(
    (t: any) => !symbol.value || t.symbol === symbol.value,
  ),
);
const tradePage = computed(() =>
  [...filteredTrades.value]
    .reverse()
    .slice(page.value * 20, page.value * 20 + 20),
);
function downloadResult() {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(props.job, null, 2)], {
      type: "application/json",
    }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = `StockLab-${props.job.id.slice(0, 8)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}
</script>
<template>
  <div v-if="result" class="result-panel">
    <div class="section-title">
      <div>
        <h2>测试结果 <span class="badge">已保存</span></h2>
        <p>
          {{ result.start }} 至 {{ result.end }} · {{ result.bars }} 个交易日 ·
          {{
            result.auto_profile
              ? result.profile_selection === "recent" ? "按近三个月模拟成绩选档" : "按价格走势选档"
              : catalog.profiles[result.profile]
          }}
          ·
          {{
            result.engine === "ml"
              ? catalog.models[result.model]
              : result.engine === "ai"
                ? "AI"
                : "本地规则"
          }}
        </p>
      </div>
      <button class="icon-btn" @click="downloadResult" title="导出完整结果">
        <Download :size="17" />
      </button>
    </div>
    <div class="metrics-grid">
      <div class="metric">
        <span>策略收益</span
        ><strong :class="result.return_pct >= 0 ? 'positive' : 'negative'">{{
          pct(result.return_pct)
        }}</strong
        ><small>结束资产 ¥{{ money(result.end_equity) }}</small>
      </div>
      <div class="metric">
        <span>买入持有</span><strong>{{ pct(result.benchmark_return) }}</strong
        ><small>等金额买入 · 相同成本</small>
      </div>
      <div class="metric">
        <span>超额收益</span
        ><strong :class="result.excess_return >= 0 ? 'positive' : 'negative'">{{
          pct(result.excess_return)
        }}</strong
        ><small>策略相对持有</small>
      </div>
      <div class="metric">
        <span>最大回撤</span><strong>{{ pct(result.max_drawdown) }}</strong
        ><small
          >{{ result.trade_count }} 笔交易 · 费用 ¥{{
            money(result.fees)
          }}</small
        >
      </div>
    </div>
    <section class="plot-section">
      <div class="section-title">
        <h3>资产曲线</h3>
        <span class="muted">起始 ¥{{ money(result.start_equity) }}</span>
      </div>
      <Plot :points="result.curve" equity />
    </section>
    <section class="plot-section">
      <div class="section-title">
        <h3>标的走势与成交</h3>
        <div class="inline-actions">
          <span class="marker-key"
            ><i class="buy-dot"></i>买入<i class="sell-dot"></i>卖出</span
          ><select v-model="symbol" aria-label="结果标的">
            <option value="">{{ result.symbol_curves[0]?.name }}</option>
            <option
              v-for="item in result.symbol_curves.slice(1)"
              :value="item.symbol"
            >
              {{ item.name }}
            </option>
          </select>
        </div>
      </div>
      <Plot
        v-if="curve"
        :points="curve.points"
        :trades="result.trades.filter((t: any) => t.symbol === selectedSymbol)"
      />
    </section>
    <div class="result-message">
      <Check :size="16" /><span
        >{{
          result.excess_return > 0
            ? "本次测试跑赢了买入持有。"
            : "本次测试未跑赢买入持有。"
        }}{{
          result.return_pct < 0
            ? "策略仍有绝对亏损，应检查回撤和其他年份。"
            : "继续比较不同年份与参数稳定性。"
        }}</span
      >
    </div>
    <details class="fold">
      <summary>
        <ChevronRight :size="16" />最终持仓<span
          >{{ Object.keys(result.positions).length }} 个标的</span
        >
      </summary>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>标的</th>
              <th>持有份数</th>
              <th>成本</th>
              <th>收盘价</th>
              <th>市值</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(position, symbol) in result.positions">
              <td>{{ catalog.symbols[symbol] }}</td>
              <td>{{ position.quantity }}</td>
              <td>{{ position.average_price.toFixed(3) }}</td>
              <td>{{ position.price.toFixed(3) }}</td>
              <td>¥{{ money(position.price * position.quantity) }}</td>
            </tr>
            <tr v-if="!Object.keys(result.positions).length">
              <td colspan="5" class="muted">期末空仓</td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
    <details class="fold" :open="expert">
      <summary>
        <ChevronRight :size="16" />完整交易记录<span
          >{{ result.trade_count }} 笔</span
        >
      </summary>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>日期</th>
              <th>标的</th>
              <th>方向</th>
              <th>数量</th>
              <th>成交价</th>
              <th>档位</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(trade, index) in tradePage" :key="index">
              <td>{{ trade.date }}</td>
              <td>{{ catalog.symbols[trade.symbol] }}</td>
              <td>
                <span :class="['trade-side', trade.side]">{{
                  trade.side === "buy" ? "买入" : "卖出"
                }}</span>
              </td>
              <td>{{ trade.quantity }}</td>
              <td>{{ trade.price.toFixed(3) }}</td>
              <td>{{ catalog.profiles[trade.profile] }}</td>
              <td class="reason-cell">{{ trade.reason }}</td>
            </tr>
            <tr v-if="!tradePage.length">
              <td colspan="7">该区间没有交易</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="pagination">
        <button class="subtle" :disabled="page === 0" @click="page--">
          上一页</button
        ><span
          >{{ page + 1 }} /
          {{ Math.max(1, Math.ceil(filteredTrades.length / 20)) }}</span
        ><button
          class="subtle"
          :disabled="(page + 1) * 20 >= filteredTrades.length"
          @click="page++"
        >
          下一页
        </button>
      </div>
    </details>
    <details v-if="aiDecisions.length" class="fold">
      <summary>
        机器学习与 AI 复核 <span>{{ aiDecisions.length }} 条</span>
      </summary>
      <div class="decision-list">
        <div
          v-for="d in aiDecisions.slice(
            decisionPage * 20,
            (decisionPage + 1) * 20,
          )"
        >
          <strong>{{ d.date }} · {{ catalog.symbols[d.symbol] }}</strong>
          <p>
            最终判断：{{ actionName(d.side) }} · 与
            {{ d.reference_agreement?.matching ?? 0 }}/{{
              d.reference_agreement?.available ?? 0
            }}
            组有效参考一致
          </p>
          <p>{{ d.reason }}</p>
          <MLReferencePanel :evidence="d.ml_reference" :catalog="catalog" />
        </div>
        <div class="pagination">
          <button
            class="subtle"
            :disabled="decisionPage === 0"
            @click="decisionPage--"
          >
            上一页</button
          ><span
            >{{ decisionPage + 1 }} /
            {{ Math.ceil(aiDecisions.length / 20) }}</span
          ><button
            class="subtle"
            :disabled="(decisionPage + 1) * 20 >= aiDecisions.length"
            @click="decisionPage++"
          >
            下一页
          </button>
        </div>
      </div>
    </details>
    <details class="fold">
      <summary>
        <FlaskConical :size="16" />训练与执行诊断<span
          >回退 {{ result.fallback_count }} 次</span
        >
      </summary>
      <div class="inner">
        <p class="muted">{{ result.execution }}</p>
        <p class="muted">
          数据版本 {{ result.data_hash }} · 随机种子 {{ result.seed }}
        </p>
        <div v-if="result.warnings.length" class="notice warning">
          <p v-for="warning in result.warnings">{{ warning }}</p>
        </div>
        <div
          v-for="(report, index) in result.training"
          class="training-report"
          :key="index"
        >
          <h4>
            {{ catalog.symbols[report.symbol] }} ·
            {{ catalog.profiles[report.profile]
            }}<span class="badge">{{
              report.cached ? "复用预测" : report.device || "本地回退"
            }}</span>
          </h4>
          <p>
            {{
              {
                self: "单标的",
                classified: "按行情分类",
                mixed: "全部混合",
                local_fallback: "本地回退",
              }[report.mode as "self"] || report.mode
            }}
            · {{ report.samples }} 条样本 · 截止 {{ report.cutoff || "—" }}
          </p>
          <p v-if="report.validation_loss != null">
            验证损失 {{ report.validation_loss.toFixed(4) }} ·
            {{ report.epochs }} 轮 · 验证 {{ report.validation_samples }} 条
          </p>
          <p v-if="report.label_basis">
            训练口径：{{
              report.label_basis === "next_open_net"
                ? "下一开盘成交净收益（扣双边成本）"
                : "收盘涨跌（旧版对照）"
            }}
          </p>
          <div class="table-scroll" v-if="report.sources">
            <table>
              <thead>
                <tr>
                  <th>训练标的</th>
                  <th>区间</th>
                  <th>样本</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="source in report.sources">
                  <td>{{ catalog.symbols[source.symbol] }}</td>
                  <td>{{ source.start }} 至 {{ source.end }}</td>
                  <td>{{ source.samples }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <details>
          <summary>本次配置快照</summary>
          <pre>{{ JSON.stringify(job.request.config, null, 2) }}</pre>
        </details>
      </div>
    </details>
  </div>
</template>
