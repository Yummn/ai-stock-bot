<script setup lang="ts">
import { computed } from "vue";
import {
  Activity,
  SlidersHorizontal,
  ChartNoAxesCombined,
  RefreshCw,
  Save,
  FlaskConical,
  ArrowUpRight,
  Sparkles,
  ShieldCheck,
  CircleAlert,
  Check,
  Square,
  Play,
  Download,
  X,
  LockKeyhole,
} from "@lucide/vue";
import { money, pct } from "../api";
import PortfolioChart from "../components/PortfolioChart.vue";
import { useInvestor } from "./workspace";
const {
  tabs,
  page,
  state,
  draft,
  report,
  auditData,
  saved,
  error,
  toast,
  busy,
  connected,
  loading,
  draftRevision,
  asset,
  interval,
  chartData,
  chartBusy,
  startYear,
  endYear,
  remote,
  writable,
  showLogin,
  loginKey,
  showReset,
  jobNames,
  methodNames,
  dirty,
  activeJobs,
  resultRows,
  comparisonSeries,
  years,
  yearly,
  name,
  positionRows,
  jobsByKind,
  gainClass,
  totalTarget,
  targetRows,
  summarySeries,
  notify,
  navigate,
  hashChanged,
  restoreDraft,
  refresh,
  loop,
  save,
  run,
  jobAction,
  loadChart,
  inspectData,
  login,
  reset,
  exportReport,
  ratio,
} = useInvestor();
const latestUpdateJob = computed<any>(() => state.value?.jobs?.find(
  (job: any) => job.kind === "portfolio_update"));
