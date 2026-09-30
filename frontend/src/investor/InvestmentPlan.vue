<script setup lang="ts">
import { computed, ref, watch } from "vue";
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
  ChevronRight,
  LockKeyhole,
} from "@lucide/vue";
import { clone, money, pct } from "../api";
import PortfolioChart from "../components/PortfolioChart.vue";
import { useInvestor, intradayNames } from "./workspace";
const {
  tabs,
  page,
  state,
  draft,
  report,
  reportError,
  reportMatchesPlan,
  auditData,
  saved,
  error,
  toast,
  busy,
  connected,
  loading,
  draftRevision,
  profileSlot,
  profileDrafts,
  selectProfileSlot,
  signalName,
  hasProfileEdits,
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
  comparisonFamily,
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
const parametersOpen = ref(false);
const editingProfile = ref(false);
const planSection = ref<"basic" | "advanced">("basic");
const lastMachine = ref("ml");
const profileOptions = [
  { key: "conservative", label: "保守" },
  { key: "balanced", label: "均衡" },
  { key: "aggressive", label: "激进" },
  { key: "custom", label: "自定义" },
];
const groups = computed(() => Object.keys(state.value?.profiles?.balanced?.allocation || {}));
const allocated = computed(() => Object.values(draft.value?.allocation || {}).reduce(
  (total: number, part: any) => total + Number(part), 0));
const t0Choices = computed(() => (draft.value?.symbols || []).filter(
  (symbol: string) => state.value?.catalog?.[symbol]?.t0));
const executionChanged = computed(() => !!state.value?.started_at && !!draft.value &&
  ["research_interval", "research_multiple", "research_symbol", "execution_policy", "allocation_policy"].some(
    (key) => draft.value[key] !== state.value.settings.plan[key]));
const reportMatchesPeriod = computed(() => reportMatchesPlan.value && !!draft.value &&
  Number(report.value.start.slice(0, 4)) === Number(draft.value.validation_start_year) &&
  Number(report.value.end.slice(0, 4)) === Number(draft.value.validation_end_year));
const machineSelected = computed(() => !["rotation", "hold"].includes(draft.value.method));
const fixedRecipe = computed(() => draft.value?.method === 'ml' && draft.value?.model_recipe !== 'legacy');
const activeSpec = computed(() => fixedRecipe.value ? { ...state.value?.model_recipes?.[draft.value.model_recipe],
  ...Object.fromEntries(Object.entries(draft.value.model_tuning || {}).filter(([,value]) => value != null)) } : null);
const fixedProfile = computed(() => profileSlot.value !== 'custom' && !editingProfile.value);
const matchesRecommendation = computed(() => Object.entries(state.value?.profiles?.[profileSlot.value] || {}).length > 0 &&
  Object.entries(state.value.profiles[profileSlot.value]).every(([key, value]) => key === 'label' || JSON.stringify(draft.value[key]) === JSON.stringify(value)));
const presetEvidence = computed(() => matchesRecommendation.value ? state.value?.profile_evidence?.[profileSlot.value] : null);
const profileName = computed(() => profileOptions.find(item => item.key === profileSlot.value)?.label || '自定义');
const study = computed(() => state.value?.profile_algorithm_study?.profiles?.[profileSlot.value]);
const modelChoice = computed({
  get: () => activeSpec.value ? (activeSpec.value.model === 'cnn' ? 'temporal' : activeSpec.value.model) : draft.value.method,
  set: (choice: string) => {
    const recipes: Record<string, string> = { hgb: 'hgb20_regularized', ridge: 'ridge20', extra: 'extra20', xgb: 'xgb20', temporal: 'cnn20' };
    if (recipes[choice]) {
      draft.value.method = 'ml'; draft.value.model_recipe = recipes[choice]; chooseRecipe();
    } else { draft.value.method = choice; draft.value.model_recipe = 'legacy'; draft.value.model_tuning = null; }
  },
});
const allocationNames: Record<string, string> = { legacy: '原分层配置', defensive_v1: '防守核心 + 模型调整', balanced_v1: '分散增长 + 模型调整' };
const coreCompatible = computed(() => draft.value?.research_interval === 'daily' && draft.value?.research_multiple === 1
  && draft.value?.execution_policy === 'cost_aware' && draft.value?.exposure_policy === 'volatility_target'
  && draft.value?.trend_signal === 'multi_horizon' && ['ml', 'rotation', 'hold'].includes(draft.value?.method));
watch(coreCompatible, (compatible) => {
  if (!compatible && draft.value?.allocation_policy !== 'legacy') {
    draft.value.allocation_policy = 'legacy';
    draft.value.profile = 'custom';
    notify('当前执行条件不支持核心组合，已切回原分层配置。');
  }
});
const presetMetrics = [
  { key: 'cagr', label: '全程净年化' },
  { key: 'drawdown', label: '最大回撤' },
  { key: 'worst_year', label: '最差完整年' },
  { key: 'later_cagr', label: '2023–2025 净年化' },
  { key: 'recent_return', label: '2026 独立起跑' },
  { key: 'stress_cagr', label: '成本加倍净年化' },
];
watch(fixedProfile, (fixed) => {
  if (fixed) { planSection.value = 'basic'; parametersOpen.value = false; }
});
function chooseRecipe() {
  draft.value.model_tuning = null;
  const spec = state.value?.model_recipes?.[draft.value.model_recipe];
  if (spec && draft.value.method === 'ml') {
    Object.assign(draft.value, { training_years: spec.years, horizon: spec.horizon, min_samples: 800,
      model_iterations: spec.iterations, model_leaves: spec.leaves, model_learning_rate: .04 });
    if (spec.model === 'cnn') Object.assign(draft.value, { cnn_window: spec.window, cnn_epochs: spec.epochs });
  }
}
const pairKeys = computed(() => comparisonFamily.value === "cnn" ? ["cnn_class", "cnn"] : ["ml_class", "ml"]);
const sameComparisonInputs = computed(() =>
  reportMatchesPeriod.value && !!state.value && !!draft.value &&
  Object.entries(draft.value).every(([key, value]) =>
    ["method", "name", "auto_update", "validation_start_year", "validation_end_year"].includes(key) ||
    JSON.stringify(value) === JSON.stringify(state.value.settings.plan[key])),
);
watch(() => draft.value?.method, (method) => {
  if (method && !["rotation", "hold"].includes(method)) {
    lastMachine.value = method;
    comparisonFamily.value = method.startsWith("cnn") ? "cnn" : "ml";
  }
});
watch([() => draft.value?.symbols?.join(","), () => draft.value?.research_interval], () => {
  if (!draft.value || draft.value.research_interval === "daily" || draft.value.symbols.includes(draft.value.research_symbol)) return;
  if (t0Choices.value.length) draft.value.research_symbol = t0Choices.value[0];
}, { immediate: true });
function selectMachine() {
  draft.value.method = lastMachine.value;
}
function selectProfile(key: string) {
  selectProfileSlot(key);
  editingProfile.value = key === 'custom';
  parametersOpen.value = false;
  planSection.value = 'basic';
}
function restoreRecommendation() {
  const key = profileSlot.value;
  const preset = state.value.profiles[key];
  if (!preset) return;
  if (!window.confirm(`将${profileName.value}恢复为推荐参数？保存后生效，其他档位不变。`)) return;
  for (const [field, value] of Object.entries(preset))
    if (field !== 'label' && !state.value.profile_library.shared_fields.includes(field)) draft.value[field] = clone(value);
  draft.value.preset_version = 1;
  draft.value.model_tuning = null;
  draft.value.profile = key;
  planSection.value = "basic";
  editingProfile.value = false;
}
function changeAllocation(group: string, event: Event) {
  const amount = Number((event.target as HTMLInputElement).value);
  if (!Number.isFinite(amount)) return;
  const share = Math.max(0, Math.min(100, amount)) / 100;
  const others = groups.value.filter((item) => item !== group);
  const previous = draft.value.allocation;
  const otherTotal = others.reduce((total, item) => total + Number(previous[item] || 0), 0);
  const next = { ...previous, [group]: share };
  for (const item of others)
    next[item] = (1 - share) * (otherTotal ? Number(previous[item] || 0) / otherTotal : 1 / others.length);
  draft.value.allocation = next;
  draft.value.profile = "custom";
}
</script>
<template>
  <section>
    <div class="page-title">
      <div>
        <p class="eyebrow">一个账户 · 各档独立保存</p>
        <h1>投资计划</h1>
        <p class="subtitle">
          {{
            dirty
              ? "有未保存的修改"
              : `已保存 · 版本 ${state.settings.revision}`
          }}
        </p>
      </div>
      <div class="actions">
        <button :disabled="!dirty || busy" @click="restoreDraft()">
          撤销草稿</button
        ><button class="primary" :disabled="busy || !dirty" @click="save">
          <Save :size="16" />保存并使用{{ profileName }}
        </button>
      </div>
    </div>
    <div class="plan-status" :class="{ warning: dirty || draft.research_interval !== 'daily' || !reportMatchesPeriod || report?.comparison_reused }" role="status">
      <div>
        <strong>{{ dirty ? '草稿尚未保存' : draft.research_interval !== 'daily' ? '盘中计划可进行短样本验证' : reportError ? '历史验证状态待确认' : !reportMatchesPeriod ? '这份计划尚未按所选年份验证' : report.comparison_reused ? '当前模型还需补跑完整验证' : '当前计划已有历史验证' }}</strong>
        <p>{{ dirty ? `已有结果只对应已保存的版本 ${state.settings.revision}，不代表当前草稿。` : draft.research_interval !== 'daily' ? `版本 ${state.settings.revision} · ${name(draft.research_symbol)} · ${draft.research_multiple}x · 模拟将使用盘中线` : reportError ? '暂未确认匹配的结果，读取失败不会删除已保存的验证。' : reportMatchesPeriod ? `版本 ${state.settings.revision} · ${report.start} 至 ${report.end} · ${report.comparison_reused ? '同口径对照可看，当前方案成本压力待验证' : '同参数验证'}` : '请到验证页选择年份并运行。' }}</p>
      </div>
      <button v-if="!dirty" type="button" @click="navigate('validation')">{{ draft.research_interval !== 'daily' || !reportMatchesPeriod || report.comparison_reused ? '去验证' : '查看验证' }} <ChevronRight :size="15" /></button>
    </div>
    <div v-if="reportError" class="notice warning" role="status">
      {{ reportError }} <button type="button" @click="refresh()"><RefreshCw :size="15" />重试读取结果</button>
    </div>
    <div v-if="executionChanged" class="notice warning" role="status">更改执行规则或组合政策需要先归档当前模拟账户。旧成交会保留在归档中。<button type="button" class="link-button" @click="showReset = true">归档当前模拟</button></div>
    <div v-if="!fixedProfile" class="validation-modes plan-subnav" role="tablist" aria-label="投资计划设置层级">
      <button role="tab" :aria-selected="planSection === 'basic'" :class="{ selected: planSection === 'basic' }" @click="planSection = 'basic'">计划概览</button>
      <button role="tab" :aria-selected="planSection === 'advanced'" :class="{ selected: planSection === 'advanced' }" @click="planSection = 'advanced'">详细设置</button>
    </div>
    <div class="plan-layout">
      <div>
        <section class="section">
          <h2>资金与风险</h2>
          <div class="risk-profile" role="group" aria-label="风险方案">
            <button v-for="option in profileOptions" :key="option.key" type="button"
              :class="{ selected: profileSlot === option.key }"
              :aria-pressed="profileSlot === option.key" @click="selectProfile(option.key)">
              {{ option.label }}
            </button>
          </div>
          <p v-if="profileOptions.some(option => hasProfileEdits(option.key))" class="small-note">未保存档位：{{ profileOptions.filter(option => hasProfileEdits(option.key)).map(option => option.label).join('、') }}</p>
          <div class="form-grid compact-fields">
            <label
              >模拟起始资金 / 元<input
                type="number"
                v-model.number="draft.starting_cash"
                min="1000"
                :disabled="!!state.started_at"
            /></label>
          </div>
          <button v-if="!fixedProfile" type="button" class="link-button" @click="planSection = 'advanced'">查看投入上限与风控细节 <ChevronRight :size="15" /></button>
        </section>
        <section v-if="fixedProfile" class="section" aria-label="固定方案摘要">
          <div class="section-heading"><h2>{{ profileName }}方案</h2><button type="button" @click="editingProfile = true; planSection = 'basic'"><SlidersHorizontal :size="16" />调整本档</button></div>
          <p>{{ signalName(draft) }} · {{ matchesRecommendation ? '推荐参数' : '独立保存参数' }}</p>
          <p v-if="draft.allocation_policy === 'defensive_v1'">防守优先。股票与黄金的目标投入合计不超过净资产的35%，其余可配置国债或保留现金。</p>
          <p v-else-if="draft.allocation_policy === 'balanced_v1'">分散增长。预测与历史趋势共同调整配置，接受阶段性亏损。</p>
          <dl class="preset-facts">
            <dt>组合政策</dt><dd>{{ allocationNames[draft.allocation_policy || 'legacy'] }}</dd>
            <dt>执行</dt><dd>{{ draft.research_interval === 'daily' ? '日线' : draft.research_interval }} · {{ ({daily: '每日', weekly: '每周', monthly: '每月'} as Record<string,string>)[draft.rebalance] }}调仓 · {{ draft.research_multiple }}x{{ draft.research_multiple === 1 ? '（无借款）' : '（仓位上限倍数）' }}</dd>
            <dt>波动预算</dt><dd>{{ pct(draft.target_volatility) }}（非收益目标）</dd>
            <template v-if="presetEvidence?.after?.volatility != null"><dt>历史实际波动</dt><dd>{{ pct(presetEvidence.after.volatility) }}</dd></template>
            <dt>投入上限</dt><dd>{{ pct(draft.max_exposure) }} · 最多 {{ draft.holdings }} 只</dd>
            <dt>投资范围</dt><dd>{{ draft.symbols.map(name).join('、') }}</dd>
            <dt>回撤暂停线</dt><dd>{{ pct(draft.drawdown_pause) }}</dd>
          </dl>
          <p v-if="!matchesRecommendation" class="small-note">已偏离推荐方案，推荐方案的历史表现不代表当前参数。</p>
          <template v-if="presetEvidence">
            <p class="small-note">通过本次历史筛选，不保证未来盈利。回撤暂停线不代表亏损上限。</p>
            <details class="preset-evidence">
              <summary>{{ profileName }}优化验证 · 新旧对照</summary>
              <p class="small-note">{{ presetEvidence.start }} 至 {{ presetEvidence.end }} · 起始 {{ money(presetEvidence.starting_cash) }} 元 · {{ presetEvidence.candidate_count }} 组配置 / {{ presetEvidence.replays }} 次回放</p>
              <div class="preset-comparison" role="table" :aria-label="profileName + '优化验证对照'">
                <div role="row"><span role="columnheader">指标</span><span role="columnheader">旧{{ profileName }}</span><span role="columnheader">新{{ profileName }}</span></div>
                <div v-for="metric in presetMetrics" :key="metric.key" role="row">
                  <span role="cell">{{ metric.label }}</span>
                  <span role="cell">{{ pct(presetEvidence.before[metric.key]) }}</span>
                  <strong role="cell">{{ pct(presetEvidence.after[metric.key]) }}</strong>
                </div>
              </div>
              <p class="small-note">前段选择参数，锁定后检查后段。新方案并非在每个区间都更好；这些年份曾用于研究，并非全新样本外证明。2023–2025 与 2026 分别独立起跑；净年化与区间收益计入期末卖出成本，最差年按连续账户年末净值计算。</p>
            </details>
          </template>
          <label class="check-field"><input type="checkbox" v-model="draft.auto_update" /><span>工作日 16:00 后自动更新模拟</span></label>
        </section>
        <div v-if="!fixedProfile && profileSlot !== 'custom'" class="section-heading profile-edit-heading">
          <strong>正在调整{{ profileName }} · 独立保存</strong>
          <div class="actions"><button type="button" @click="restoreRecommendation"><RefreshCw :size="15" />恢复本档推荐</button><button type="button" @click="editingProfile = false">收起参数</button></div>
        </div>
        <details v-if="study" class="section profile-study">
          <summary>{{ profileName }} · 算法比较</summary>
          <p>{{ study.conclusion }}</p>
          <p class="small-note">2020–2022选择参数，2023–2024比较算法；再检查2025、2026和完整区间。历史曾被研究，不代表未来盈利。</p>
          <div class="table-scroll"><table :aria-label="profileName + '算法选择对照'"><thead><tr><th>算法 / 配方</th><th>开发净年化</th><th>选择期净年化</th><th>选择期回撤</th><th>结论</th></tr></thead>
          <tbody><tr v-for="row in study.rows" :key="row.key"><td>{{ row.label }}</td><td>{{ pct(row.development_cagr) }}</td><td>{{ pct(row.selection_cagr) }}</td><td>{{ pct(row.selection_drawdown) }}</td><td>{{ row.verdict }}</td></tr></tbody></table></div>
          <details><summary>参数复测明细</summary><div class="table-scroll"><table :aria-label="profileName + '参数复测'"><thead><tr><th>配方</th><th>开发净年化</th><th>最大回撤</th><th>采纳模型次数</th></tr></thead><tbody><tr v-for="row in study.parameter_trials" :key="row.label"><td>{{ row.label }}</td><td>{{ pct(row.cagr) }}</td><td>{{ pct(row.drawdown) }}</td><td>{{ row.model_decisions }}</td></tr></tbody></table></div></details>
        </details>
        <section v-if="!fixedProfile && planSection === 'basic'" class="section">
          <h2>{{ draft.research_interval === 'daily' ? '组合方案' : '盘中规则' }}</h2>
          <div v-if="draft.research_interval === 'daily'" class="method-options">
            <div class="strategy-option" :class="{ checked: draft.method === 'rotation' }">
              <label><input type="radio" name="strategy-family" value="rotation" :checked="draft.method === 'rotation'" @change="draft.method = 'rotation'" />
                <span><strong>趋势组合</strong><small>按价格趋势与分散仓位运行</small></span></label>
            </div>
            <div class="strategy-option" :class="{ checked: draft.method === 'hold' }">
              <label><input type="radio" name="strategy-family" value="hold" :checked="draft.method === 'hold'" @change="draft.method = 'hold'" />
                <span><strong>按板块偏好持有</strong><small>首次买入后不择时交易，成本较低</small></span></label>
            </div>
            <div class="strategy-option" :class="{ checked: machineSelected }">
              <label><input type="radio" name="strategy-family" value="machine" :checked="machineSelected" @change="selectMachine" />
                <span><strong>机器学习组合</strong><small>分类或回归信号，交易规则相同</small></span></label>
              <div v-if="machineSelected" class="model-picker">
                <select aria-label="机器学习模型" v-model="modelChoice">
                  <option value="hgb">HistGradientBoosting</option><option value="ridge">Ridge 线性回归</option><option value="extra">Extra Trees 树集成</option><option value="xgb">XGBoost</option><option value="temporal">1D-CNN 序列回归</option>
                  <option value="ml_class">梯度树 · 涨跌分类</option>
                  <option value="ml">梯度树 · 自定义参数</option>
                  <option value="cnn_class">1D-CNN · 涨跌分类</option>
                  <option value="cnn">1D-CNN · 净收益回归</option>
                </select>
                <button type="button" title="打开模型参数设置" @click="parametersOpen = true">
                  <SlidersHorizontal :size="16" /><span>参数</span>
                </button>
              </div>
            </div>
          </div>
          <div v-if="draft.research_interval === 'daily'" class="form-grid">
            <label v-if="draft.method !== 'hold'">趋势过滤<select v-model="draft.trend_signal" aria-label="趋势过滤">
              <option value="legacy">原始双周期</option>
              <option value="multi_horizon">多周期共识 · 实验</option>
            </select></label>
            <label
              >调仓频率<select v-model="draft.rebalance" :disabled="draft.method === 'hold'">
                <option value="daily">每日</option>
                <option value="weekly">每周</option>
                <option value="monthly">每月</option>
              </select><small>{{ draft.method === 'hold' ? '持有方案只在启用时买入，不定期调仓。' : '每日更频繁，不保证更高收益；服务需持续运行并启用自动更新，离线期间不会补造信号。' }}</small></label
            >
          </div>
          <div v-if="draft.research_interval !== 'daily'" class="form-grid">
            <label>盘中策略<select v-model="draft.intraday_strategy" aria-label="盘中策略">
              <option value="cost_aware">成本过滤 + 波动限仓</option>
              <option value="breakout">通道突破 · 实验</option>
              <option value="ml_edge">盘中回归 · 实验</option>
              <option value="legacy">原始均线 · 对照用</option>
            </select></label>
          </div>
          <label class="check-field"
            ><input type="checkbox" v-model="draft.auto_update" /><span
            >{{ draft.research_interval !== 'daily' ? '交易时段每根 K 线完成后自动更新模拟' : '工作日 16:00 后自动更新模拟' }}</span
            ></label
          ><small
            >仅更新模拟账户，不会向券商下单；节假日无新行情时不重复成交。</small
          >
        </section>
        <section v-if="!fixedProfile && planSection === 'basic'" class="section">
          <h2>投资范围</h2>
          <div class="asset-options">
            <label v-for="(v, s) in state.catalog" :key="s"
              ><input type="checkbox" v-model="draft.symbols" :value="s" /><span
                ><strong>{{ (v as any).name }}</strong
                ><small>{{ (v as any).group }} · {{ s }}</small></span
              ></label
            >
          </div>
          <p class="small-note">已选 {{ draft.symbols.length }} 只 ETF。板块比例可在详细设置调整。</p>
        </section>
        <section v-if="!fixedProfile && planSection === 'basic'" class="section">
          <h2>执行模式</h2>
          <p class="small-note">保存后用于后续模拟更新；验证使用相同粒度和倍数。切换粒度或倍数前需归档当前模拟。实盘未连接时不会下单。</p>
          <p class="small-note">倍数放大风险档的目标仓位，不代表账户总资产一定达到 2 倍或 3 倍；若目标仓位超过自有资金，模拟才会出现借款。</p>
          <div class="form-grid">
            <label>行情时间粒度<select v-model="draft.research_interval">
              <option value="daily">日线 · 所选组合策略</option>
              <option value="60m">60 分钟 · T+0 盘中策略</option>
              <option value="30m">30 分钟 · T+0 盘中策略</option>
            </select></label>
            <label>目标仓位倍数<select v-model.number="draft.research_multiple">
              <option :value="1">1x · 无借款</option>
              <option :value="2">2x · 放大目标仓位</option>
              <option :value="3">3x · 放大目标仓位</option>
            </select></label>
            <label v-if="draft.research_interval === 'daily' && draft.method !== 'hold'">仓位管理<select v-model="draft.exposure_policy" aria-label="仓位管理">
              <option value="fixed">固定放大</option>
              <option value="volatility_target">动态减仓 · 实验</option>
            </select></label>
            <label v-if="draft.research_interval !== 'daily'">盘中交易 ETF<select v-model="draft.research_symbol" aria-label="盘中交易 ETF">
              <option v-for="symbol in t0Choices" :key="symbol" :value="symbol">{{ name(symbol) }}</option>
            </select></label>
          </div>
          <p v-if="draft.research_interval !== 'daily'" class="notice warning">盘中模式使用独立的 T+0 信号，不运行日线组合。当前只有近期短样本，不具备稳定盈利依据；模拟只在指令生成后的行情中成交。</p>
          <p v-if="draft.research_interval !== 'daily' && !t0Choices.length" class="notice warning">投资范围没有支持盘中模拟的 ETF，请先选入黄金、国债或纳指 ETF。</p>
          <button type="button" class="link-button" @click="navigate('validation')">去验证当前计划 <ChevronRight :size="15" /></button>
        </section>
        <section v-if="planSection === 'advanced'" class="section">
          <h2>资金与风险细节</h2>
          <p class="small-note">修改这些值后风险档会显示为“自定义”，保存后需要重新验证。</p>
          <div class="form-grid">
            <label v-if="draft.research_interval === 'daily' && draft.method !== 'hold'">组合政策<select v-model="draft.allocation_policy" aria-label="组合政策" @change="draft.profile = 'custom'">
              <option value="legacy">原分层配置</option>
              <option value="defensive_v1" :disabled="!coreCompatible">防守核心 + 模型调整</option>
              <option value="balanced_v1" :disabled="!coreCompatible">分散增长 + 模型调整</option>
            </select></label>
            <label v-if="draft.allocation_policy !== 'legacy' && draft.method !== 'hold'">核心配置约束强度<input aria-label="核心配置约束强度" type="number" min="0" max="5" step="0.1" v-model.number="draft.core_anchor" /><small>越高越接近分散配置，越低越依赖预测；不强制满仓。</small></label>
            <label v-if="draft.allocation_policy !== 'legacy' && draft.method !== 'hold'">风险惩罚系数<input aria-label="风险惩罚系数" type="number" min="0.1" max="30" step="0.1" v-model.number="draft.risk_aversion" /><small>越高越偏向降低组合波动。</small></label>
            <label v-if="draft.research_interval === 'daily' && draft.exposure_policy === 'volatility_target' && draft.method !== 'hold'">目标年化波动 / %<input aria-label="目标年化波动" type="number" min="3" max="40" step="1" :value="draft.target_volatility * 100" @input="ratio('target_volatility', $event)" /></label>
            <label v-if="draft.research_interval === 'daily'">执行规则<select v-model="draft.execution_policy" aria-label="执行规则">
              <option value="corrected">标准 · 完整建仓</option>
              <option value="cost_aware" :disabled="draft.method === 'hold'">成本约束仓位 · 实验</option>
              <option value="legacy">旧版 · 复现历史结果</option>
            </select></label>
            <label>总投入上限 / %<input type="number" min="1" max="100" :value="draft.max_exposure * 100" @input="ratio('max_exposure', $event)" /></label>
            <label>单只 ETF 目标上限 / %<input type="number" min="1" max="100" :value="draft.max_weight * 100" @input="ratio('max_weight', $event)" /></label>
            <label>账户回撤暂停线 / %<input type="number" min="2" max="50" :value="draft.drawdown_pause * 100" @input="ratio('drawdown_pause', $event)" :disabled="draft.method === 'hold' && draft.research_interval === 'daily'" /></label>
            <label v-if="draft.research_interval === 'daily'">最多持有 / 只<input type="number" min="1" max="4" v-model.number="draft.holdings" @change="draft.profile = 'custom'" :disabled="draft.method === 'hold'" /></label>
          </div>
        </section>
        <section v-if="planSection === 'advanced' && draft.research_interval === 'daily'" class="section">
          <h2>板块分配偏好</h2>
          <div class="sector-allocation">
            <div class="section-heading">
              <span>可投入资金的 {{ pct(allocated) }}</span>
            </div>
            <div class="allocation-inputs">
              <label v-for="group in groups" :key="group">
                <span>{{ group }}<small>参考 {{ money(draft.starting_cash * draft.max_exposure * draft.allocation[group]) }} 元</small></span>
                <span class="percent-input"><input type="number" min="0" max="100" step="0.1"
                  :value="(draft.allocation[group] * 100).toFixed(1)" @change="changeAllocation(group, $event)" />%</span>
              </label>
            </div>
            <p class="small-note">参考金额不是固定承诺；未入选板块的份额可分给其他合格板块，受单只上限约束。</p>
          </div>
        </section>
        <section v-if="planSection === 'advanced' && draft.research_interval !== 'daily'" class="section">
          <h2>盘中信号参数</h2>
          <div class="form-grid">
            <label>成本门槛倍数<input type="number" min="1" max="5" step="0.1" v-model.number="draft.intraday_cost_buffer" :disabled="draft.intraday_strategy === 'legacy'" /></label>
            <label>波动风险预算 / %<input type="number" min="0.2" max="10" step="0.1" :value="draft.intraday_risk_budget * 100" @input="ratio('intraday_risk_budget', $event)" :disabled="draft.intraday_strategy === 'legacy'" /></label>
          </div>
          <div v-if="draft.intraday_strategy === 'ml_edge'" class="form-grid">
            <label>训练窗口 / 交易日<input type="number" min="20" max="750" v-model.number="draft.intraday_training_sessions" /></label>
            <label>最小已结束样本<input type="number" min="60" max="10000" v-model.number="draft.intraday_min_samples" /></label>
            <label>预测持有 / 交易小时<input type="number" min="1" max="16" v-model.number="draft.intraday_horizon_hours" /></label>
          </div>
          <p class="small-note">波动预算不是最大亏损保证。盘中回归只使用今天之前结束的标签；训练不足或隔离校验不优于零收益预测时不新增仓位。其他盘中策略的动量不是预期收益。</p>
        </section>
        <section v-if="planSection === 'advanced' && draft.research_multiple > 1" class="section">
          <h2>模拟融资参数</h2>
          <p class="small-note">日线与盘中模拟都会计入借款利息和维保减仓；真实利率和券商规则尚未接入。</p>
          <div class="form-grid">
            <label>假设年化融资成本 / %<input type="number" min="0" max="30" step="0.5" :value="draft.research_annual_rate * 100" @input="ratio('research_annual_rate', $event)" /></label>
            <label>假设维保线 / %<input type="number" min="0" max="80" step="1" :value="draft.research_maintenance_ratio * 100" @input="ratio('research_maintenance_ratio', $event)" /></label>
          </div>
        </section>
        <section v-if="planSection === 'advanced' && draft.research_interval === 'daily'" class="section">
          <div class="signal-compare">
            <div class="signal-compare-heading"><h3>同口径模型对照</h3><button class="link-button" @click="navigate('validation')">完整验证 <ChevronRight :size="15" /></button></div>
            <template v-if="sameComparisonInputs && pairKeys.every((key) => report.results[key])">
              <div class="signal-compare-row" v-for="key in pairKeys" :key="key">
                <span>{{ methodNames[key] }}</span>
                <strong :class="gainClass(report.results[key].return_pct)">{{ pct(report.results[key].return_pct) }}</strong>
                <small>回撤 {{ pct(report.results[key].max_drawdown) }}</small>
              </div>
              <small>同一行情、组合仓位与成交成本；分类使用旧版标签，不复用旧版账户规则。</small>
            </template>
            <p v-else class="small-note">当前计划尚无同口径验证结果。</p>
          </div>
        </section>
        <details v-if="planSection === 'advanced'" class="section">
          <summary>交易成本与执行细节</summary>
          <div class="form-grid">
            <label
              >佣金费率 / %<input
                type="number"
                step="0.001"
                :value="draft.commission_rate * 100"
                @input="ratio('commission_rate', $event)" /></label
            ><label
              >最低佣金 / 元<input
                type="number"
                min="0"
                max="100"
                v-model.number="draft.min_commission" /></label
            ><label
              >单边滑点 / 万分点<input
                type="number"
                min="0"
                max="100"
                v-model.number="draft.slippage_bps" /></label
            ><label
              >仓位偏离调仓 / %<input
                type="number"
                :value="draft.drift_band * 100"
                @input="ratio('drift_band', $event)" :disabled="draft.method === 'hold'" /></label
            ><label
              >冷静期 / 交易日<input
                type="number"
                min="1"
                max="120"
                v-model.number="draft.cooldown_sessions" @change="draft.profile = 'custom'" :disabled="draft.method === 'hold'" /></label
            ><label
              >参考成交量上限 / %<input
                type="number"
                step="0.1"
                :value="draft.participation * 100"
                @input="ratio('participation', $event)"
            /></label>
          </div>
        </details>
      </div>
      <aside class="plan-summary">
        <ShieldCheck :size="24" />
        <h2>{{ dirty ? "计划草稿" : "当前计划" }}</h2>
        <dl>
          <dt>方案</dt>
          <dd>{{ draft.research_interval === 'daily' ? signalName(draft) : intradayNames[draft.intraday_strategy] }}</dd>
          <dt>风险档</dt>
          <dd>{{ profileName }}</dd>
          <dt>模拟执行</dt>
          <dd>{{ draft.research_interval !== 'daily' ? draft.research_interval.replace('m', ' 分钟') : '日线' }} · {{ draft.research_multiple }}x</dd>
          <dt v-if="draft.research_interval === 'daily'">执行规则</dt>
          <dd v-if="draft.research_interval === 'daily'">{{ draft.allocation_policy !== 'legacy' && draft.method !== 'hold' ? allocationNames[draft.allocation_policy] : draft.execution_policy === 'cost_aware' && draft.method !== 'hold' ? '成本约束仓位 · 实验' : draft.execution_policy === 'legacy' ? '旧版复现' : '标准 · 完整建仓' }}</dd>
          <dt v-if="draft.research_interval === 'daily' && draft.method !== 'hold'">仓位管理</dt>
          <dd v-if="draft.research_interval === 'daily' && draft.method !== 'hold'">{{ draft.exposure_policy === 'volatility_target' ? '动态减仓 / 波动目标 ' + pct(draft.target_volatility) : '固定放大' }}</dd>
          <dt v-if="draft.research_interval !== 'daily'">盘中标的</dt>
          <dd v-if="draft.research_interval !== 'daily'">{{ name(draft.research_symbol) }}</dd>
          <dt>最多投入</dt>
          <dd>{{ money(draft.starting_cash * (draft.research_interval === 'daily' ? draft.max_exposure : Math.min(draft.max_exposure, draft.max_weight)) * draft.research_multiple) }} 元</dd>
          <dt v-if="planSection === 'advanced' && draft.research_interval === 'daily'">基础未投入比例</dt>
          <dd v-if="planSection === 'advanced' && draft.research_interval === 'daily'">{{ pct(1 - draft.max_exposure) }}</dd>
          <dt v-if="planSection === 'advanced'">单只上限</dt>
          <dd v-if="planSection === 'advanced'">{{ pct(draft.max_weight) }}</dd>
          <dt v-if="planSection === 'advanced'">回撤暂停线</dt>
          <dd v-if="planSection === 'advanced'">{{ draft.method === 'hold' && draft.research_interval === 'daily' ? '不适用' : pct(draft.drawdown_pause) }}</dd>
          <dt>调仓</dt>
          <dd>{{ draft.research_interval !== 'daily' ? '每根 K 线完成后检查' : draft.method === 'hold' ? '仅首次买入' : draft.rebalance === "daily" ? "每日" : draft.rebalance === "weekly" ? "每周" : "每月" }}</dd>
        </dl>
        <p class="small-note">
          回撤线是触发条件，不是损失保证。跳空、无法成交和数据延迟都可能扩大亏损。
        </p>
        <details class="plan-management">
          <summary>其他管理操作</summary>
          <a href="/?lab=1#strategy">独立研究工具 <ArrowUpRight :size="14" /></a>
          <small>旧模型和实验记录留在研究区，不改变当前计划。</small>
          <button class="danger-text" :disabled="!state.started_at" @click="showReset = true">归档并重新开始模拟</button>
        </details>
      </aside>
    </div>
    <div v-if="parametersOpen && machineSelected" class="modal-backdrop" @click.self="parametersOpen = false">
      <section class="dialog model-parameters" role="dialog" aria-modal="true" aria-label="模型参数设置" tabindex="-1" @keydown.esc="parametersOpen = false">
        <div class="dialog-heading">
          <div><p class="eyebrow">{{ profileName }} · 模型参数</p><h2>{{ signalName(draft) }}</h2></div>
          <button class="icon-button" title="关闭模型参数设置" aria-label="关闭模型参数设置" @click="parametersOpen = false"><X :size="18" /></button>
        </div>
        <div class="form-grid">
          <label v-if="draft.method === 'ml'" class="recipe-choice">模型配方<select v-model="draft.model_recipe" aria-label="模型配方" @change="chooseRecipe"><option value="legacy">基础梯度树 · 自定义参数</option><option v-for="(spec, key) in state.model_recipes" :key="key" :value="key">{{ spec.label }}</option></select></label>
          <details v-if="draft.model_tuning"><summary>已载入的研究参数</summary><dl><template v-for="(value, key) in draft.model_tuning" :key="key"><template v-if="value != null"><dt>{{ key }}</dt><dd>{{ value }}</dd></template></template></dl></details>
          <label>训练窗口 / 年<input type="number" min="1" max="10" v-model.number="draft.training_years" :disabled="fixedRecipe" /></label>
          <label>预测持有期 / 交易日<input type="number" min="1" max="20" v-model.number="draft.horizon" :disabled="fixedRecipe" /></label>
          <label>最小训练样本<input type="number" min="100" max="100000" v-model.number="draft.min_samples" :disabled="fixedRecipe" /></label>
          <label v-if="draft.method.endsWith('_class')">上涨概率门槛 / %
            <input type="number" min="5" max="95" step="1" :value="draft.classification_threshold * 100" @input="ratio('classification_threshold', $event)" />
          </label>
          <template v-if="!activeSpec && draft.method.startsWith('ml')">
            <label>迭代次数<input type="number" min="20" max="500" v-model.number="draft.model_iterations" :disabled="fixedRecipe" /></label>
            <label>每棵树最多叶子<input type="number" min="3" max="31" v-model.number="draft.model_leaves" :disabled="fixedRecipe" /></label>
            <label>学习率<input type="number" step="0.005" min="0.005" max="0.2" v-model.number="draft.model_learning_rate" :disabled="fixedRecipe" /></label>
          </template>
          <template v-else-if="activeSpec">
            <label v-if="['hgb','extra','xgb'].includes(activeSpec.model)">树 / 迭代次数<input type="number" :value="activeSpec.iterations" disabled /></label>
            <label v-if="activeSpec.model === 'hgb'">每棵树最多叶子<input type="number" :value="activeSpec.leaves" disabled /></label>
            <label v-if="['hgb','extra'].includes(activeSpec.model)">叶节点最小样本<input type="number" :value="activeSpec.leaf_samples" disabled /></label>
            <label v-if="['hgb','ridge','xgb'].includes(activeSpec.model)">正则化强度<input type="number" :value="activeSpec.regularization" disabled /></label>
            <template v-if="activeSpec.model === 'cnn'"><label>观察长度 / 交易日<input type="number" :value="activeSpec.window" disabled /></label><label>训练轮数<input type="number" :value="activeSpec.epochs" disabled /></label></template>
          </template>
          <template v-else>
            <label>观察长度 / 交易日<input type="number" min="10" max="60" v-model.number="draft.cnn_window" /></label>
            <label>训练轮数<input type="number" min="4" max="30" v-model.number="draft.cnn_epochs" /></label>
          </template>
        </div>
        <div class="dialog-actions"><button class="primary" @click="parametersOpen = false">完成</button></div>
      </section>
    </div>
  </section>
</template>
<style scoped>
.preset-evidence { margin-block: 16px; border-top: 1px solid var(--border, #dce2e7); padding-top: 12px; }
.preset-evidence summary { cursor: pointer; font-weight: 600; }
.preset-comparison [role='row'] { display: grid; grid-template-columns: minmax(0, 1.65fr) repeat(2, minmax(0, 1fr)); gap: 8px; padding: 10px 0; border-bottom: 1px solid var(--border, #dce2e7); font-size: 13px; }
.preset-comparison [role='row'] > :not(:first-child) { text-align: right; font-variant-numeric: tabular-nums; }
.preset-comparison [role='columnheader'] { font-weight: 600; }
</style>

<style scoped>
.recipe-choice { grid-column: 1 / -1; }
.preset-facts { display: grid; grid-template-columns: minmax(80px, 110px) minmax(0, 1fr); gap: 12px; }
.preset-facts dd { margin: 0; overflow-wrap: anywhere; }
.profile-edit-heading { flex-wrap: wrap; gap: 12px; }
.profile-study { min-width: 0; }
.profile-study table { min-width: 580px; }
</style>
