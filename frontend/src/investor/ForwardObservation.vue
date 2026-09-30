<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue';
import { Play, Pause, Archive, RefreshCw, Download } from '@lucide/vue';
import { api, pct, dateTime, money } from '../api';
import PortfolioChart from '../components/PortfolioChart.vue';
import { useInvestor } from './workspace';

const { state, dirty, writable, showLogin, signalName, gainClass, refresh } = useInvestor();
const data = ref<any>({ current: null, archives: [] });
const loading = ref(true), busy = ref(false), error = ref(''), confirming = ref(false);
const study = computed(() => data.value.current);
const working = computed(() => ['queued', 'running'].includes(data.value.job?.status));
const names: Record<string, string> = { strategy: '冻结计划', unlevered: '同策略 1x', hold: '同偏好持有 1x' };
const summary = computed(() => study.value?.books?.strategy);
const stamp = (value: string) => dateTime(Date.parse(value) / 1000);
const series = computed(() => Object.entries(study.value?.books || {}).map(([key, value]: [string, any]) => ({
  name: names[key], color: ({ strategy: '#0e8074', unlevered: '#4267c6', hold: '#b96f30' } as any)[key],
  points: value.curve.map((p: any) => ({ date: p.date, value: (p.equity / study.value.plan.starting_cash - 1) * 100 })),
})));
let alive = true, timer: ReturnType<typeof setTimeout>;
let readSequence = 0;
async function load() {
  if (busy.value) return;
  const sequence = ++readSequence;
  try {
    const result = await api('/investment/observation');
    if (alive && sequence === readSequence) {data.value = result; error.value = '';}
  }
  catch (cause: any) { if (alive && sequence === readSequence) error.value = cause.message; }
  finally { if (alive && sequence === readSequence) loading.value = false; }
}
async function poll() { await load(); if (alive) timer = setTimeout(poll, working.value ? 2500 : 10000); }
async function act(action: string) {
  if (busy.value) return;
  if (!writable.value) { showLogin.value = true; return; }
  busy.value = true; error.value = '';
  ++readSequence;
  try {
    data.value = await api('/investment/observation', { method: 'POST', body: JSON.stringify({
      action, revision: study.value?.revision, plan_key: state.value.plan_key,
    }) });
    confirming.value = false;
    await refresh();
  } catch (cause: any) { error.value = cause.message; }
  finally { busy.value = false; }
}
async function download(id: string) {
  try {
    const result = await api('/investment/observation/export?study_id=' + id);
    const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `observation-${id}.json`; link.click();
    URL.revokeObjectURL(url);
  } catch (cause: any) { error.value = cause.message; }
}
onMounted(poll);
onBeforeUnmount(() => { alive = false; clearTimeout(timer); });
</script>