</script>
<template>
  <section>
    <div class="page-title">
      <div>
        <p class="eyebrow">模拟账户 · 不涉及真实资金</p>
        <h1>账户总览</h1>
        <p class="subtitle">
          {{ state.date ? `已处理至 ${state.date}` : "尚未开始前向模拟"
          }}<span v-if="state.started_at">
            · 起始 {{ state.started_at.slice(0, 10) }}</span
          >
          · {{ state.execution.interval !== 'daily' ? state.execution.interval.replace('m', ' 分钟') : '日线' }} / {{ state.execution.multiple }}x
        </p>
      </div>
      <button
        class="primary"
        :disabled="busy || jobsByKind('portfolio_update')"
        @click="run('portfolio_update')"
      >
        <RefreshCw :size="16" />{{ state.date ? "更新模拟" : "开始模拟观察" }}
      </button>
    </div>
    <div v-if="state.plan_changed" class="notice warning">
      投资计划已修改；账户仍是上次计划的结果，更新后新计划才会生效。
    </div>
    <div v-if="latestUpdateJob && latestUpdateJob.created >= (state.settings.saved_at || 0) && ['failed', 'stopped', 'interrupted'].includes(latestUpdateJob.status)" class="notice error" role="alert">
      上次模拟更新未完成：{{ latestUpdateJob.error || latestUpdateJob.message || '请重新运行' }}
    </div>
    <div v-if="state.quote_age_days >= 3" class="notice warning">
      行情日期距今
      {{
        state.quote_age_days
      }}
      天，并非实时价格。尚未成交的计划不会补成交到保存之前的日期。
    </div>
    <div class="metrics">
      <div>
        <span>模拟总资产</span
        ><strong>{{ money(state.account.equity) }}<small>元</small></strong>
      </div>
      <div>
        <span>累计盈亏</span
        ><strong
          :class="
            gainClass(state.account.equity - state.settings.plan.starting_cash)
          "
          >{{ money(state.account.equity - state.settings.plan.starting_cash)
          }}<small>元</small></strong
        >
      </div>
      <div>
        <span>{{ state.account.cash < 0 ? '模拟借款余额' : '可用现金' }}</span
        ><strong>{{ money(Math.abs(state.account.cash)) }}<small>元</small></strong>
      </div>
      <div>
        <span>当前仓位</span
        ><strong>{{ state.account.equity > 0 ? pct((state.account.gross_exposure ?? (state.account.equity - state.account.cash - (state.account.receivables || 0))) / state.account.equity) : '—' }}</strong>
      </div>
    </div>
    <div class="overview-grid">
      <section class="section">
        <div class="section-heading">
          <h2>账户走势</h2>
          <span>人民币</span>
        </div>
        <PortfolioChart v-if="state.curve.length > 1" :series="summarySeries" />
        <div v-else class="empty chart-empty">
          <Activity :size="29" /><strong>{{
            state.date ? "已建立起点" : "还没有模拟记录"
          }}</strong
          ><span>资产曲线从保存计划后的实际交易日开始。</span>
        </div>
      </section>
      <section class="section allocation">
        <div class="section-heading">
          <h2>下一次目标分配</h2>
          <span>{{ state.decision?.date || "未生成" }}</span>
        </div>
        <div v-if="state.decision && !state.plan_changed" class="allocation-content">
          <p class="allocation-reason">
            {{ state.decision.reason
            }}<span v-if="state.cooldown">
              · 冷静期余 {{ state.cooldown }} 个交易日</span
            >
          </p>
          <div
            class="weight-row"
            v-for="row in targetRows"
            :key="row.symbol"
          >
            <div>
              <strong>{{ row.name }}</strong
              ><span>{{ row.group }}</span>
            </div>
            <b>{{ pct(row.weight) }}</b
            ><meter min="0" :max="state.execution.multiple" :value="row.weight" />
          </div>
          <div v-if="totalTarget <= 1" class="weight-row cash">
            <div><strong>现金</strong><span>未投入部分</span></div>
            <b>{{ pct(1 - totalTarget) }}</b
            ><meter min="0" max="1" :value="1 - totalTarget" />
          </div>
          <div v-else class="weight-row cash"><div><strong>模拟借款</strong><span>需计入利息和维保风险</span></div><b>{{ pct(totalTarget - 1) }}</b></div>
          <small v-if="state.decision.model">{{
            state.decision.model.reason
          }}</small>
        </div>
        <div v-else class="empty">{{ state.plan_changed ? '新计划待更新' : '等待生成投资计划' }}</div>
      </section>
    </div>
    <section class="section">
      <div class="section-heading">
        <h2>当前持仓</h2>
        <span>{{ positionRows.length }} 只 ETF</span>
      </div>
      <div v-if="!positionRows.length" class="empty compact">
        目前全部为现金
      </div>
      <div v-else class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>标的</th>
              <th>份额</th>
              <th>成本 / 最新价</th>
              <th>市值</th>
              <th>浮动盈亏</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in positionRows" :key="p.symbol">
              <td>
                <strong>{{ name(p.symbol) }}</strong
                ><small>{{ p.symbol }}</small>
              </td>
              <td>{{ p.quantity }}</td>
              <td>{{ money(p.average_price) }} / {{ money(p.price) }}</td>
              <td>{{ money(p.quantity * p.price) }}</td>
              <td :class="gainClass(p.price - p.average_price)">
                {{ pct(p.price / p.average_price - 1) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <div class="overview-grid">
      <section class="section">
        <div class="section-heading">
          <h2>行情</h2>
          <div class="inline-fields">
            <select aria-label="行情标的" v-model="asset">
              <option v-for="(v, s) in state.catalog" :value="s" :key="s">
                {{ (v as any).name }}
              </option></select
            ><select aria-label="图表粒度" v-model="interval">
              <option value="daily">日线</option>
              <option v-if="state.execution.interval !== '30m'" value="60m">60 分钟</option>
              <option v-if="state.execution.interval === '30m'" value="30m">30 分钟</option>
            </select>
          </div>
        </div>
        <PortfolioChart
          v-if="!chartBusy && chartData?.points.length"
          :series="[
            {
              name: chartData.name,
              color: '#4267c6',
              points: chartData.points.map((p: any) => ({
                date: p.date,
                value: p.close,
              })),
            },
          ]"
        />
        <div v-else class="empty chart-empty">
          {{ chartBusy ? "正在读取…" : "暂无该粒度行情" }}
        </div>
        <div class="section-foot">
          <small
            >{{ chartData?.points[0]?.date || "暂无" }} 至
            {{ chartData?.points.at(-1)?.date || "暂无" }} ·
            {{ interval !== "daily" ? "仅供盘中观察" : "未复权价格" }}</small
          ><button
            v-if="interval !== 'daily'"
            :disabled="busy || jobsByKind('intraday_refresh')"
            @click="run('intraday_refresh')"
          >
            <RefreshCw :size="14" />更新盘中行情
          </button>
        </div>
      </section>
      <section class="section">
        <div class="section-heading">
          <h2>AI 解读</h2>
          <button
            :disabled="busy || !state.date || state.execution.interval !== 'daily' || jobsByKind('portfolio_review')"
            @click="run('portfolio_review')"
          >
            <Sparkles :size="15" />分析当前计划
          </button>
        </div>
        <template v-if="state.execution.interval !== 'daily'"><p class="small-note">盘中账户的 AI 解读尚未适配，请先查看上方持仓与盘中验证。</p></template>
        <template v-else-if="state.ai_review"
          ><p class="review-summary">{{ state.ai_review.summary }}</p>
          <div class="review-block">
            <h3>需要注意</h3>
            <ul>
              <li v-for="r in state.ai_review.risks" :key="r">{{ r }}</li>
            </ul>
          </div>
          <details>
            <summary>观察依据与后续关注</summary>
            <ul>
              <li
                v-for="r in [
                  ...state.ai_review.observations,
                  ...state.ai_review.watch_next,
                ]"
                :key="r"
              >
                {{ r }}
              </li>
            </ul>
          </details>
          <small
            >依据 {{ state.ai_review.as_of }} 数据 ·
            {{ state.ai_review.provider }} · {{ state.ai_review.model==='provider_default' ? '账户默认模型（实际型号未回传）' : (state.ai_review.model_scope==='requested'?'请求型号：':'')+state.ai_review.model }}</small
          ></template
        >
        <div v-else class="empty chart-empty">
          <Sparkles :size="27" /><span>暂无当前计划的 AI 分析</span>
        </div>
      </section>
    </div>
    <details class="section">
      <summary>计划依据与最近成交</summary>
      <div v-if="state.decision" class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>标的</th>
              <th>近 60 日涨跌</th>
              <th>选择依据</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in state.decision.rows" :key="row.symbol">
              <td>{{ row.name }}</td>
              <td>{{ pct(row.momentum) }}</td>
              <td>{{ row.reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>成交日期</th>
              <th>标的</th>
              <th>方向</th>
              <th>份额</th>
              <th>成交价</th>
              <th>佣金</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(t, i) in [...state.account.trades].reverse()" :key="i">
              <td>{{ t.date }}</td>
              <td>{{ name(t.symbol) }}</td>
              <td>{{ t.side === "buy" ? "买入" : "卖出" }}</td>
              <td>{{ t.quantity }}</td>
              <td>{{ money(t.price) }}</td>
              <td>{{ money(t.fee) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!state.account.trades.length" class="empty compact">
          暂无成交
        </p>
      </div>
      <div v-if="state.rejections?.length" class="table-scroll">
        <h3>未成交记录</h3>
        <table><thead><tr><th>日期</th><th>标的</th><th>原因</th></tr></thead><tbody><tr v-for="(row,i) in state.rejections" :key="i"><td>{{row.date}}</td><td>{{name(row.symbol)}}</td><td>{{row.reason}}</td></tr></tbody></table>
      </div>
    </details>
  </section>
</template>
