<script setup lang="ts">
import { provide } from "vue";
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
  Download,
  X,
  LockKeyhole,
} from "@lucide/vue";
import { createInvestorWorkspace, investorKey } from "./investor/workspace";
import InvestmentOverview from "./investor/InvestmentOverview.vue";
import InvestmentLive from "./investor/InvestmentLive.vue";
import InvestmentPlan from "./investor/InvestmentPlan.vue";
import InvestmentValidation from "./investor/InvestmentValidation.vue";
import "./investor.css";
const workspace = createInvestorWorkspace();
provide(investorKey, workspace);
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
} = workspace;
</script>
<template>
  <div class="investor-shell">
    <header class="app-header">
      <a class="brand" href="#overview" @click="navigate('overview')"
        ><span class="brand-mark"><ChartNoAxesCombined :size="21" /></span
        ><strong>StockLab</strong><span class="brand-sub">个人投资</span></a
      >
      <nav class="top-nav" aria-label="主导航">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          :class="{ selected: page === tab.id }"
          @click="navigate(tab.id)"
        >
          <component :is="tab.icon" :size="17" />{{ tab.name }}
        </button>
      </nav>
      <div class="header-tools">
        <span class="connection" :class="{ offline: !connected }"
          ><i />{{ connected ? "服务已连接" : "连接中断" }}</span
        ><a
          href="/?lab=1#strategy"
          class="icon-button"
          title="研究工具"
          aria-label="研究工具"
          ><FlaskConical :size="18" /></a
        ><button
          v-if="remote && !writable"
          class="icon-button"
          title="管理口令"
          @click="showLogin = true"
        >
          <LockKeyhole :size="18" />
        </button>
      </div>
    </header>
    <main>
      <div v-if="error" class="notice error" role="alert">
        <CircleAlert :size="18" /><span>{{ error }}</span
        ><button class="icon-button" aria-label="关闭错误" @click="error = ''">
          <X :size="16" />
        </button>
      </div>
      <div v-if="toast" class="notice success" role="status">
        <Check :size="18" /><span>{{ toast }}</span
        ><button class="icon-button" aria-label="关闭提示" @click="toast = ''">
          <X :size="16" />
        </button>
      </div>
      <div v-if="!connected && !loading" class="notice warning">
        暂时无法连接本机服务，以下为上次成功读取的状态。
      </div>
      <div v-if="loading" class="empty">正在读取本地账户…</div>
      <template v-else-if="state && draft">
        <div v-for="job in activeJobs" :key="job.id" class="job-live">
          <div>
            <strong>{{ jobNames[job.kind] }}</strong
            ><span>{{ job.message }}</span>
          </div>
          <progress max="100" :value="job.progress" /><small
            >{{ Math.round(job.progress) }}%</small
          ><button
            class="icon-button"
            title="停止任务"
            aria-label="停止任务"
            @click="jobAction(job, 'stop')"
          >
            <Square :size="15" />
          </button>
        </div>

        <InvestmentOverview v-if="page === 'overview'" />

        <InvestmentLive v-else-if="page === 'live'" />

        <InvestmentPlan v-else-if="page === 'plan'" />

        <InvestmentValidation v-else />
        <footer>模拟与实盘分开 · 实盘尚未接入 · 不保证盈利</footer>
      </template>
    </main>
    <nav class="bottom-nav" aria-label="移动端导航">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        :class="{ selected: page === tab.id }"
        @click="navigate(tab.id)"
      >
        <component :is="tab.icon" :size="20" /><span>{{ tab.name }}</span>
      </button>
    </nav>
    <div
      v-if="showReset || showLogin"
      class="modal-backdrop"
      @click.self="
        showReset = false;
        showLogin = false;
      "
    >
      <section class="dialog" role="dialog" aria-modal="true" v-modal>
        <div class="section-heading">
          <h2>{{ showReset ? "归档当前模拟" : "远程管理口令" }}</h2>
          <button
            class="icon-button"
            aria-label="关闭"
            @click="
              showReset = false;
              showLogin = false;
            "
          >
            <X :size="18" />
          </button>
        </div>
        <template v-if="showReset"
          ><p>
            当前持仓和成交会归档，新的模拟重新从起始资金开始。旧记录不会被删除。
          </p>
          <button class="primary" @click="reset">确认归档</button></template
        ><template v-else-if="showLogin"
          ><label
            >管理口令<input
              type="password"
              v-model="loginKey"
              autocomplete="current-password" /></label
          ><button class="primary" @click="login">解锁</button></template
        >
      </section>
    </div>
  </div>
</template>
