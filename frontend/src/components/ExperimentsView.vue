<script setup lang="ts">
import { computed, defineAsyncComponent, inject, ref, watch } from "vue";
import {
  Play,
  Square,
  History,
  X,
  ArrowLeft,
  ArrowRight,
  FolderInput,
  FlaskConical,
} from "@lucide/vue";
import { api, dateTime, statusName, clone } from "../api";
import { modelName, taskName, taskStyle } from "../workspace";
import EvidencePanel from "./EvidencePanel.vue";
const ResultPanel = defineAsyncComponent(() => import("./ResultPanel.vue"));
const ResearchReport = defineAsyncComponent(
  () => import("./ResearchReport.vue"),
);
const LearningResults = defineAsyncComponent(
  () => import("./LearningPanel.vue"),
);
const w = inject<any>("workspace"),
  years = ref([2021, 2022, 2023, 2024, 2025]),
  searchYears = ref([2021, 2022, 2023, 2024]),
  checks = ref([2025]),
  learnProfiles = ref([w.draft.profile]),
  trials = ref(3),
  continuous = ref(false),
  year = ref(2025),
  period = ref("year");
const weights = ref({ specialized: 50, fallback: 20, composite: 30 }),
  history = ref(false),
  items = ref<any[]>([]),
  total = ref(0),
  page = ref(0),
  filter = ref(""),
  detail = ref<any>(null),
  detailYear = ref<number | null>(null);
