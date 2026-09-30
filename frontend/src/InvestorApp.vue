<script setup lang="ts">
import { defineAsyncComponent, provide } from 'vue';
import { ChartNoAxesCombined, FlaskConical, CircleAlert, Check, Square, X, LockKeyhole, ArrowRight, RefreshCw } from '@lucide/vue';
import { createInvestorWorkspace, investorKey } from './investor/workspace';
import './investor.css';
const InvestmentOverview = defineAsyncComponent(() => import('./investor/InvestmentOverview.vue'));
const InvestmentPlan = defineAsyncComponent(() => import('./investor/InvestmentPlan.vue'));
const InvestmentValidation = defineAsyncComponent(() => import('./investor/InvestmentValidation.vue'));
const InvestmentLive = defineAsyncComponent(() => import('./investor/InvestmentLive.vue'));
const workspace = createInvestorWorkspace();
provide(investorKey, workspace);
const {tabs, page, state, draft, error, toast, busy, connected, loading, remote, writable,
  showLogin, loginKey, showReset, jobNames, dirty, draftConflict, activeJobs,
  navigate, refresh, jobAction, login, reset} = workspace;
const closeDialog = () => { if (!busy.value) {showReset.value = false; showLogin.value = false;} };
</script>
<template>
  <div class="investor-shell">
    <header class="app-header">
      <a class="brand" href="#overview"><span class="brand-mark"><ChartNoAxesCombined :size="21"/></span><strong>StockLab</strong><span class="brand-sub">个人投资</span></a>
      <nav class="top-nav" aria-label="主导航"><button v-for="tab in tabs" :key="tab.id" :class="{selected:page === tab.id}" :aria-current="page === tab.id ? 'page' : undefined" @click="navigate(tab.id)"><component :is="tab.icon" :size="17"/>{{ tab.name }}</button></nav>
      <div class="header-tools">
        <span class="connection" :class="{offline:!connected}"><i/>{{ loading ? '正在连接' : connected ? '本机服务' : '离线' }}</span>
        <a href="/?lab=1#compare" class="workspace-link" title="研究工作台"><FlaskConical :size="17"/><span>研究</span></a>
        <button v-if="remote && !writable" class="icon-button" title="管理口令" aria-label="管理口令" @click="showLogin = true"><LockKeyhole :size="18"/></button>
      </div>
    </header>
    <main>
      <div v-if="error" class="notice error" role="alert"><CircleAlert :size="18"/><span>{{ error }}</span><button class="icon-button" aria-label="关闭错误" @click="error = ''"><X :size="16"/></button></div>
      <div v-if="toast" class="notice success" role="status"><Check :size="18"/><span>{{ toast }}</span><button class="icon-button" aria-label="关闭提示" @click="toast = ''"><X :size="16"/></button></div>
      <div v-if="!connected && !loading" class="notice warning" role="status"><span>服务连接中断，当前为上次读取的数据。</span><button :disabled="loading" @click="refresh(!state)"><RefreshCw :size="15"/>重试连接</button></div>
      <div v-if="loading" class="empty">正在读取本地账户…</div>
      <template v-else-if="state && draft">
        <div v-if="draftConflict" class="notice warning" role="alert"><span>另一设备已保存新版本。当前草稿仍保留，保存冲突时不会覆盖服务器参数。</span><button @click="navigate('plan')">查看草稿</button></div>
        <div v-else-if="dirty && page !== 'plan'" class="draft-reminder" role="status"><span>投资计划有未保存草稿，以下仍对应已保存计划。</span><button class="link-button" @click="navigate('plan')">返回草稿 <ArrowRight :size="15"/></button></div>
        <div v-for="job in activeJobs" :key="job.id" class="job-live" role="status">
          <div><strong>{{ jobNames[job.kind] || job.kind }} · {{ job.status === 'queued' ? '排队中' : '运行中' }}</strong><span>{{ job.message }}</span></div>
          <progress max="100" :value="job.progress" :aria-label="jobNames[job.kind] || '任务进度'"/><small>{{ Math.round(job.progress) }}%</small>
          <button class="icon-button" title="停止任务" aria-label="停止任务" :disabled="busy || !writable" @click="jobAction(job, 'stop')"><Square :size="15"/></button>
        </div>
        <InvestmentOverview v-if="page === 'overview'"/>
        <InvestmentPlan v-else-if="page === 'plan'"/>
        <InvestmentValidation v-else-if="page === 'validation'"/>
        <InvestmentLive v-else/>
        <footer>项目模拟不使用真钱 · 券商真实下单未启用 · 历史表现不保证未来收益</footer>
      </template>
    </main>
    <nav class="bottom-nav" aria-label="移动端导航"><button v-for="tab in tabs" :key="tab.id" :class="{selected:page === tab.id}" :aria-current="page === tab.id ? 'page' : undefined" @click="navigate(tab.id)"><component :is="tab.icon" :size="20"/><span>{{ tab.name }}</span></button></nav>
    <div v-if="showReset || showLogin" class="modal-backdrop" @click.self="closeDialog">
      <section class="dialog" role="dialog" aria-modal="true" :aria-label="showReset ? '归档当前模拟' : '远程管理口令'" v-modal="closeDialog">
        <div class="section-heading"><h2>{{ showReset ? '归档当前模拟' : '远程管理口令' }}</h2><button class="icon-button" aria-label="关闭" :disabled="busy" @click="closeDialog"><X :size="18"/></button></div>
        <template v-if="showReset"><p>当前持仓和成交会归档，新的模拟重新从起始资金开始。旧记录不会被删除。此操作不会归档或清空任何券商账户。</p><p v-if="error" class="notice error" role="alert">{{ error }}</p><div class="dialog-actions"><button :disabled="busy" @click="closeDialog">取消</button><button class="primary" :disabled="busy || workspace.jobsByKind('portfolio_update')" @click="reset">确认归档</button></div></template>
        <form v-else @submit.prevent="login"><label>管理口令<input type="password" v-model="loginKey" autocomplete="current-password"/></label><div class="dialog-actions"><button class="primary" type="submit">解锁</button></div></form>
      </section>
    </div>
  </div>
</template>
