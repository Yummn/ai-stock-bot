<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, ref, watch } from "vue";
import { ChartNoAxesCombined, CircleAlert, Download, Play, RefreshCw, ShieldCheck, FlaskConical } from "@lucide/vue";
import { api, dateTime, money, pct } from "../api";
import PortfolioChart from "../components/PortfolioChart.vue";
const ForwardObservation = defineAsyncComponent(() => import('./ForwardObservation.vue'));
import { useInvestor, intradayNames } from "./workspace";

const {
  state, draft, report, dirty, busy, methodNames, resultRows, signalName,
  startYear, endYear, periodDirty, savePreferences, connected,
  reportMatchesPlan, reportError, refresh, profileSlot,
  name, gainClass, jobsByKind, navigate, run, exportReport, inspectData, auditData,
} = useInvestor();
const plan = computed<any>(() => state.value?.settings?.plan);
const latestValidationJob = computed<any>(() => state.value?.jobs?.find(
  (job: any) => job.kind === (hourly.value ? "intraday_test" : "portfolio_test")));
const hourly = computed(() => plan.value?.research_interval !== "daily");
const yearOptions = Array.from({ length: Math.max(1, new Date().getFullYear() - 2019) }, (_, index) => 2019 + index);
const periodMatches = computed(() => !!report.value && !!plan.value &&
  Number(report.value.start.slice(0, 4)) === startYear.value &&
  Number(report.value.end.slice(0, 4)) === endYear.value);
const currentReport = computed(() => reportMatchesPlan.value && periodMatches.value ? report.value : null);
const currentResult = computed(() => currentReport.value?.results?.[plan.value?.method]);
const riskName = computed(() => ({
  conservative: "保守", balanced: "均衡", aggressive: "激进", custom: "自定义",
} as Record<string, string>)[state.value?.profile_library?.active_slot || plan.value?.profile] || "自定义");
const cadenceName = computed(() => ({
  monthly: "每月", weekly: "每周", daily: "每日",
} as Record<string, string>)[plan.value?.rebalance] || "仅首次买入");
const hourlyResult = ref<any>();
const hourlyBusy = ref(false);
const hourlyError = ref("");
let hourlyRequest = 0;
const view = ref('history');
const selectedHourly = computed(() => hourlyResult.value?.scenarios?.find(
  (item: any) => item.multiple === plan.value?.research_multiple));
const diagnostic = computed(() => hourlyResult.value?.validation);
const dailySeries = computed(() => {
  if (!currentResult.value || !plan.value) return [];
  const series = [{
    name: plan.value.research_multiple + "x 当前计划",
    color: "#0e8074",
    points: currentResult.value.curve.map((item: any) => ({
      date: item.date, value: (item.equity / plan.value.starting_cash - 1) * 100,
    })),
  }];
  for (const [key, name, color] of [["unlevered", "当前策略 1x", "#4267c6"], ["hold_1x", "同偏好持有 1x", "#b96f30"]]) {
    if (!currentReport.value.results[key] || key === 'unlevered' && plan.value.research_multiple === 1) continue;
    series.push({ name, color, points: currentReport.value.results[key].curve.map((item: any) => ({
      date: item.date, value: (item.equity / plan.value.starting_cash - 1) * 100,
    })) });
  }
  return series;
});
const hourlySeries = computed(() => {
  if (!selectedHourly.value || !hourlyResult.value) return [];
  return [{
    name: plan.value.research_multiple + "x 盘中实验",
    color: "#0e8074",
    points: selectedHourly.value.base.curve.map((item: any) => ({
      date: item.date,
      value: (item.equity / plan.value.starting_cash - 1) * 100,
    })),
  }];
});
async function loadHourly() {
  const request = ++hourlyRequest;
  hourlyBusy.value = false;
  if (!hourly.value) return;
  hourlyBusy.value = true;
  hourlyError.value = "";
  hourlyResult.value = null;
  try {
    const revision = state.value.settings.revision;
    const response = await api("/investment/intraday-research");
    if (request === hourlyRequest && revision === state.value.settings.revision &&
        response.result?.plan_key === state.value.plan_key) hourlyResult.value = response.result;
  } catch (cause: any) {
    if (request === hourlyRequest) hourlyError.value = cause.message;
  } finally {
    if (request === hourlyRequest) hourlyBusy.value = false;
  }
}
async function validateCurrent() {
  if (dirty.value) {
    navigate("plan");
    return;
  }
  if (!hourly.value && startYear.value > endYear.value) return;
  if (!hourly.value && periodDirty.value && !await savePreferences({
    validation_start_year: startYear.value, validation_end_year: endYear.value,
  })) return;
  if (hourly.value) await run("intraday_test");
  else await run("portfolio_test");
}
watch([() => state.value?.settings?.revision, () => dirty.value,
       () => state.value?.jobs?.find((job: any) => job.kind === 'intraday_test' && job.status === 'done')?.id], () => {
  hourlyResult.value = null;
  hourlyError.value = "";
  void loadHourly();
}, { immediate: true });
onBeforeUnmount(() => { ++hourlyRequest; });
</script>