<template>
  <section aria-label="前向观察">
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <div v-if="loading" class="empty">正在读取观察记录…</div>
    <template v-else-if="!study">
      <div class="validation-plan">
        <div><strong>尚未开始独立观察</strong>
          <p>{{ signalName(state.settings.plan) }} · {{ state.settings.plan.research_multiple }}x · {{ money(state.settings.plan.starting_cash) }} 元模拟本金</p>
          <small>收益从启动后计算；参数固定，三组模拟账户分别记账，不影响总览账户。</small>
        </div>
        <button class="primary" :disabled="busy || dirty || state.settings.plan.research_interval !== 'daily'" @click="act('start')"><Play :size="16" />开始观察已保存计划</button>
      </div>
      <p v-if="dirty" class="notice warning">投资计划尚有未保存修改。</p>
      <p v-if="state.settings.plan.research_interval !== 'daily'" class="notice warning">独立对照暂仅支持日线。盘中模拟仍在总览运行。</p>
    </template>
    <template v-else>
      <div class="validation-plan" :class="{ warning: !study.same_plan || study.status === 'paused' }">
        <div><strong>{{ study.status === 'paused' ? '已暂停' : '观察中' }} · {{ study.plan.name }}</strong>
          <p>{{ signalName(study.plan) }} · {{ study.plan.research_multiple }}x · {{ stamp(study.created_at) }} 启动</p>
          <small v-if="!study.same_plan">当前投资计划与此观察不同；此处仍使用启动时的参数，记录未被重置。</small>
          <small v-else>计划已冻结 · 参数编号 {{ study.plan_key }}</small>
        </div>
        <button v-if="study.status === 'active'" :disabled="busy" @click="act('pause')"><Pause :size="16" />暂停观察</button>
        <button v-else :disabled="busy || !study.engine_matches" @click="act('resume')"><Play :size="16" />继续观察</button>
      </div>
      <p v-if="!study.engine_matches" class="notice warning">引擎版本已变化，自动观察停止；旧记录仍保留。</p>
      <div class="observation-tools">
        <span>{{ study.verdict }} · 最新行情 {{ study.last_quote || '待获取' }}</span>
        <button :disabled="busy || working || study.status !== 'active' || !study.engine_matches" @click="act('update')"><RefreshCw :size="16" />{{ working ? '更新中' : '更新观察' }}</button>
        <button class="icon-button" title="导出观察记录" aria-label="导出观察记录" @click="download(study.id)"><Download :size="18" /></button>
      </div>
      <p v-if="data.job?.error" class="notice warning">本次更新未完成：{{ data.job.error }}。已保存记录不受影响。</p>
      <div v-if="summary" class="validation-metrics">
        <div><span>冻结计划收益</span><strong :class="gainClass(summary.return_pct)">{{ pct(summary.return_pct) }}</strong></div>
        <div><span>同偏好持有 1x</span><strong :class="gainClass(study.books.hold.return_pct)">{{ pct(study.books.hold.return_pct) }}</strong></div>
        <div><span>按时观察 / 行情日</span><strong>{{ summary.observed_sessions }} / {{ summary.sessions }}</strong></div>
        <div><span>离线补算行情日</span><strong>{{ summary.reconstructed_sessions }}</strong></div>
      </div>
      <section v-if="summary?.sessions" class="section">
        <h2>启动后的模拟收益</h2><PortfolioChart :series="series" percentage />
      </section>
      <div v-if="summary" class="table-scroll">
        <table><thead><tr><th>对照方案</th><th>收益</th><th>最大回撤</th><th>成交</th><th>费用 / 利息</th></tr></thead>
          <tbody><tr v-for="(book, key) in study.books" :key="key"><td>{{ names[key] }}</td><td :class="gainClass(book.return_pct)">{{ pct(book.return_pct) }}</td><td>{{ pct(book.max_drawdown) }}</td><td>{{ book.trade_count }}</td><td>{{ money(book.fees) }} / {{ money(book.interest_paid) }}</td></tr></tbody>
        </table>
      </div>
      <details class="section"><summary>冻结参数与归档</summary>
        <dl class="live-facts"><div><dt>模拟本金</dt><dd>{{ money(study.plan.starting_cash) }} 元</dd></div>
          <div><dt>调仓频率</dt><dd>{{ ({ daily: '每日', weekly: '每周', monthly: '每月' } as any)[study.plan.rebalance] }}</dd></div>
          <div><dt>仓位上限 / 倍数</dt><dd>{{ pct(study.plan.max_exposure) }} / {{ study.plan.research_multiple }}x</dd></div>
          <div><dt>趋势 / 仓位管理</dt><dd>{{ study.plan.trend_signal === 'multi_horizon' ? '多周期' : '双周期' }} / {{ study.plan.exposure_policy === 'volatility_target' ? '动态减仓' : '固定仓位' }}</dd></div>
          <div><dt>引擎 / 行情快照数</dt><dd>{{ study.engine }} / {{ study.receipt_count }}</dd></div></dl>
        <div class="observation-tools"><button v-if="!confirming" :disabled="busy" @click="confirming = true"><Archive :size="16" />归档本次观察</button>
          <template v-else><span>归档后停止更新，完整记录保留。</span><button :disabled="busy" @click="act('archive')">确认归档</button><button @click="confirming = false">取消</button></template></div>
      </details>
    </template>
    <p class="small-note observation-note">自动观察在工作日收盘后运行，本机服务需保持开启；长任务可能延后更新。仅按已保存指令模拟之后的开盘成交，含手续费、滑点及借款利息，不代表实时撮合。收益含持仓浮动盈亏，未扣未来平仓费；离线期间不补造交易决策。</p>
    <details v-if="data.archives.length" class="section"><summary>已归档观察 · {{ data.archives.length }}</summary>
      <div class="table-scroll"><table><thead><tr><th>开始时间</th><th>计划</th><th>收益</th><th>观察日</th><th>记录</th></tr></thead><tbody>
        <tr v-for="item in data.archives" :key="item.id"><td>{{ stamp(item.created_at) }}</td><td>{{ signalName(item.plan) }} · {{ item.plan.research_multiple }}x</td><td>{{ pct(item.books.strategy?.return_pct) }}</td><td>{{ item.books.strategy?.observed_sessions || 0 }}</td><td><button class="icon-button" title="导出归档记录" aria-label="导出归档记录" @click="download(item.id)"><Download :size="16" /></button></td></tr>
      </tbody></table></div>
    </details>
  </section>
</template>

<style scoped>
.observation-tools { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin: 18px 0; }
.observation-tools > span { flex: 1; min-width: 180px; color: #687880; }
.observation-note { margin-top: 18px; }
@media (max-width: 600px) { .validation-plan { flex-wrap: wrap; } }
</style>
