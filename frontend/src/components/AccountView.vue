<script setup lang="ts">
import { computed, defineAsyncComponent, inject, ref } from "vue";
import {
  Play,
  RefreshCw,
  Settings2,
  Plus,
  X,
  RotateCcw,
  ArrowUpRight,
  Clock3,
} from "@lucide/vue";
import { modelName, taskName } from "../workspace";
import { api, money, pct, statusName } from "../api";
const Plot = defineAsyncComponent(() => import("./Plot.vue"));
const MLReferencePanel = defineAsyncComponent(
  () => import("./MLReferencePanel.vue"),
);
const MarketResearch = defineAsyncComponent(
  () => import("./MarketResearch.vue"),
);
const w = inject<any>("workspace"),
  chosen = ref(""),
  manual = ref(false),
  side = ref("buy"),
  quantity = ref(100),
  symbol = ref("510300.SH");
const quote = computed(
  () => w.market.find((q: any) => q.symbol === chosen.value) || w.market[0],
);
const invested = computed(
  () => w.overview.account.equity - w.overview.account.cash,
);
const pending = computed(() =>
  w.jobs.filter((j: any) => ["queued", "running"].includes(j.status)),
);
const positions = computed(
  () => Object.entries(w.overview.account.positions) as [string, any][],
);
async function trade() {
  await w.act(async () => {
    await api("/account/trade", {
      method: "POST",
      body: JSON.stringify({
        symbol: symbol.value,
        side: side.value,
        quantity: quantity.value,
      }),
    });
    manual.value = false;
    await w.refreshOverview();
    w.flash("模拟交易已记录");
  });
}
async function reset() {
  if (confirm("重置模拟账户的现金与持仓？实验与方案保留。"))
    await w.act(async () => {
      await api("/account/reset", { method: "POST" });
      await w.refreshOverview();
    });
}
</script>
<template>
  <div class="page-title">
    <div>
      <span class="overline">PAPER ACCOUNT</span>
      <h1>模拟账户</h1>
      <p>
        {{ modelName(w.active) }} ·
        {{
          w.active.auto_profile
            ? w.active.profile_selection === "recent" ? "按近三个月模拟成绩选档" : "按价格走势选档"
            : w.catalog.profiles[w.active.profile]
        }}
      </p>
    </div>
    <button
      class="primary"
      :disabled="
        w.busy ||
        pending.some((j: any) => j.kind === 'paper') ||
        !w.access.write_allowed
      "
      @click="w.submit('paper')"
    >
      <Play :size="16" />运行模拟
    </button>
  </div>
  <div class="account-metrics">
    <div class="metric primary-metric">
      <span>当前总资产</span
      ><strong>¥{{ money(w.overview.account.equity) }}</strong
      ><small>模拟资金</small>
    </div>
    <div class="metric">
      <span>持仓市值</span><strong>¥{{ money(invested) }}</strong
      ><small>资产的 {{ pct(invested / w.overview.account.equity) }}</small>
    </div>
    <div class="metric">
      <span>可用现金</span><strong>¥{{ money(w.overview.account.cash) }}</strong
      ><small>{{ positions.length }} 个持仓</small>
    </div>
  </div>
  <div class="account-status">
    <span
      ><Clock3 :size="14" />最近信号
      {{ w.overview.account.last_signal_date || "尚未运行" }}</span
    ><button class="link-button" @click="w.navigate('strategy')">
      当前方案<ArrowUpRight :size="14" /></button
    ><button class="link-button" @click="w.editor = 'account'">
      <Settings2 :size="14" />资金与限制
    </button>
  </div>
  <div v-if="pending.length" class="task-inline">
    <div v-for="job in pending" :key="job.id">
      <span class="running-dot"></span><strong>{{ taskName(job.kind) }}</strong
      ><span>{{ job.message }}</span
      ><button
        v-if="['learn', 'evaluate', 'backtest'].includes(job.kind)"
        class="link-button"
        @click="w.selectJob(job.id)"
      >
        查看进度
      </button>
    </div>
  </div>
  <section class="holdings-section">
    <div class="section-title">
      <h2>
        当前持仓 <span class="count">{{ positions.length }}</span>
      </h2>
      <div class="inline-actions">
        <button
          class="subtle"
          :disabled="!w.access.write_allowed"
          @click="
            manual = true;
            symbol = w.active.symbols[0];
          "
        >
          <Plus :size="15" />手动模拟</button
        ><button
          class="icon-btn"
          title="重置模拟账户"
          :disabled="!w.access.write_allowed"
          @click="reset"
        >
          <RotateCcw :size="15" />
        </button>
      </div>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>标的</th>
            <th>持有份数</th>
            <th>市值</th>
            <th>持仓盈亏</th>
            <th>可卖份数</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="[code, p] in positions">
            <td>
              <strong>{{ w.catalog.symbols[code] }}</strong
              ><small class="block">{{ code }}</small>
            </td>
            <td>{{ p.quantity }}</td>
            <td>¥{{ money(p.quantity * p.price) }}</td>
            <td :class="p.price >= p.average_price ? 'positive' : 'negative'">
              {{ pct(p.price / p.average_price - 1) }}
            </td>
            <td>{{ p.available }}</td>
          </tr>
          <tr v-if="!positions.length">
            <td colspan="5" class="muted">当前没有持仓</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
  <section class="market-workspace">
    <div class="section-title">
      <div>
        <h2>关注行情</h2>
        <p>日线数据 · {{ w.active.symbols.length }} 个交易标的</p>
      </div>
      <button
        class="icon-btn"
        title="更新行情"
        :disabled="w.busy || !w.access.write_allowed"
        @click="w.submit('refresh')"
      >
        <RefreshCw :size="17" />
      </button>
    </div>
    <div v-if="quote" class="market-layout">
      <div class="instrument-list">
        <button
          v-for="q in w.market"
          :class="{ selected: q.symbol === quote.symbol }"
          @click="chosen = q.symbol"
        >
          <span
            ><strong>{{ q.name }}</strong
            ><small>{{ q.symbol }}</small></span
          ><span
            ><strong>{{ q.price.toFixed(3) }}</strong
            ><small :class="q.change >= 0 ? 'positive' : 'negative'">{{
              pct(q.change)
            }}</small></span
          >
        </button>
      </div>
      <div class="instrument-chart">
        <div class="section-title">
          <div>
            <h3>{{ quote.name }}</h3>
            <p>{{ quote.points[0]?.date }} 至 {{ quote.date }}</p>
          </div>
          <strong :class="quote.change >= 0 ? 'positive' : 'negative'"
            >{{ pct(quote.change)
            }}<small class="block">图示区间</small></strong
          >
        </div>
        <Plot :key="quote.symbol" :points="quote.points" />
      </div>
    </div>
    <div v-else class="empty-small">正在读取行情；无数据时可更新历史行情。</div>
  </section>
  <MarketResearch v-if="w.capabilities.includes('market_research')" />
  <details v-if="w.overview.decisions.length" class="fold">
    <summary>
      最近决策与成交原因 <span>{{ w.overview.decisions.length }} 个标的</span>
    </summary>
    <div class="decision-list">
      <div v-for="d in w.overview.decisions">
        <strong>{{ w.catalog.symbols[d.symbol] }}</strong
        ><span :class="['trade-side', d.side]">{{
          { buy: "买入", sell: "卖出", hold: "观望" }[d.side as "buy"]
        }}</span>
        <p>{{ d.reason }}</p>
        <p v-if="d.profile_selection_reason">本次使用{{ w.catalog.profiles[d.profile] }}档</p>
        <p v-if="d.ml_reference?.references">
          AI 与 {{ d.reference_agreement?.matching ?? 0 }}/{{
            d.reference_agreement?.available ?? 0
          }}
          组有效参考一致；
          {{ d.ml_reference.summary?.unavailable || 0 }} 组不可用。
        </p>
        <MLReferencePanel
          v-if="d.ml_reference"
          :evidence="d.ml_reference"
          :catalog="w.catalog"
        />
        <p v-if="d.source" :class="{ notice: d.fallback }">
          {{
            d.source === "safe_hold"
              ? "安全观望"
              : `决策来源：${w.catalog.models?.[d.source] || ({ local: "本地规则", codex: "Codex", openai: "OpenAI" } as any)[d.source] || d.source}`
          }}
          <span v-if="d.confidence != null">
            · 动作置信度 {{ (d.confidence * 100).toFixed(1) }}%（非胜率）</span
          >
          <span v-if="d.opportunity_score != null">
            · 机会分
            {{ (d.opportunity_score * 100).toFixed(1) }}%（非胜率）</span
          >
        </p>
        <details v-if="d.audit">
          <summary>决策依据</summary>
          <p>
            截至 {{ d.audit.as_of }} · {{ d.audit.provider }} /
            {{ d.audit.model }}
          </p>
          <p>输入指纹：{{ d.audit.prompt_hash.slice(0, 16) }}</p>
          <p>可用资金：{{ d.audit.input.account?.cash?.toFixed(2) }} 元</p>
        </details>
        <small>{{ d.execution }}</small>
      </div>
    </div>
  </details>
  <div v-if="manual" class="modal-backdrop" @click.self="manual = false">
    <section
      class="modal"
      role="dialog"
      aria-modal="true"
      aria-label="手动模拟"
      v-modal="() => (manual = false)"
    >
      <div class="section-title">
        <h2>手动模拟</h2>
        <button class="icon-btn" title="关闭" @click="manual = false">
          <X :size="18" />
        </button>
      </div>
      <div class="field-grid">
        <label class="field"
          ><span>标的</span
          ><select v-model="symbol">
            <option v-for="s in w.active.symbols" :value="s">
              {{ w.catalog.symbols[s] }}
            </option>
          </select></label
        ><label class="field"
          ><span>方向</span
          ><select v-model="side">
            <option value="buy">买入</option>
            <option value="sell">卖出</option>
          </select></label
        ><label class="field"
          ><span>数量 / 份</span
          ><input type="number" v-model.number="quantity" min="100" step="100"
        /></label>
      </div>
      <p class="muted">按最近缓存收盘价估算成交。</p>
      <button class="primary full" :disabled="w.busy" @click="trade">
        确认模拟{{ side === "buy" ? "买入" : "卖出" }}
      </button>
    </section>
  </div>
</template>