<template>
  <section>
    <div class="page-title">
      <div>
        <p class="eyebrow">{{ view === 'forward' ? '独立模拟账户 · 启动时锁定参数' : '直接使用已保存的投资计划' }}</p>
        <h1>{{ view === 'forward' ? '独立跟踪' : '历史验证' }}</h1>
        <p v-if="view === 'history'" class="subtitle">{{ hourly ? `${plan.research_interval.replace('m', ' 分钟')}短样本探索` : '当前计划、持有基准与成本压力对照' }}</p>
      </div>
      <button v-if="view === 'history' && currentReport && !hourly" class="icon-button" title="导出完整验证结果"
        aria-label="导出完整验证结果" @click="exportReport"><Download :size="19" /></button>
    </div>

    <div class="validation-modes" role="tablist" aria-label="验证方式">
      <button role="tab" :aria-selected="view === 'history'" :class="{ selected: view === 'history' }" @click="view = 'history'">历史验证</button>
      <button role="tab" :aria-selected="view === 'forward'" :class="{ selected: view === 'forward' }" @click="view = 'forward'">独立跟踪</button>
    </div>
    <ForwardObservation v-if="view === 'forward'" />
    <template v-else>
    <div v-if="!hourly && reportError" class="notice warning" role="status">
      {{ reportError }} <button type="button" @click="refresh()"><RefreshCw :size="15" />重试读取结果</button>
    </div>

    <div class="validation-plan" :class="{ warning: dirty || (!hourly && !currentReport) }" role="status">
      <div>
        <strong>{{ dirty ? '投资计划有未保存修改' : '本次使用已保存的计划' }} · v{{ state.settings.revision }}</strong>
        <p>{{ hourly ? intradayNames[plan.intraday_strategy] : signalName(plan) }} · {{ hourly ? '独立盘中实验' : riskName }} · {{ hourly ? plan.research_interval.replace('m', ' 分钟') : '日线 / ' + cadenceName }} · {{ plan.research_multiple }}x
          <span v-if="hourly"> · {{ name(plan.research_symbol) }}</span>
          <span v-if="!hourly && plan.method !== 'hold'"> · {{ plan.trend_signal === 'multi_horizon' ? '多周期' : '双周期' }} · {{ plan.exposure_policy === 'volatility_target' ? '动态减仓' : '固定仓位' }}</span>
        </p>
        <small v-if="hourly">盘中验证与模拟都使用同一套 T+0 规则与成交引擎；短样本不代表长期表现。</small>
        <small v-else-if="plan.research_multiple > 1">按所选倍数逐笔重放成交、利息和维保减仓；模拟账户更新采用相同指令。</small>
      </div>
      <button type="button" @click="navigate('plan')">{{ dirty ? '查看草稿' : '修改计划' }}</button>
    </div>

    <div v-if="!hourly && currentReport?.market_revisions" class="notice warning">
      与上一份相同区间的快照相比，{{ currentReport.market_revisions.names.join('、') }} 的历史行情已修订；不同快照的收益不能直接归因于参数变化。
    </div>
    <div v-if="!hourly && currentReport?.comparison_reused" class="notice warning">
      这份同参数对照可看，但所选模型的成本加倍压力尚未补测；运行当前计划验证后更新。
    </div>

    <div class="validation-toolbar">
      <div v-if="!hourly" class="validation-period">
        <label>起始年份<select v-model.number="startYear" :disabled="busy" aria-label="验证起始年份">
          <option v-for="year in yearOptions" :key="year" :value="year">{{ year }}</option>
        </select></label>
        <label>结束年份<select v-model.number="endYear" :disabled="busy" aria-label="验证结束年份">
          <option v-for="year in yearOptions" :key="year" :value="year">{{ year }}</option>
        </select></label>
      </div>
      <button class="primary" :disabled="busy || !connected || hourlyBusy || (!hourly && startYear > endYear) || jobsByKind(hourly ? 'intraday_test' : 'portfolio_test')" @click="validateCurrent">
        <Play :size="16" />{{ dirty ? '先保存投资计划' : jobsByKind(hourly ? 'intraday_test' : 'portfolio_test') ? '验证进行中' : hourly ? '运行盘中验证' : currentReport ? '重新验证当前计划' : '验证当前计划' }}
      </button>
      <button v-if="hourly" :disabled="busy || dirty || jobsByKind('intraday_refresh')" @click="run('intraday_refresh')"><RefreshCw :size="16" />更新盘中数据</button>
      <a class="workspace-link" href="/?lab=1#compare"><FlaskConical :size="16"/>研究模型与参数</a>
    </div>
    <p v-if="!hourly && startYear > endYear" class="notice warning">起始年份不能晚于结束年份。</p>
    <p v-else-if="periodDirty && !hourly" class="small-note">所选区间尚未运行；只改变验证年份，不改变模拟账户的策略。</p>
    <div v-if="latestValidationJob && latestValidationJob.created >= (state.settings.saved_at || 0) && ['failed', 'stopped', 'interrupted'].includes(latestValidationJob.status)" class="notice error" role="alert">
      最近任务未完成：{{ latestValidationJob.error || latestValidationJob.message || '请重新运行' }}。下方如有结果，来自此前保存的历史快照，不代表本次成功。
    </div>

    <template v-if="hourly">
      <div v-if="hourlyError" class="notice error" role="alert">{{ hourlyError }}</div>
      <div v-if="hourlyBusy" class="empty compact">正在读取已保存的结果…</div>
      <div v-if="jobsByKind('intraday_test')" class="notice" role="status">盘中验证正在后台运行，结果会保存在本机。</div>
      <template v-else-if="hourlyResult">
        <div class="validation-verdict" :class="{ warning: hourlyResult.status === 'exploratory' || hourlyResult.status === 'no_data' }">
          <CircleAlert :size="21" />
          <div><strong>{{ hourlyResult.status === 'no_data' ? '数据不足，无法验证' : diagnostic?.status === 'not_qualified' ? '后段验证未通过' : '仅供短样本探索' }}</strong>
            <p>{{ hourlyResult.coverage.sessions }} 个交易日 / {{ hourlyResult.coverage.bars }} 根 {{ plan.research_interval.replace('m', ' 分钟') }} K 线。短样本不足以建立稳定盈利依据。</p></div>
        </div>
        <div class="validation-metrics" v-if="selectedHourly">
          <div><span>{{ plan.research_multiple }}x 全段探索收益（含筛选）</span><strong :class="gainClass(selectedHourly.base.return_pct)">{{ pct(selectedHourly.base.return_pct) }}</strong></div>
          <div><span>最大回撤</span><strong>{{ pct(selectedHourly.base.max_drawdown) }}</strong></div>
          <div><span>交易笔数</span><strong>{{ selectedHourly.base.trade_count }}</strong></div>
          <div><span>盘中样本</span><strong>{{ hourlyResult.coverage.sessions }} 日</strong></div>
        </div>
        <section v-if="selectedHourly" class="section">
          <h2>全段探索走势</h2>
          <PortfolioChart :series="hourlySeries" percentage />
          <p class="small-note">回测计入收线后 10 分钟更新延迟，只使用指令之后尚未开始的 K 线开盘；前向模拟按实际收到指令的时间执行。{{ hourlyResult.coverage.first }} 至 {{ hourlyResult.coverage.last }}。</p>
        </section>
        <details v-if="hourlyResult.scenarios?.length" class="section">
          <summary>查看 1x / 2x / 3x 对照与成本</summary>
          <div class="table-scroll"><table>
            <thead><tr><th>倍数</th><th>净收益</th><th>成本加倍后</th><th>最大回撤</th><th>交易</th><th>利息</th><th>维保触发</th></tr></thead>
            <tbody><tr v-for="row in hourlyResult.scenarios" :key="row.multiple" :class="{ highlight: row.multiple === plan.research_multiple }">
              <td>{{ row.multiple }}x</td><td :class="gainClass(row.base.return_pct)">{{ pct(row.base.return_pct) }}</td>
              <td>{{ pct(row.stress.return_pct) }}</td><td>{{ pct(row.base.max_drawdown) }}</td>
              <td>{{ row.base.trade_count }}</td><td>{{ money(row.base.interest) }} 元</td>
              <td>{{ row.base.margin_exit_at || '否' }}</td>
            </tr></tbody>
          </table></div>
        </details>
        <section v-if="diagnostic" class="section">
          <h2>参数筛选与后段验证</h2>
          <div class="notice" :class="diagnostic.status === 'passed_research' ? '' : 'warning'">
            {{ diagnostic.status === 'passed_research' ? '达到初步研究门槛，仍需前向观察' : '未达到启用依据，不自动替换计划' }}
          </div>
          <p v-if="diagnostic.split_date">{{ diagnostic.development_sessions }} 日筛选，{{ diagnostic.split_date }} 起 {{ diagnostic.evaluation_sessions }} 日验证。前段选出：{{ diagnostic.selected_name }}。</p>
          <p class="small-note">{{ diagnostic.reason }}</p>
          <div v-if="diagnostic.evaluation" class="table-scroll"><table>
            <thead><tr><th>后段对照</th><th>扣除估算平仓费后</th><th>最大回撤</th><th>成交</th><th>成本占本金</th></tr></thead>
            <tbody><tr v-for="item in [
              {name: '当前计划', value: diagnostic.current},
              {name: '筛选方案', value: diagnostic.evaluation},
              {name: '筛选方案 · 成本加倍', value: diagnostic.stress},
              {name: '原始均线', value: diagnostic.legacy},
              {name: '同预算持有', value: diagnostic.hold},
            ]" :key="item.name">
              <td>{{ item.name }}</td><td :class="gainClass(item.value.liquidation_return)">{{ pct(item.value.liquidation_return) }}</td>
              <td>{{ pct(item.value.max_drawdown) }}</td><td>{{ item.value.trade_count }}</td><td>{{ pct(item.value.cost_pct) }}</td>
            </tr></tbody>
          </table></div>
          <details v-if="diagnostic.checks?.length">
            <summary>检查结果与筛选记录</summary>
            <div class="check-list"><div v-for="check in diagnostic.checks" :key="check.name">
              <CircleAlert :size="17" :class="{ amber: !check.ok }" /><span>{{ check.name }}</span><b>{{ check.ok ? '通过' : '未通过' }}</b>
            </div></div>
            <div class="table-scroll"><table>
              <thead><tr><th>前段候选</th><th>成本加倍后</th><th>评分</th></tr></thead>
              <tbody><tr v-for="candidate in diagnostic.candidates" :key="candidate.key">
                <td>{{ candidate.name }}</td><td>{{ pct(candidate.development_stress.liquidation_return) }}</td><td>{{ candidate.score.toFixed(4) }}</td>
              </tr></tbody>
            </table></div>
          </details>
        </section>
        <details class="section">
          <summary>数据与限制</summary>
          <p>来源 {{ hourlyResult.coverage.source }} · 快照 {{ hourlyResult.coverage.snapshot }} · {{ hourlyResult.coverage.bars }} 根盘中线。</p>
          <p v-if="hourlyResult.coverage.quality">已出现交易日的时段覆盖 {{ pct(hourlyResult.coverage.quality.coverage_ratio) }}；缺失 {{ hourlyResult.coverage.quality.missing_bars }} 根。{{ hourlyResult.coverage.quality.note }}</p>
          <p>引擎 {{ hourlyResult.engine }} · 参数指纹 {{ hourlyResult.plan_key }} · 任务 {{ hourlyResult.job_id }} · {{ dateTime(hourlyResult.tested_at) }}。这是验证时冻结的历史快照，行情更新后需重新验证。</p>
          <p>{{ hourlyResult.assumptions?.signal }}。无盘口价差、停牌与真实强平规则；短期盈利不能证明可复制收益。</p>
        </details>
        <section v-if="hourlyResult.walk_forward?.strategies?.length" class="section">
          <h2>多时段对照 · {{ plan.research_multiple }}x</h2>
          <p class="small-note">{{ hourlyResult.walk_forward.reason }}</p>
          <details v-for="strategy in hourlyResult.walk_forward.strategies" :key="strategy.strategy">
            <summary>{{ strategy.name }} · 成本加倍后盈利 {{ strategy.positive_stress_periods }}/{{ strategy.periods.length }} 段 · 未获实盘验证</summary>
            <div class="table-scroll"><table>
              <thead><tr><th>独立测试段</th><th>净收益</th><th>成本加倍</th><th>1x 同预算持有</th><th>回撤</th><th>成交</th><th>模型就绪</th></tr></thead>
              <tbody><tr v-for="period in strategy.periods" :key="period.start">
                <td>{{ period.start }} 至 {{ period.end }}</td><td :class="gainClass(period.base.liquidation_return)">{{ pct(period.base.liquidation_return) }}</td>
                <td>{{ pct(period.stress.liquidation_return) }}</td><td>{{ pct(period.hold.liquidation_return) }}</td>
                <td>{{ pct(period.base.max_drawdown) }}</td><td>{{ period.base.trade_count }}</td>
                <td>{{ strategy.strategy === 'ml_edge' ? `${period.model_ready}/${period.model_decisions}` : '不适用' }}</td>
              </tr></tbody>
            </table></div>
            <div class="check-list"><div v-for="check in strategy.checks" :key="check.name">
              <CircleAlert :size="17" :class="{ amber: !check.ok }" /><span>{{ check.name }}</span><b>{{ check.ok ? '通过' : '未通过' }}</b>
            </div></div>
          </details>
        </section>
      </template>
      <div v-else class="empty report-empty">
        <ChartNoAxesCombined :size="32" /><h2>准备验证盘中行情</h2>
        <p>所选 {{ name(plan.research_symbol) }} · {{ plan.research_multiple }}x，点击上方按钮运行。</p>
      </div>
    </template>

    <template v-else>
      <template v-if="currentReport && currentResult">
        <div class="result-identity" role="status"><span>匹配已保存计划 · {{ currentReport.start }} 至 {{ currentReport.end }}</span><span>参数 {{ currentReport.plan_key.slice(0, 8) }} · {{ currentReport.tested_at ? dateTime(currentReport.tested_at) : '历史快照' }}</span></div>
        <div class="validation-verdict" :class="{warning: currentReport.checks?.some((item: any) => !item.ok && !item.not_applicable)}">
          <ShieldCheck :size="21" />
          <div><strong>{{ currentReport.verdict }}</strong>
            <p>历史结果不是未来收益；目前不代表可以实盘交易。</p></div>
        </div>
        <div class="validation-metrics">
          <div><span>{{ plan.research_multiple }}x 区间累计收益</span><strong :class="gainClass(currentResult.return_pct)">{{ pct(currentResult.return_pct) }}</strong><small v-if="currentResult.annualized_return != null">折合年化 {{ pct(currentResult.annualized_return) }}</small></div>
          <div><span>最大回撤</span><strong>{{ pct(currentResult.max_drawdown) }}</strong></div>
          <div><span>{{ plan.research_multiple }}x 同偏好持有</span><strong :class="gainClass(currentReport.results.hold?.return_pct)">{{ pct(currentReport.results.hold?.return_pct) }}</strong></div>
          <div><span>交易笔数</span><strong>{{ currentResult.trade_count }}</strong><small>借款利息 {{ money(currentResult.interest_paid || 0) }} 元</small></div>
        </div>
        <section class="section">
          <h2>逐笔执行后的资产走势</h2>
          <PortfolioChart :series="dailySeries" percentage />
          <p class="small-note">{{ signalName(plan) }} · {{ cadenceName }} · {{ plan.research_multiple }}x · {{ currentReport.start }} 至 {{ currentReport.end }}。逐笔计入佣金、滑点、借款利息；维保触发 {{ currentResult.margin_exit_at || '无' }}。不是券商真实成交。</p>
        </section>
        <details class="section">
          <summary>逐年表现与其他策略</summary>
          <p class="small-note">年度主表使用当前 {{ plan.research_multiple }}x；策略对照中标明 1x 的行使用无借款账户。持有基准不应用择时和动态减仓。</p>
          <div class="table-scroll"><table>
            <thead><tr><th>年份</th><th>当前组合</th><th>同偏好持有</th></tr></thead>
            <tbody><tr v-for="row in currentResult.per_year" :key="row.year">
              <td>{{ row.year }}</td><td :class="gainClass(row.return_pct)">{{ pct(row.return_pct) }}</td>
              <td>{{ pct(currentReport.results.hold.per_year.find((other: any) => other.year === row.year)?.return_pct) }}</td>
            </tr></tbody>
          </table></div>
          <h3>策略、杠杆与成本对照</h3>
          <div class="table-scroll"><table>
            <thead><tr><th>策略</th><th>收益</th><th>最大回撤</th><th>交易笔数</th></tr></thead>
            <tbody><tr v-for="row in resultRows" :key="row.key" :class="{ highlight: row.key === plan.method }">
              <td>{{ row.key === 'ml' ? signalName({ ...plan, method: 'ml' }) : methodNames[row.key] }}</td><td :class="gainClass(row.return_pct)">{{ pct(row.return_pct) }}</td>
              <td>{{ pct(row.max_drawdown) }}</td><td>{{ row.trade_count }}</td>
            </tr></tbody>
          </table></div>
        </details>
        <details class="section">
          <summary>验证方法、来源与限制</summary>
          <p>{{ currentReport.protocol }}</p>
          <p>引擎 {{ currentReport.engine }} · 验证编号 {{ currentReport.id }} · 参数指纹 {{ currentReport.plan_key }} · {{ currentReport.tested_at ? dateTime(currentReport.tested_at) : '历史结果' }}</p>
          <ul><li v-for="item in currentReport.audit?.limitations || []" :key="item">{{ item }}</li></ul>
          <div class="check-list">
            <div v-for="item in currentReport.checks || []" :key="item.name">
              <CircleAlert :size="17" :class="{ amber: !item.ok }" /><span><strong>{{ item.name }}</strong><small>{{ item.detail }}</small></span>
              <b>{{ item.not_applicable ? '不适用' : item.pending ? '待验证' : item.ok ? '通过' : '未通过' }}</b>
            </div>
          </div>
          <p v-if="currentResult.training?.length" class="small-note">当前模型 {{ currentResult.training.filter((item: any) => item.status === 'model').length }}/{{ currentResult.training.length }} 次训练检查通过。</p>
        </details>
      </template>
      <div v-else class="empty report-empty">
        <ChartNoAxesCombined :size="32" />
        <h2>当前计划尚未验证</h2>
        <p v-if="report && !periodMatches">已有结果对应 {{ report.start }} 至 {{ report.end }}；本次选择 {{ startYear }}–{{ endYear }} 年，旧结果不再展示。</p>
        <p v-else>{{ dirty ? '草稿尚未启用；先保存计划，再验证。' : `所选 ${startYear}–${endYear} 年尚无匹配结果。` }}</p>
      </div>
    </template>

    <details v-if="!hourly" class="section" @toggle="($event.target as HTMLDetailsElement).open && inspectData()">
      <summary>行情数据覆盖</summary>
      <div v-if="auditData" class="table-scroll"><table>
        <thead><tr><th>标的</th><th>开始</th><th>结束</th><th>日线数量</th><th>检查</th></tr></thead>
        <tbody><tr v-for="row in auditData.audit.rows" :key="row.symbol">
          <td>{{ row.name }}</td><td>{{ row.start || '无' }}</td><td>{{ row.end || '无' }}</td>
          <td>{{ row.bars }}</td><td>{{ row.ok ? '通过' : row.issues.join('；') }}</td>
        </tr></tbody>
      </table></div>
    </details>
    </template>
  </section>
</template>
