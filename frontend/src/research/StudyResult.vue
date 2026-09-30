<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { ArrowUpRight, Trophy } from '@lucide/vue';
import { pct, money } from '../api';
import PortfolioChart from '../components/PortfolioChart.vue';
import { fieldNames } from './workspace';
const props = defineProps<{job: any}>();
const picked = ref(''), phase = ref('selection');
const result = computed(() => props.job?.result);
const rows = computed<any[]>(() => result.value?.candidates || []);
const row = computed(() => rows.value.find(r => r.id === picked.value) || rows.value.find(r => r.id === result.value?.winner_id) || rows.value[0]);
const stats = computed(() => row.value?.[phase.value]);
const baseline = computed(() => rows.value.find(r => r.role === 'baseline'));
const benchmark = computed(() => rows.value.find(r => r.role === 'benchmark'));
const difference = computed(() => Object.entries(row.value?.plan || {}).filter(([k, v]) =>
  !['name','profile','preset_version','auto_update'].includes(k) && JSON.stringify(v) !== JSON.stringify(result.value?.base_plan[k])));
const series = computed(() => {
  const ids = new Set<string>();
  return [row.value, baseline.value, benchmark.value].filter(r => r && r[phase.value]?.curve && !ids.has(r.id) && ids.add(r.id)).map((r, i) => ({
    name: r.id === row.value?.id ? '选中候选' : r.role === 'baseline' ? '当前计划' : '持有基准',
    color: ['#0e8074', '#5576b8', '#9b7846'][i],
    points: r[phase.value].curve.map((p: any) => ({date: p.date, value: (p.equity / r.plan.starting_cash - 1) * 100})),
  }));
});
const promotable = computed(() => props.job.status === 'done' && result.value?.complete && row.value?.role !== 'benchmark' && row.value?.selection?.score != null && (result.value.study.mode !== 'tune' || row.value.check));
const handoff = computed(() => `/?research_job=${props.job.id}&research_candidate=${row.value?.id}#plan`);
const label = (key: string) => fieldNames[key] || ({model_tuning:'研究参数', method:'信号类型', model_recipe:'模型配方', training_years:'训练年数', horizon:'预测跨度', model_iterations:'迭代次数', model_leaves:'叶节点', cnn_window:'序列长度', cnn_epochs:'训练轮数'} as Record<string,string>)[key] || key;
const readable = (value: any) => value == null ? '默认' : typeof value === 'object'
  ? Object.entries(value).filter(([,v]) => v != null).map(([k,v]) => `${label(k)}: ${v}`).join('；') : String(value);
watch(() => props.job.id, () => {picked.value = ''; phase.value = 'selection';});
</script>