const availableYears = Array.from(
  { length: Math.min(12, new Date().getFullYear() - 2020) },
  (_, i) => new Date().getFullYear() - 1 - i,
);
const source = computed(() =>
  w.experimentSource === "draft" ? w.draft : w.active,
);
const running = computed(
  () => w.selected && ["running", "queued"].includes(w.selected.status),
);
const result = computed(() => w.selected?.result);
const evidence = computed(() =>
  result.value?.scorecard
    ? {
        matched: true,
        scorecard: result.value.scorecard,
        per_year: result.value.per_year,
        origin: "每年独立起算；相同资金、标的及成本",
      }
    : null,
);
async function run() {
  const mode = w.experimentMode;
  if (mode === "learn")
    await w.submit("learn", {
      years: searchYears.value,
      check_years: checks.value,
      profiles: learnProfiles.value,
      trials: trials.value,
      continuous: continuous.value,
      weights: weights.value,
    });
  else if (mode === "evaluate")
    await w.submit("evaluate", { years: years.value });
  else
    await w.submit("backtest", {
      period: source.value.engine === "ai" ? "random14" : period.value,
      year: year.value,
    });
}
async function historyPage() {
  await w.act(async () => {
    const r = await api(
      `/jobs?limit=12&offset=${page.value * 12}${filter.value ? "&kind=" + filter.value : ""}`,
    );
    items.value = r.items;
    total.value = r.total;
  });
}
async function openHistory() {
  history.value = true;
  await historyPage();
}
async function openYear(y: number) {
  detailYear.value = y;
  await w.act(
    async () => (detail.value = await api(`/jobs/${w.selected.id}/years/${y}`)),
  );
}
function copyConfig() {
  w.draft = clone(w.selected.request.config);
  w.navigate("strategy");
  w.flash("已载入该实验的参数快照");
}
watch(
  () => w.selected?.id,
  () => {
    detail.value = null;
    detailYear.value = null;
  },
);
watch(
  () => source.value.engine,
  (engine) => {
    if (engine === "ai") w.experimentMode = "backtest";
    else if (engine !== "ml" && w.experimentMode === "learn")
      w.experimentMode = "evaluate";
  },
  { immediate: true },
);
watch(filter, () => {
  page.value = 0;
  void historyPage();
});
const formOpen = ref(!w.selected);
watch(
  () => w.selected?.id,
  (id) => {
    formOpen.value = !id;
  },
);
</script>
<template>
  <div class="page-title">
    <div>
      <span class="overline">EXPERIMENTS</span>
      <h1>实验中心</h1>
    </div>
    <div class="inline-actions">
      <button class="subtle" @click="openHistory">
        <History :size="16" />历史记录</button
      ><button
        v-if="w.selected && !formOpen"
        class="primary"
        @click="formOpen = true"
      >
        新实验<FlaskConical :size="16" />
      </button>
    </div>
  </div>
  <section v-if="formOpen || !w.selected" class="experiment-form">
    <div class="section-tabs">
      <button
        :class="{ active: w.experimentMode === 'evaluate' }"
        :disabled="source.engine === 'ai'"
        @click="w.experimentMode = 'evaluate'"
      >
        跨年评估</button
      ><button
        :class="{ active: w.experimentMode === 'backtest' }"
        @click="w.experimentMode = 'backtest'"
      >
        单段回测</button
      ><button
        :class="{ active: w.experimentMode === 'learn' }"
        :disabled="source.engine !== 'ml'"
        @click="w.experimentMode = 'learn'"
      >
        参数优化
      </button>
    </div>
    <div class="experiment-identity">
      <label class="field"
        ><span>使用方案</span
        ><select v-model="w.experimentSource">
          <option value="active">当前启用的方案</option>
          <option value="draft">方案页正在编辑的参数</option>
        </select></label
      >
      <div>
        <strong
          >{{ modelName(source) }} ·
          {{
            source.auto_profile && w.experimentMode !== "learn"
              ? source.profile_selection === "recent" ? "按近三个月模拟成绩选档" : "按价格走势选档"
              : w.catalog.profiles[source.profile]
          }}</strong
        ><small
          >{{ source.symbols.length }} 个标的 · ¥{{
            source.risk.starting_cash.toLocaleString()
          }}</small
        >
      </div>
    </div>
    <div v-if="w.experimentMode === 'evaluate'">
      <label class="field"><span>评估年份</span></label>
      <div class="year-options">
        <label v-for="y in availableYears"
          ><input type="checkbox" v-model="years" :value="y" />{{ y }}</label
        >
      </div>
      <p class="form-context">
        对比收益、最差年份和买入持有，每年从相同资金独立开始。
      </p>
    </div>
    <div v-if="w.experimentMode === 'backtest'" class="field-grid">
      <label class="field"
        ><span>时间范围</span
        ><select
          :value="source.engine === 'ai' ? 'random14' : period"
          :disabled="source.engine === 'ai'"
          @change="period = ($event.target as HTMLSelectElement).value"
        >
          <option value="year">完整自然年</option>
          <option value="random14">随机 14 个交易日</option>
        </select></label
      ><label v-if="period === 'year' && source.engine !== 'ai'" class="field"
        ><span>年份</span
        ><select v-model.number="year">
          <option v-for="y in availableYears" :value="y">{{ y }}</option>
        </select></label
      >
    </div>
    <template v-if="w.experimentMode === 'learn'"
      ><div class="field">
        <span>需要改进的档位</span>
        <div class="year-options">
          <label v-for="(name, key) in w.catalog.profiles"
            ><input type="checkbox" v-model="learnProfiles" :value="key" />{{
              name
            }}</label
          >
        </div>
      </div>
      <div class="learning-split">
        <div class="field">
          <span>用于选择参数的年份</span>
          <div class="year-options">
            <label v-for="y in availableYears"
              ><input
                type="checkbox"
                v-model="searchYears"
                :value="y"
                :disabled="checks.includes(y)"
              />{{ y }}</label
            >
          </div>
        </div>
        <div class="field">
          <span>仅验收，不参与排名</span>
          <div class="year-options">
            <label v-for="y in availableYears"
              ><input
                type="checkbox"
                v-model="checks"
                :value="y"
                :disabled="searchYears.includes(y)"
              />{{ y }}</label
            >
          </div>
        </div>
      </div>
      <details class="fold">
        <summary>搜索预算与评分</summary>
        <div class="field-grid inner">
          <label class="field"
            ><span>每轮每档候选数</span
            ><select v-model.number="trials">
              <option :value="3">3 · 快速</option>
              <option :value="6">6 · 常规</option>
              <option :value="12">12 · 深度</option>
            </select></label
          ><label class="check-field"
            ><input
              type="checkbox"
              v-model="continuous"
            />持续迭代直到手动停止</label
          ><label
            v-for="(label, key) in {
              specialized: '目标行情',
              fallback: '其他行情',
              composite: '所有年份',
            }"
            class="field"
            ><span>{{ label }}权重</span
            ><input
              type="number"
              v-model.number="weights[key]"
              min="0"
              step="5"
          /></label>
        </div>
        <p class="form-context">
          各档独立优化；学习时关闭自动切档。自定义档按全部年份评价，验收结果不参与排名。
        </p>
      </details></template
    >
    <div class="form-actions">
      <span>{{
        w.experimentMode === "learn"
          ? `${learnProfiles.length} 档 · ${searchYears.length} 个调参年份 · ${checks.length} 个验收年份`
          : "日线 · 下一交易日开盘成交 · 含手续费和滑点"
      }}</span
      ><button
        class="primary"
        :disabled="
          w.busy ||
          !w.access.write_allowed ||
          (w.experimentMode === 'evaluate' && !years.length) ||
          (w.experimentMode === 'learn' &&
            (!searchYears.length || !learnProfiles.length))
        "
        @click="run"
      >
        <Play :size="16" />{{
          w.experimentMode === "learn"
            ? "开始优化"
            : w.experimentMode === "evaluate"
              ? "开始评估"
              : "开始回测"
        }}
      </button>
    </div>
  </section>
  <section v-if="w.selected" class="experiment-result">
    <ResearchReport
      v-if="w.selected.kind === 'research' && result"
      :report="result"
      :catalog="w.catalog"
    />
    <div class="experiment-title">
      <div>
        <span class="badge">{{ statusName(w.selected.status) }}</span>
        <h2>
          {{ taskName(w.selected.kind) }} ·
          {{ w.selected.kind === "research" ? "联网分析" : modelName(w.selected.request.config) }}
        </h2>
        <p>
          {{ taskStyle(w.selected, w.catalog) }} ·
          {{ dateTime(w.selected.created) }}
        </p>
      </div>
      <div class="inline-actions">
        <button
          class="icon-btn"
          title="载入本次参数到方案页"
          @click="copyConfig"
        >
          <FolderInput :size="18" /></button
        ><button v-if="running" class="subtle" @click="w.control('stop')">
          <Square :size="14" />停止</button
        ><button
          v-else-if="
            ['failed', 'stopped', 'interrupted'].includes(w.selected.status)
          "
          class="subtle"
          @click="w.control('resume')"
        >
          <Play :size="14" />继续
        </button>
      </div>
    </div>
    <div v-if="running" class="job-progress">
      <span>{{ w.selected.message }}</span
      ><strong>{{ Math.round(w.selected.progress) }}%</strong
      ><progress max="100" :value="w.selected.progress"></progress>
    </div>
    <p v-if="w.selected.error" class="notice warning">{{ w.selected.error }}</p>
    <template v-if="w.selected.kind === 'evaluate' && result"
      ><EvidencePanel :evidence="evidence" />
      <p class="form-context">{{ result.protocol }}</p>
      <div class="year-detail-buttons">
        <button
          v-for="row in result.per_year"
          class="subtle"
          :class="{ selected: detailYear === row.year }"
          @click="openYear(row.year)"
        >
          {{ row.year }} 年明细<ArrowRight :size="14" />
        </button>
      </div>
      <ResultPanel
        v-if="detail"
        :job="detail"
        :catalog="w.catalog"
        :expert="false"
        :key="`${w.selected.id}-${detailYear}`"
    /></template>
    <ResultPanel
      v-if="w.selected.kind === 'backtest' && result"
      :job="w.selected"
      :catalog="w.catalog"
      :expert="false"
      :key="w.selected.id"
    />
    <LearningResults
      v-if="w.selected.kind === 'learn' && result"
      :job="w.selected"
      :catalog="w.catalog"
      :busy="w.busy"
      @apply="w.loadSuggestion"
    />
    <div v-if="w.selected.kind === 'paper' && result" class="notice">
      <p>模拟账户日期：{{ result.date || "未运行" }}。{{ result.note }}</p>
      <p v-for="warning in result.warnings">{{ warning }}</p>
      <button class="link-button" @click="w.navigate('account')">
        查看当前账户<ArrowRight :size="15" />
      </button>
    </div>
    <div v-if="w.selected.kind === 'refresh' && result" class="notice">
      <p>已读取 {{ result.coverage?.length || 0 }} 个标的的数据覆盖。</p>
      <p v-for="item in result.errors">
        {{ w.catalog.symbols[item.symbol] }}：{{ item.error }}
      </p>
      <button class="link-button" @click="w.navigate('data')">
        查看数据覆盖<ArrowRight :size="15" />
      </button>
    </div>
    <p v-if="!result && !running && !w.selected.error" class="empty-small">
      该任务没有回测结果。
    </p>
  </section>
  <div
    v-if="history"
    class="modal-backdrop editor-backdrop"
    @click.self="history = false"
  >
    <section
      class="parameter-editor history-drawer"
      role="dialog"
      aria-modal="true"
      aria-label="实验历史"
      v-modal="() => (history = false)"
    >
      <header class="editor-header">
        <h2>实验历史</h2>
        <button class="icon-btn" title="关闭历史记录" @click="history = false">
          <X :size="20" />
        </button>
      </header>
      <div class="editor-body">
        <label class="field"
          ><span>类型</span
          ><select v-model="filter">
            <option value="">全部任务</option>
            <option value="evaluate">跨年评估</option>
            <option value="backtest">单段回测</option>
            <option value="learn">参数优化</option>
            <option value="paper">模拟运行</option>
            <option value="refresh">更新行情</option>
          </select></label
        >
        <div class="history-list">
          <button
            v-for="job in items"
            :key="job.id"
            @click="
              w.selectJob(job.id);
              history = false;
            "
          >
            <span class="badge">{{ statusName(job.status) }}</span
            ><strong
              >{{ taskName(job.kind) }} · {{ job.kind === "research" ? "联网分析" : modelName(job.summary) }}</strong
            ><small
              >{{ taskStyle(job, w.catalog) }} ·
              {{ dateTime(job.created) }}</small
            ><span class="muted">{{ job.message }}</span>
          </button>
        </div>
      </div>
      <footer class="editor-footer">
        <button
          class="icon-btn"
          title="上一页"
          :disabled="page === 0"
          @click="
            page--;
            historyPage();
          "
        >
          <ArrowLeft :size="18" /></button
        ><span>{{ page + 1 }} / {{ Math.max(1, Math.ceil(total / 12)) }}</span
        ><button
          class="icon-btn"
          title="下一页"
          :disabled="(page + 1) * 12 >= total"
          @click="
            page++;
            historyPage();
          "
        >
          <ArrowRight :size="18" />
        </button>
      </footer>
    </section>
  </div>
</template>
