<script setup lang="ts">
import { computed, inject, ref, watch, onBeforeUnmount, onMounted } from "vue";
import {
  Save,
  SlidersHorizontal,
  ArrowRight,
  RotateCcw,
  FolderOpen,
  Check,
} from "@lucide/vue";
import { methods, modelName, preset, profileNotes, selectionName } from "../workspace";
import { api, clone, money } from "../api";
import EvidencePanel from "./EvidencePanel.vue";
const w = inject<any>("workspace");
const method = computed({
  get: () => (w.draft.engine === "ml" ? w.draft.model : w.draft.engine),
  set: (key: string) => {
    const m = methods.find((m) => m.key === key)!;
    w.draft.engine = m.type;
    if (m.type === "ml") w.draft.model = key;
    else if (w.draft.profile_selection === "recent") w.draft.profile_selection = "trend";
  },
});
const p = computed(() => preset(w.draft)),
  library = ref(false),
  plans = ref<any[]>([]);
const selection = computed({
  get: () => w.draft.profile_selection || (w.draft.auto_profile ? "trend" : "fixed"),
  set: (mode: string) => {
    w.draft.profile_selection = mode;
    w.draft.auto_profile = mode !== "fixed";
  },
});
let timer: ReturnType<typeof setTimeout>;
onBeforeUnmount(() => clearTimeout(timer));
onMounted(() => {
  void w.refreshEvidence();
  void w.refreshProfilePreview();
});
watch(
  () => JSON.stringify(w.draft),
  () => {
    w.invalidateEvidence();
    w.invalidateProfilePreview();
    clearTimeout(timer);
    timer = setTimeout(() => {
      void w.refreshEvidence();
      void w.refreshProfilePreview();
    }, 400);
  },
);
async function showLibrary() {
  library.value = !library.value;
  if (library.value)
    await w.act(async () => (plans.value = (await api("/plans")).items));
}
async function load(id: string) {
  await w.act(async () => {
    w.draft = clone((await api(`/plans/${id}`)).config);
    library.value = false;
    w.flash("方案已载入，尚未启用");
  });
}
async function snapshot() {
  await w.act(async () => {
    await api("/plans", { method: "POST", body: JSON.stringify(w.draft) });
    w.flash("已存入方案库");
    plans.value = (await api("/plans")).items;
  });
}
</script>
<template>
  <div class="page-title">
    <div>
      <span class="overline">STRATEGY</span>
      <h1>策略方案</h1>
    </div>
    <button class="subtle" @click="showLibrary">
      <FolderOpen :size="16" />方案库
    </button>
  </div>
  <div class="active-line">
    <span class="status-dot done"></span><span>当前启用</span
    ><strong
      >{{ modelName(w.active) }} ·
      {{ w.active.auto_profile ? selectionName(w.active) : w.catalog.profiles[w.active.profile] }}</strong
    ><span v-if="w.dirty()" class="draft-badge">正在编辑另一组参数</span
    ><Check v-else :size="15" />
  </div>
  <section v-if="library" class="library-band">
    <div class="section-title">
      <h2>保存的方案</h2>
      <button class="subtle" @click="snapshot">
        <Save :size="15" />保存当前快照
      </button>
    </div>
    <label class="field"
      ><span>方案名称</span><input v-model="w.draft.name" maxlength="80"
    /></label>
    <div class="library-list">
      <button v-for="plan in plans" :key="plan.id" @click="load(plan.id)">
        <FolderOpen :size="16" /><span>{{ plan.name }}</span
        ><ArrowRight :size="15" />
      </button>
    </div>
  </section>
  <div class="strategy-layout">
    <section class="strategy-choice">
      <label class="field method-choice"
        ><span>决策方法</span
        ><select v-model="method" :disabled="!w.access.write_allowed">
          <optgroup label="机器学习">
            <option
              v-for="item in methods.filter((m) => m.type === 'ml')"
              :value="item.key"
            >
              {{ item.name }} · {{ item.code }}
            </option>
          </optgroup>
          <optgroup label="其他方法">
            <option value="local">规则策略</option>
            <option value="ai">AI 分析 · Codex / OpenAI</option>
          </optgroup>
        </select></label
      >
      <label class="field profile-method">
        <span>交易时怎样选档</span>
        <select v-model="selection" :disabled="!w.access.write_allowed">
          <option value="fixed">固定使用一个档位</option>
          <option value="trend">根据价格走势选择</option>
          <option v-if="w.draft.engine === 'ml' && w.capabilities.includes('recent_profile_selection')" value="recent">根据近三个月模拟成绩选择</option>
        </select>
      </label>
      <p class="form-context" v-if="selection === 'recent'">每只 ETF 分别模拟三档近 60 个交易日的净收益；激进亏损再看均衡，两档都亏损时选保守。记录不足则按价格走势选择。</p>
      <p class="form-context" v-else-if="selection === 'trend'">每只 ETF 根据截至当天的价格趋势选择激进、均衡或保守。</p>
      <div v-if="selection === 'fixed'" class="section-title"><h2>固定档位</h2></div>
      <div v-if="selection === 'fixed'" class="style-options" role="group" aria-label="策略风格">
        <button
          v-for="key in ['conservative', 'balanced', 'aggressive', 'custom']"
          :class="{ selected: w.draft.profile === key }"
          :disabled="!w.access.write_allowed"
          @click="w.draft.profile = key"
        >
          <span class="option-radio"
            ><i v-if="w.draft.profile === key"></i></span
          ><span
            ><strong>{{ w.catalog.profiles[key] }}</strong
            ><small>{{ profileNotes[key] }}</small></span
          ><Check v-if="w.draft.profile === key" :size="16" />
        </button>
      </div>
      <div v-else class="auto-profile-selection">
        <div class="section-title">
          <h2>{{ selection === 'recent' ? '最近一次自动选档' : '当前自动选档' }}</h2>
          <small>{{ w.draft.symbols.length }} 只 ETF</small>
        </div>
        <div v-if="w.profilePreviewLoading" class="empty-small">正在读取选档结果…</div>
        <div v-else-if="w.profilePreview?.error" class="notice">{{ w.profilePreview.error }}</div>
        <div v-else-if="selection === 'recent' && w.profilePreview?.rows?.length && w.profilePreview.rows.every((row: any) => row.status === 'pending')" class="auto-profile-pending">
          <strong>当前选档尚未计算</strong>
          <p>这组方案还没有匹配的近三个月模拟结果，不能沿用旧方案的档位。</p>
          <p>{{ w.dirty() ? '先保存并启用，再到模拟账户运行。' : '到模拟账户运行后，这里会显示每只 ETF 的档位和原因。' }}同一交易日已运行过时，需等下一交易日。</p>
          <button v-if="!w.dirty()" class="subtle" @click="w.navigate('account')">前往模拟账户<ArrowRight :size="15" /></button>
        </div>
        <div v-else class="auto-profile-list">
          <div v-for="row in w.profilePreview?.rows || []" :key="row.symbol" class="auto-profile-row">
            <div>
              <strong>{{ row.name }}</strong>
              <span :class="['auto-profile-grade', row.profile || 'pending']">{{ row.profile ? w.catalog.profiles[row.profile] : '未计算' }}</span>
            </div>
            <p>{{ row.reason }}</p>
            <small v-if="row.date">截至 {{ row.date }}</small>
          </div>
        </div>
        <p v-if="selection === 'recent' && w.profilePreview?.rows?.some((row: any) => row.status === 'pending')" class="form-context">首次计算三档近三个月成绩可能较慢。</p>
      </div>
      <div class="strategy-facts">
        <div>
          <span>交易范围</span
          ><strong>{{ w.draft.symbols.length }} 个 ETF</strong>
        </div>
        <div>
          <span>单笔金额上限</span
          ><strong>¥{{ money(w.draft.risk.max_trade_cash) }}</strong>
        </div>
        <div v-if="w.draft.engine === 'ml'">
          <span>学习历史</span
          ><strong
            >{{ p.training_years }} 年 ·
            {{
              { self: "单标的", classified: "按行情分组", mixed: "混合样本" }[
                p.training_mode as "self"
              ]
            }}</strong
          >
        </div>
      </div>
      <button class="subtle full" @click="w.editor = 'strategy'">
        <SlidersHorizontal :size="16" />查看与调整参数
      </button>
    </section>
    <EvidencePanel
      :title="selection === 'recent' ? '根据近三个月模拟成绩选择 · 历史表现' : selection === 'trend' ? '根据价格走势选择 · 历史表现' : '固定档位 · 历史表现'"
      :evidence="w.evidence"
      :loading="w.evidenceLoading"
      @open="w.selectJob"
      @test="w.testDraft"
    />
  </div>
  <div class="strategy-actions">
    <div>
      <strong
        >{{ modelName(w.draft) }} ·
        {{ w.draft.auto_profile ? selectionName(w.draft) : w.catalog.profiles[w.draft.profile] }}</strong
      ><small>{{
        w.dirty() ? "编辑方案尚未启用" : "已与当前启用参数一致"
      }}</small>
    </div>
    <div class="inline-actions">
      <button
        v-if="w.dirty()"
        class="icon-btn"
        title="放弃草稿并读取当前启用方案"
        @click="w.reload"
      >
        <RotateCcw :size="17" /></button
      ><button
        class="subtle"
        :disabled="w.busy || !w.access.write_allowed"
        @click="w.testDraft"
      >
        评估方案<ArrowRight :size="16" /></button
      ><button
        class="primary"
        :disabled="w.busy || !w.dirty() || !w.access.write_allowed"
        @click="w.save"
      >
        <Save :size="16" />保存并启用
      </button>
    </div>
  </div>
</template>