<template>
  <section v-if="result" class="study-results">
    <div class="research-heading"><h2>研究结果</h2><span class="research-status">{{ result.complete ? '全部完成' : '阶段结果' }}</span></div>
    <div class="research-provenance">
      <span>调仓：{{ {daily:'每日',weekly:'每周',monthly:'每月'}[result.base_plan.rebalance as string] }}</span>
      <span>{{ result.base_plan.research_multiple }}x · {{ money(result.base_plan.starting_cash) }} 元</span>
      <span>佣金 {{ pct(result.base_plan.commission_rate) }} · 滑点 {{ result.base_plan.slippage_bps }} bps</span>
      <span>计划 {{ result.base_plan_key.slice(0, 8) }} · 行情 {{ result.data_identity.slice(0, 8) }}</span>
    </div>
    <div v-if="result.winner_id" class="research-winner"><Trophy :size="18"/><span>{{ result.study.mode === 'tune' ? '本模型参数候选评分最高' : '策略候选评分最高（不含持有基准）' }}：<strong>{{ rows.find(r => r.id === result.winner_id)?.label }}</strong></span></div>
    <div v-if="result.errors.length" class="notice error">有候选未完成，暂不选优。已完成阶段保留，可继续重试。</div>
    <div class="research-table-wrap" tabindex="0" aria-label="候选对照结果">
      <table class="research-table">
        <thead><tr><th>候选 / 参数</th><th>年化收益</th><th>最大回撤</th><th>交易</th><th>模型采用 / 回退月</th><th>评分</th></tr></thead>
        <tbody><tr v-for="r in rows" :key="r.id" :class="{chosen: row?.id === r.id}">
          <td><button class="research-row-button" @click="picked = r.id; phase = 'selection'">{{ r.label }}<Trophy v-if="r.id === result.winner_id" :size="14"/></button><small>{{ r.role === 'baseline' ? '原计划' : r.role === 'benchmark' ? '持有基准' : r.id.slice(0, 8) }}</small></td>
          <td v-if="r.selection?.error" colspan="5" class="research-error">{{ r.selection.error }}</td>
          <template v-else><td :class="{'positive': r.selection?.annualized_return > 0, 'negative': r.selection?.annualized_return < 0}">{{ pct(r.selection?.annualized_return) }}</td><td>{{ pct(r.selection?.max_drawdown) }}</td><td>{{ r.selection?.trade_count ?? '待计算' }}</td><td>{{ r.selection ? `${r.selection.model_months} / ${r.selection.fallback_months}` : '待计算' }}</td><td>{{ r.selection?.score?.toFixed(4) ?? '—' }}</td></template>
        </tr></tbody>
      </table>
    </div>
    <small class="research-score-rule">{{ result.score_rule }}。历史研究不保证未来收益。</small>
    <template v-if="row">
      <div class="research-detail-heading"><h3>{{ row.label }}</h3><a v-if="promotable" class="research-link" :href="handoff"><ArrowUpRight :size="16"/>送到投资计划草稿</a></div>
      <div class="research-segments" role="group" aria-label="结果区间">
        <button :class="{selected: phase === 'selection'}" @click="phase = 'selection'">{{ result.study.start_year }}–{{ result.study.end_year }} · {{ result.study.mode === 'tune' ? '调参区间' : '对比区间' }}</button>
        <button v-if="result.study.mode === 'tune'" :disabled="!row.check" :class="{selected: phase === 'check'}" @click="phase = 'check'">{{ result.study.check_start_year }}–{{ result.study.check_end_year }} · 后续检验</button>
      </div>
      <div v-if="stats?.curve?.length">
        <p v-if="benchmark?.[phase]?.annualized_return > stats.annualized_return" class="research-caution">这段区间的年化收益低于持有基准，请同时比较回撤和成本，不能仅凭策略候选排名认定更优。</p>
        <div class="research-metrics"><div><small>区间收益</small><strong>{{ pct(stats.return_pct) }}</strong></div><div><small>最大回撤</small><strong>{{ pct(stats.max_drawdown) }}</strong></div><div><small>佣金 / 融资利息</small><strong>{{ money(stats.fees) }} / {{ money(stats.interest_paid) }}</strong></div></div>
        <PortfolioChart :series="series" percentage />
        <div class="research-table-wrap" tabindex="0" aria-label="年度对照"><table class="research-table research-year-table"><thead><tr><th>年份</th><th>候选收益</th><th>原计划</th><th>持有收益</th><th>最大回撤</th></tr></thead><tbody><tr v-for="year in stats.per_year" :key="year.year"><td>{{ year.year }}</td><td>{{ pct(year.return_pct) }}</td><td>{{ pct(baseline?.[phase]?.per_year?.find((y: any) => y.year === year.year)?.return_pct) }}</td><td>{{ pct(benchmark?.[phase]?.per_year?.find((y: any) => y.year === year.year)?.return_pct) }}</td><td>{{ pct(year.max_drawdown) }}</td></tr></tbody></table></div>
      </div>
      <div v-else class="research-empty">{{ stats?.error || '此阶段尚无结果' }}</div>
      <details class="research-disclosure"><summary>参数变化与训练审计</summary>
        <dl class="research-diff"><template v-for="[key, value] in difference" :key="key"><dt>{{ label(key) }}</dt><dd>{{ readable(result.base_plan[key]) }} → {{ readable(value) }}</dd></template></dl>
        <p v-if="!difference.length">与原计划参数一致。</p>
        <div v-if="stats?.training?.length" class="research-table-wrap" tabindex="0"><table class="research-table"><thead><tr><th>训练截止</th><th>最新标签截止</th><th>样本</th><th>使用情况</th></tr></thead><tbody><tr v-for="(r, i) in stats.training" :key="i"><td>{{ r.cutoff || '—' }}</td><td>{{ r.label_end || '—' }}</td><td>{{ r.samples ?? '—' }}</td><td>{{ r.status === 'model' ? '模型通过时间验证' : '回退趋势规则' }}</td></tr></tbody></table></div>
      </details>
      <p v-if="result.study.mode === 'tune'" class="research-caution">候选只按调参区间选优，后续检验不参与排名。已经反复看过的历史年份不等于真正未知的未来。</p>
    </template>
  </section>
</template>
