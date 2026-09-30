<script setup lang="ts">
import {
  computed,
  defineAsyncComponent,
  onBeforeUnmount,
  onMounted,
  provide,
  ref,
} from "vue";
import {
  Activity,
  Wallet,
  Layers3,
  FlaskConical,
  Database,
  X,
  CircleAlert,
  CircleCheck,
  LockKeyhole,
  ArrowLeft,
  ArrowRight,
  LoaderCircle,
} from "@lucide/vue";
import { createWorkspace } from "./workspace";
import { api } from "./api";
const AccountView = defineAsyncComponent(
  () => import("./components/AccountView.vue"),
);
const StrategyView = defineAsyncComponent(
  () => import("./components/StrategyView.vue"),
);
const ExperimentsView = defineAsyncComponent(
  () => import("./components/ExperimentsView.vue"),
);
const DataView = defineAsyncComponent(
  () => import("./components/DataView.vue"),
);
const ParameterEditor = defineAsyncComponent(
  () => import("./components/ParameterEditor.vue"),
);
const w = createWorkspace();
provide("workspace", w);
const nav = [
  { key: "account", name: "账户", icon: Wallet },
  { key: "strategy", name: "方案", icon: Layers3 },
  { key: "experiments", name: "实验", icon: FlaskConical },
];
const title = computed(
  () =>
    (
      ({
        account: "模拟账户",
        strategy: "策略方案",
        experiments: "实验中心",
        data: "数据与维护",
      }) as any
    )[w.view],
);
const accessKey = ref(""),
  visibleKey = ref("");
const isInvestorLab =
  import.meta.env.VITE_INVESTOR === "1" &&
  new URLSearchParams(location.search).has("lab");
const localUpgradeUrl = ["127.0.0.1", "localhost", "[::1]"].includes(
  location.hostname,
)
  ? "http://127.0.0.1:8768/"
  : "";
