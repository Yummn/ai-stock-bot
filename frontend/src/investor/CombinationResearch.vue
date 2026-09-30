<script setup lang="ts">
import { computed, ref } from 'vue';
import { Download, CircleAlert } from '@lucide/vue';
import { api, pct, money } from '../api';
import PortfolioChart from '../components/PortfolioChart.vue';

const report = ref<any>();
const error = ref('');
const loading = ref(false);
const selected = ref('frozen-forward-2x');
const period = ref('continuous');
const upgradePeriod = ref('continuous');
const resiliencePeriod = ref('continuous');
const resilienceChecks: Record<string, string> = {
  return_target: '后段和全程净年化均达到20%', drawdown_limit: '正常及压力回撤不超过20%',
  completed_years_positive: '完整年份均盈利', recent_positive: '2026年内盈利',
  stress_profitable: '各区间成本压力后均盈利', beats_hold: '后段和全程超过1x持有', solvent: '未资不抵债',
};
function failedChecks(row: any) {
  return Object.entries(row.gate.checks).filter(([, ok]) => !ok).map(([key]) => resilienceChecks[key] || key).join('、');
}
const periods = [
  { id: 'continuous', name: '2020–2026 连续账户' },
  { id: 'later', name: '2023–2025 后段检查' },
  { id: 'partial', name: '2026 年内独立起步' },
];
const selectedRow = computed<any>(() => report.value?.rows.find((r: any) => r.id === selected.value));
const result = computed<any>(() => selectedRow.value?.periods[period.value]);
const comparison = computed<any>(() => report.value?.rows.find((r: any) => r.id === 'hold-1x')?.periods[period.value]);
const chart = computed(() => !result.value ? [] : [
  { name: '当前研究方案', color: '#0e8074', points: result.value.base.curve.map((r: any) => ({ date: r.date, value: (r.equity / 100000 - 1) * 100 })) },
  { name: '完整持有 1x', color: '#4267c6', points: comparison.value.base.curve.map((r: any) => ({ date: r.date, value: (r.equity / 100000 - 1) * 100 })) },
]);
const checks: Record<string, string> = {
  return_target: '后段和全程净年化均 ≥20%', drawdown: '最大回撤不超过20%',
  stress_profitable: '各区间成本压力后均盈利', positive_completed_years: '所有完整年份均盈利',
  positive_partial: '2026年内独立起步盈利', beats_hold: '后段和全程均超过1x持有', solvent: '无模拟资不抵债',
};
async function load(event: Event) {
  if (!(event.target as HTMLDetailsElement).open || report.value || loading.value) return;
  loading.value = true;
  error.value = '';
  try {
    report.value = (await api('/investment/research-combinations')).report;
    if (report.value && !selectedRow.value) selected.value = report.value.rows[0]?.id;
  } catch (cause: any) { error.value = cause.message; }
  finally { loading.value = false; }
}
</script>