async function login() {
  await w.act(async () => {
    await api("/access/login", {
      method: "POST",
      body: JSON.stringify({ key: accessKey.value }),
    });
    w.access = await api("/access");
    accessKey.value = "";
    w.accessDialog = false;
    w.flash("远程管理已解锁");
  });
}
async function showKey() {
  await w.act(async () => (visibleKey.value = (await api("/access/key")).key));
}
function closeAccess() {
  w.accessDialog = false;
  visibleKey.value = "";
}
onMounted(w.init);
onBeforeUnmount(w.dispose);
</script>
<template>
  <div class="app-shell">
    <aside class="sidebar">
      <a href="#account" class="brand" @click.prevent="w.navigate('account')"
        ><span class="brand-icon"><Activity :size="24" /></span
        ><span>行情实验室<small>STOCKLAB</small></span></a
      >
      <div class="nav-label">工作空间</div>
      <nav>
        <button
          v-for="item in nav"
          :class="['nav-item', { active: w.view === item.key }]"
          @click="w.navigate(item.key)"
        >
          <component :is="item.icon" :size="19" /><span>{{ item.name }}</span
          ><i
            v-if="
              item.key === 'experiments' &&
              w.jobs.some((j: any) => ['running', 'queued'].includes(j.status))
            "
            class="running-dot"
          ></i>
        </button>
      </nav>
      <div class="sidebar-bottom">
        <button
          :class="['nav-item', { active: w.view === 'data' }]"
          @click="w.navigate('data')"
        >
          <Database :size="18" />数据与维护
        </button>
        <div class="connection">
          <i :class="w.online ? 'online' : 'offline'"></i
          >{{
            w.upgradeRequired
              ? "服务待升级"
              : w.online
                ? "本机服务在线"
                : "连接中断"
          }}<span>{{ w.serverVersion || "3.0" }}</span>
        </div>
      </div>
    </aside>
    <main class="workspace">
      <header class="topbar">
        <a v-if="isInvestorLab" class="return-investor" href="/"
          ><ArrowLeft :size="16" />返回个人投资</a
        >
        <strong v-else>{{ title }}</strong>
        <div class="topbar-actions">
          <button
            v-if="w.dirty()"
            class="draft-link"
            @click="w.navigate('strategy')"
          >
            有未启用的修改<ArrowRight :size="13" /></button
          ><span class="mode-badge">模拟交易</span
          ><button
            class="icon-btn mobile-utility"
            title="数据与维护"
            @click="w.navigate('data')"
          >
            <Database :size="18" /></button
          ><button
            v-if="w.access.remote && !w.access.write_allowed"
            class="icon-btn"
            title="解锁管理"
            @click="w.accessDialog = true"
          >
            <LockKeyhole :size="18" />
          </button>
        </div>
      </header>
      <div class="main-content">
        <div v-if="w.externalChange" class="banner warning" role="alert">
          <CircleAlert :size="18" /><span
            >其他设备已更新启用方案，当前编辑草稿仍保留。</span
          ><button class="link-button" @click="w.reload">载入最新</button>
        </div>
        <div v-if="w.error" class="banner error" role="alert">
          <CircleAlert :size="18" /><span>{{ w.error }}</span
          ><button class="icon-btn" title="关闭错误" @click="w.error = ''">
            <X :size="16" />
          </button>
        </div>
        <div v-if="w.notice" class="banner success" role="status">
          <CircleCheck :size="18" /><span>{{ w.notice }}</span
          ><button class="icon-btn" title="关闭通知" @click="w.notice = ''">
            <X :size="16" />
          </button>
        </div>
        <div v-if="w.loading" class="loading">
          <LoaderCircle :size="24" class="spin" />正在载入工作空间
        </div>
        <div v-else-if="w.upgradeRequired" class="empty-small">
          <h2>当前入口仍连接旧服务</h2>
          <p>服务版本 {{ w.serverVersion }}，新版界面需要 3.0 服务。</p>
          <a v-if="localUpgradeUrl" :href="localUpgradeUrl" class="link-button"
            >打开新版工作空间 <ArrowRight :size="16"
          /></a>
          <p v-else>请由本机管理员启动新版，并更新隧道的本地目标地址。</p>
        </div>
        <template v-else-if="w.active"
          ><AccountView v-if="w.view === 'account'" /><StrategyView
            v-else-if="w.view === 'strategy'"
          /><ExperimentsView v-else-if="w.view === 'experiments'" /><DataView
            v-else-if="w.view === 'data'"
          />
          <footer class="workspace-footer">
            <span>行情实验室</span><span>日线研究 · 数据与结果保存在本机</span>
          </footer></template
        >
        <div v-else class="empty-small">
          <p>暂时无法连接服务。</p>
          <button class="subtle" @click="w.init">重新连接</button>
        </div>
      </div>
    </main>
    <nav class="bottom-nav">
      <button
        v-for="item in nav"
        :class="{ active: w.view === item.key }"
        @click="w.navigate(item.key)"
      >
        <component :is="item.icon" :size="21" /><span>{{ item.name }}</span>
      </button>
    </nav>
    <ParameterEditor v-if="w.editor" :key="w.editor" />
    <div v-if="w.accessDialog" class="modal-backdrop" @click.self="closeAccess">
      <section
        class="modal"
        role="dialog"
        aria-modal="true"
        aria-label="访问管理"
        v-modal="closeAccess"
      >
        <div class="section-title">
          <h2>访问管理</h2>
          <button class="icon-btn" title="关闭访问管理" @click="closeAccess">
            <X :size="19" />
          </button>
        </div>
        <template v-if="w.access.remote"
          ><label class="field"
            ><span>管理口令</span
            ><input
              v-model="accessKey"
              type="password"
              autocomplete="off"
              @keyup.enter="login" /></label
          ><button class="primary full" :disabled="w.busy" @click="login">
            解锁管理
          </button></template
        ><template v-else
          ><p class="muted">
            远程设备需要管理口令才能运行实验、修改方案和模拟交易。
          </p>
          <button class="subtle" @click="showKey">显示管理口令</button>
          <pre v-if="visibleKey" class="access-key">{{ visibleKey }}</pre>
        </template>
      </section>
    </div>
  </div>
</template>