<template>
  <details class="section research-section" @toggle="load">
    <summary>独立研究 · 策略、模型、频率与杠杆</summary>
    <p v-if="loading" role="status">正在读取研究结果…</p>
    <p v-else-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-else-if="!report" class="small-note">暂无已完成的研究结果。</p>
    <template v-else>
      <div class="research-heading">
        <div><h2>{{ report.verdict }}</h2>
          <p>{{ report.candidate_count }} 个候选 · {{ report.replays }} 次账户回放 · {{ report.id }}</p></div>
        <a class="icon-button" href="/api/v2/investment/research-combinations/export" title="下载研究结果" aria-label="下载研究结果"><Download :size="18" /></a>
      </div>
      <p class="notice warning">这是独立的冻结行情研究，不是当前投资计划的验证结果。未更改模拟账户与前向观察；历史数据已被多次研究，不是全新盲测。</p>
      <details v-for="study in report.resilience_studies || []" :key="study.id" class="resilience-study">
        <summary>改进检验 · {{ study.title }}</summary>
        <p>{{ study.candidate_count }} 个候选 · {{ study.replays }} 次回放 · {{ study.passed.length ? '仅通过历史筛选，尚无前向证明' : '未通过全部门槛，不替换当前方案' }}</p>
        <div class="research-fields"><label>对照区间<select v-model="resiliencePeriod" :aria-label="'改进检验区间 ' + study.id"><option v-for="p in periods" :key="p.id" :value="p.id">{{ p.name }}</option></select></label></div>
        <div class="table-scroll"><table><thead><tr><th>方案</th><th>{{ resiliencePeriod === 'partial' ? '年内净收益' : '净年化' }}</th><th>最大回撤</th><th>压力后{{ resiliencePeriod === 'partial' ? '年内收益' : '年化' }}</th><th>筛选</th></tr></thead><tbody>
          <tr v-for="row in study.rows" :key="row.id"><td>{{ row.name }}</td><td>{{ pct(row.periods[resiliencePeriod].base[resiliencePeriod === 'partial' ? 'liquidation_return' : 'liquidation_cagr']) }}</td><td>{{ pct(row.periods[resiliencePeriod].base.max_drawdown) }}</td><td>{{ pct(row.periods[resiliencePeriod].stress[resiliencePeriod === 'partial' ? 'liquidation_return' : 'liquidation_cagr']) }}</td><td>{{ row.gate.historical_passed ? '历史通过' : '未通过' }}</td></tr>
        </tbody></table></div>
        <details><summary>淘汰原因与不确定性</summary>
          <div v-for="row in study.rows" :key="row.id" class="candidate-audit">
            <strong>{{ row.name }}</strong>
            <p>{{ row.gate.historical_passed ? '历史门槛通过，但尚无未来表现证明。' : '未通过：' + failedChecks(row) }}</p>
            <p class="small-note" v-for="interval in row.uncertainty" :key="interval.block_sessions">{{ interval.block_sessions }} 日块重采样：相对原方案的年化差额区间 {{ (interval.low * 100).toFixed(2) }} 至 {{ (interval.high * 100).toFixed(2) }} 个百分点。</p>
          </div>
          <p class="small-note">区间使用2000次配对重采样，保留局部时间相关性；未校正多次择参，仅供探索。跨零意味着提升方向仍不确定。</p>
        </details>
        <details><summary>全部候选 · 开发期</summary><div class="table-scroll"><table><thead><tr><th>方案</th><th>净年化</th><th>压力后年化</th><th>最大回撤</th></tr></thead><tbody><tr v-for="row in study.development" :key="row.id"><td>{{ row.name }}</td><td>{{ pct(row.base.liquidation_cagr) }}</td><td>{{ pct(row.stress.liquidation_cagr) }}</td><td>{{ pct(row.base.max_drawdown) }}</td></tr></tbody></table></div></details>
        <p class="small-note">各组先按2020–2022开发期选定候选，再检查后续年份。三轮共享已看过的历史，不能当作三次独立验证。均保持2x上限、30%波动目标；没有通过加风险提高成绩。2026年未满年。</p>
      </details>
      <details v-for="study in report.execution_upgrades || []" :key="study.id" class="execution-study">
        <summary>{{ study.id.includes('execution-risk') ? '新增 · 高风险与频率对照' : '新增 · 执行修正与融资成本对照' }}</summary>
        <p>{{ study.candidate_count }} 个候选 · {{ study.replays }} 次完整回放 · {{ study.id }}</p>
        <div class="research-fields"><label>对照区间<select v-model="upgradePeriod" :aria-label="'执行研究区间 ' + study.id"><option v-for="p in periods" :key="p.id" :value="p.id">{{ p.name }}</option></select></label></div>
        <div class="table-scroll"><table><thead><tr><th>方案</th><th>{{ upgradePeriod === 'partial' ? '年内净收益' : '净年化' }}</th><th>最大回撤</th><th>压力后{{ upgradePeriod === 'partial' ? '年内收益' : '年化' }}</th><th>融资利息 / 元</th><th>成交</th></tr></thead><tbody>
          <tr v-for="row in study.rows" :key="row.id"><td>{{ row.name }}</td><td>{{ pct(row.periods[upgradePeriod].base[upgradePeriod === 'partial' ? 'liquidation_return' : 'liquidation_cagr']) }}</td><td>{{ pct(row.periods[upgradePeriod].base.max_drawdown) }}</td><td>{{ pct(row.periods[upgradePeriod].stress[upgradePeriod === 'partial' ? 'liquidation_return' : 'liquidation_cagr']) }}</td><td>{{ money(row.periods[upgradePeriod].base.interest_paid) }}</td><td>{{ row.periods[upgradePeriod].base.trade_count }}</td></tr>
        </tbody></table></div>
        <p class="small-note">只按 2020–2022 开发期选定调仓频率，后段不改选。成本约束可降低实际仓位，2x/3x 是上限而非满额借款；国债不放大。2026 年只显示截至 9 月 28 日的区间收益，不冒充全年收益。此处模型沿用独立研究的滚动预测，不能把结果当作其他模型参数的验证。</p>
      </details>
      <div class="research-fields">
        <label>查看方案<select v-model="selected" aria-label="研究方案"><option v-for="row in report.rows" :key="row.id" :value="row.id">{{ row.name }}{{ row.id.startsWith('locked-') ? ' · 倍数对照' : '' }}</option></select></label>
        <label>检查区间<select v-model="period" aria-label="研究区间"><option v-for="p in periods" :key="p.id" :value="p.id">{{ p.name }}</option></select></label>
      </div>
      <template v-if="result">
        <div class="research-metrics">
          <div><span>净年化收益</span><strong>{{ pct(result.base.liquidation_cagr) }}</strong></div>
          <div><span>最大回撤</span><strong>{{ pct(result.base.max_drawdown) }}</strong></div>
          <div><span>压力后净年化</span><strong>{{ pct(result.stress.liquidation_cagr) }}</strong></div>
          <div><span>1x 持有净年化</span><strong>{{ pct(comparison.base.liquidation_cagr) }}</strong></div>
        </div>
        <PortfolioChart :series="chart" percentage />
        <p class="small-note">{{ result.base.start }} 至 {{ result.base.end }} · 累计净收益 {{ pct(result.base.liquidation_return) }} · 成交 {{ result.base.trade_count }} 笔 · 平均总仓位 {{ pct(result.base.mean_exposure) }} · 压力：双倍佣金与滑点、借款年利率12%。曲线每5根日线取点，指标使用全部日线；净收益另扣期末估算平仓成本。累计收益不等于每年收益。</p>
        <div class="table-scroll"><table><thead><tr><th>年份</th><th>策略收益</th><th>1x 持有</th><th>年内回撤</th></tr></thead>
          <tbody><tr v-for="year in result.base.per_year" :key="year.year"><td>{{ year.year }}{{ year.year === 2026 ? '（未满年）' : '' }}</td><td>{{ pct(year.return_pct) }}</td><td>{{ pct(comparison.base.per_year.find((r: any) => r.year === year.year)?.return_pct) }}</td><td>{{ pct(year.max_drawdown) }}</td></tr></tbody>
        </table></div>
        <details><summary>完整筛选检查</summary>
          <div class="check-list"><div v-for="(label, key) in checks" :key="key"><CircleAlert :size="17" /><span>{{ label }}</span><b>{{ selectedRow.gate[key] ? '通过' : '未通过' }}</b></div></div>
          <p class="small-note">年化20%与回撤20%是本轮研究门槛，不是收益承诺。通过筛选也不代表可以盈利，更不等于适合实盘加杠杆。</p>
        </details>
        <details><summary>冻结参数</summary>
          <pre>{{ JSON.stringify({ plan: selectedRow.candidate.plan, model: report.models[selectedRow.candidate.model]?.spec || null }, null, 2) }}</pre>
        </details>
      </template>
      <details><summary>全部候选 · 2020–2022 开发期</summary>
        <div class="table-scroll"><table><thead><tr><th>方案</th><th>净年化</th><th>压力后净年化</th><th>最大回撤</th><th>成交</th></tr></thead>
          <tbody><tr v-for="row in report.development" :key="row.id"><td>{{ row.name }}</td><td>{{ pct(row.base.liquidation_cagr) }}</td><td>{{ pct(row.stress.liquidation_cagr) }}</td><td>{{ pct(row.base.max_drawdown) }}</td><td>{{ row.base.trade_count }}</td></tr></tbody>
        </table></div>
      </details>
      <details><summary>模型采用情况与研究边界</summary>
        <div class="table-scroll"><table><thead><tr><th>模型</th><th>时间验证通过月份</th></tr></thead><tbody>
          <tr v-for="(model, key) in report.models" :key="key"><td>{{ model.name }}</td><td>{{ model.failed ? '训练失败，不参与排名' : `${model.accepted} / ${model.months}` }}</td></tr>
        </tbody></table></div>
        <p>模型时间验证未通过时使用趋势策略，因此混合方案的全部收益不能归功于机器学习。模型仅用当时已完成的标签训练；训练、验证区间按标签结束时间隔离。</p>
        <p>固定六只ETF，不代表全市场。未还原完整盘口、市场冲击、未来流动性或券商强平过程。每日调整不是交易所高频交易。只按开发期选方案，后段结果不用于改选；仍有重复研究历史数据的选择偏差。</p>
        <p v-if="report.benchmark_correction">{{ report.benchmark_correction.reason }}</p>
        <p>未完成任务 {{ report.failures.length }} 项 · 子进程失败尝试 {{ report.failed_attempts || 0 }} 次（记录原错误，只允许同输入重试） · 冻结行情 {{ report.source_hash.slice(0, 16) }} · {{ report.engine }}</p>
      </details>
    </template>
  </details>
</template>

<style scoped>
.research-section { min-width: 0; }
.research-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-top: 20px; }
.research-heading h2 { font-size: 18px; line-height: 1.5; }
.research-fields { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: 16px; margin: 20px 0; }
.research-fields label { min-width: 0; display: grid; gap: 8px; }
.research-fields select { min-width: 0; width: 100%; max-width: 100%; }
.research-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-block: 1px solid var(--border, #dde3e8); margin: 16px 0; }
.research-metrics > div { display: grid; gap: 8px; padding: 16px 8px; }
.research-metrics span { font-size: 13px; color: #5b6d76; }
.research-metrics strong { font-size: 23px; }
.research-section table { min-width: 500px; }
.research-section .table-scroll { max-width: 100%; overflow-x: auto; }
.research-section .small-note { overflow-wrap: anywhere; }
.research-section pre { max-height: 300px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
.candidate-audit { padding-block: 12px; border-bottom: 1px solid var(--border, #dde3e8); }
@media (max-width: 640px) {
  .research-fields { grid-template-columns: minmax(0, 1fr); }
  .research-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .research-metrics strong { font-size: 21px; }
}
</style>
