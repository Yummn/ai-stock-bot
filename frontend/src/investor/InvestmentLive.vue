<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ChevronRight, Landmark, LockKeyhole, RefreshCw, ShieldCheck } from "@lucide/vue";
import { api, money } from "../api";
import { useInvestor } from "./workspace";

const { state, navigate, methodNames } = useInvestor();
const live = computed(() => state.value.live);
const accountView = ref<'live' | 'simulation'>('live');
const connected = computed(() => !!liveSnapshot.value);
const local = ["127.0.0.1", "localhost"].includes(location.hostname);
const sdkAvailable = ref(false);
const testOrderAvailable = ref(false);
const liveReadiness = ref<any>(null);
const liveSnapshot = ref<any>(null);
const liveBusy = ref(false);
const liveError = ref("");
const testAccount = ref("");
const testPassword = ref("");
const testBusy = ref(false);
const testError = ref("");
const testSnapshot = ref<any>(null);
const orderAccount = ref("");
const orderPassword = ref("");
const orderSymbol = ref("510300.SH");
const orderPrice = ref("");
const orderPreview = ref<{ symbol: string; price: string; requestId: string } | null>(null);
const orderBusy = ref(false);
const orderAttempted = ref(false);
const orderError = ref("");
const orderResult = ref<any>(null);
const testEtfs = [
  ["510300.SH", "沪深300ETF"], ["510500.SH", "中证500ETF"],
  ["512890.SH", "红利低波ETF"], ["518880.SH", "黄金ETF"],
  ["511260.SH", "十年国债ETF"], ["513100.SH", "纳指ETF"],
];

onMounted(async () => {
  if (local) {
    try {
      const availability = await api("/investment/broker/availability");
      sdkAvailable.value = availability.available;
      testOrderAvailable.value = availability.manual_test_order_available;
      liveReadiness.value = await api("/investment/broker/live-readiness", { cache: "no-store" });
    } catch {
      sdkAvailable.value = false;
    }
  }
});

async function readSimulation() {
  if (!local || !sdkAvailable.value || testBusy.value) return;
  testBusy.value = true;
  testError.value = "";
  testSnapshot.value = null;
  try {
    testSnapshot.value = await api("/investment/broker/snapshot", {
      method: "POST",
      body: JSON.stringify({ account: testAccount.value.trim(), password: testPassword.value }),
      cache: "no-store",
    });
  } catch (error: any) {
    testError.value = error.message;
  } finally {
    testAccount.value = "";
    testPassword.value = "";
    testBusy.value = false;
  }
}

async function readLiveAccount() {
  if (!local || !liveReadiness.value?.configured || liveBusy.value) return;
  liveBusy.value = true;
  liveError.value = "";
  liveSnapshot.value = null;
  try {
    liveSnapshot.value = await api("/investment/broker/live-snapshot", {
      method: "POST", cache: "no-store",
    });
  } catch (error: any) {
    liveError.value = error.message;
  } finally {
    liveBusy.value = false;
  }
}

function prepareOrder() {
  const price = Number(orderPrice.value);
  orderError.value = "";
  orderResult.value = null;
  if (!orderAccount.value.trim() || !orderPassword.value || !Number.isFinite(price) || price <= 0 || price > 10 || !/^\d+(\.\d{1,3})?$/.test(orderPrice.value)) {
    orderError.value = "请输入测试账号、密码，以及 0 至 10 元、最多三位小数的限价。";
    return;
  }
  orderPreview.value = { symbol: orderSymbol.value, price: orderPrice.value, requestId: crypto.randomUUID() };
  orderAttempted.value = false;
}

async function sendTestBuy() {
  if (!local || !testOrderAvailable.value || !orderPreview.value || orderBusy.value || orderAttempted.value) return;
  orderBusy.value = true;
  orderAttempted.value = true;
  orderError.value = "";
  try {
    orderResult.value = await api("/investment/broker/test-buy", {
      method: "POST",
      body: JSON.stringify({
        account: orderAccount.value.trim(), password: orderPassword.value,
        symbol: orderPreview.value.symbol, price: orderPreview.value.price,
        request_id: orderPreview.value.requestId,
        confirmation: "提交仿真测试委托",
      }),
      cache: "no-store",
    });
  } catch (error: any) {
    orderError.value = `${error.message}；若请求已经发出，请先到券商仿真终端核对订单，不要直接重发。`;
  } finally {
    orderPassword.value = "";
    orderAccount.value = "";
    orderBusy.value = false;
  }
}
const steps = computed(() => [
  {
    title: "取得真实账户与 EMT 实盘权限",
    detail: "测试账号不能代替真实资金账户；需由券商确认程序化权限、服务器与白名单 IP。",
    done: !!liveReadiness.value?.configured,
  },
  {
    title: "在本机核对真实账户",
    detail: "只读查询资金、持仓和未完结订单，不收集交易密码。",
    done: connected.value,
  },
  ...(live.value.requested_multiple > 1 ? [{
    title: "确认融资资格与可用额度",
    detail: "目标仓位倍数不是券商授信。未核对真实额度、利率和维保规则前不能下单。",
    done: false,
  }] : []),
  {
    title: "人工核对券商数据",
    detail: "将页面数据与东方财富终端逐项比较，确认未完成订单。",
    done: false,
  },
  {
    title: "设置实盘额度并核对首单",
    detail: "实盘限额与模拟资金分开设置，启用前先人工检查。",
    done: live.value.limits_configured && live.value.orders_enabled,
  },
]);
</script>

<template>
  <section class="live-page">
    <div class="page-title">
      <div>
        <p class="eyebrow">券商接入 · 与项目模拟账户分开</p>
        <h1>券商账户</h1>
        <p class="subtitle">EMT 仿真账户可查询并手动提交测试委托；真钱交易尚未接入。</p>
      </div>
      <span class="live-badge" :class="{ ready: connected }">
        <i />{{ connected ? "实盘只读已连接" : "实盘未接入" }}
      </span>
    </div>

    <div class="live-banner" role="status">
      <LockKeyhole :size="22" />
      <div>
        <strong>{{ live.orders_enabled ? "真实下单已启用" : "当前不会发生真实交易" }}</strong>
        <p v-if="!connected">仿真测试委托与真钱交易完全分开，也不会把测试资产算入投资计划。</p>
        <p v-else-if="!live.orders_enabled">已连接券商，但真实下单仍关闭。</p>
        <p v-else>请以券商返回的账户和订单状态为准。</p>
      </div>
    </div>

    <div class="validation-modes broker-modes" role="tablist" aria-label="券商账户类型">
      <button role="tab" :aria-selected="accountView === 'live'" :class="{selected:accountView === 'live'}" :disabled="testBusy || orderBusy" @click="accountView = 'live'">真实账户 · 只读</button>
      <button role="tab" :aria-selected="accountView === 'simulation'" :class="{selected:accountView === 'simulation'}" :disabled="liveBusy" @click="accountView = 'simulation'">券商仿真 · 测试</button>
    </div>
    <section v-show="accountView === 'simulation'" class="section">
      <div class="section-heading">
        <h2>EMT 仿真账户</h2>
        <span>查询 · 仅本机</span>
      </div>
      <template v-if="local && sdkAvailable">
        <form class="broker-form" autocomplete="off" @submit.prevent="readSimulation">
          <label>API 仿真测试账号
            <input v-model="testAccount" inputmode="numeric" autocomplete="off" required placeholder="输入券商提供的 API 测试账号" />
          </label>
          <label>API 仿真密码
            <input v-model="testPassword" type="password" autocomplete="new-password" required placeholder="仅供本次查询" />
          </label>
          <button class="primary" type="submit" :disabled="testBusy">
            <RefreshCw :size="16" />{{ testBusy ? "正在查询" : "查询仿真账户" }}
          </button>
        </form>
        <p class="live-footnote">这里使用单独申请的 EMT API 仿真账号，不是机构终端登录账号。账号和密码只用于当前请求，不保存；查询后会清空输入框。</p>
        <p v-if="testError" class="broker-error" role="alert">{{ testError }}</p>
        <template v-if="testSnapshot">
          <dl class="live-facts broker-facts">
            <div><dt>账户</dt><dd>{{ testSnapshot.account_masked }}</dd></div>
            <div><dt>总资产</dt><dd>{{ money(testSnapshot.equity) }} 元</dd></div>
            <div><dt>可用资金</dt><dd>{{ money(testSnapshot.cash) }} 元</dd></div>
            <div><dt>持仓</dt><dd>{{ testSnapshot.positions.length }} 只</dd></div>
            <div><dt>同步时间</dt><dd>{{ new Date(testSnapshot.last_sync * 1000).toLocaleString('zh-CN') }}</dd></div>
          </dl>
          <div v-if="testSnapshot.positions.length" class="broker-positions">
            <div v-for="item in testSnapshot.positions" :key="item.ticker">
              <strong>{{ item.name || item.ticker }}</strong>
              <span>{{ item.ticker }} · {{ item.quantity }} 股 · 可卖 {{ item.sellable }} 股</span>
            </div>
          </div>
        </template>
      </template>
      <p v-else class="live-footnote">{{ local ? "本机缺少 EMT 仿真 SDK，请检查安装。" : "券商账户查询仅能在电脑本机的 127.0.0.1 页面使用。" }}</p>
    </section>

    <details class="section" v-if="accountView === 'simulation' && local && testOrderAvailable">
      <summary>手动仿真测试委托 <span>不使用真钱</span></summary>
      <p class="live-footnote">仅限已核验的普通 EMT API 仿真账号。每次只买入 100 份列表中的 ETF，使用你指定的限价；不会自动选股、自动重试或使用融资。订单号不等于已成交。</p>
      <form class="broker-form test-order-form" autocomplete="off" @submit.prevent="prepareOrder">
        <label>ETF
          <select v-model="orderSymbol" @change="orderPreview = null">
            <option v-for="[symbol, name] in testEtfs" :key="symbol" :value="symbol">{{ name }} · {{ symbol }}</option>
          </select>
        </label>
        <label>买入限价（元）
          <input v-model.trim="orderPrice" @input="orderPreview = null" type="number" min="0.001" max="10" step="0.001" required placeholder="输入你愿意支付的最高价" />
        </label>
        <label>普通 API 仿真账号
          <input v-model.trim="orderAccount" @input="orderPreview = null" inputmode="numeric" autocomplete="off" required placeholder="输入普通测试资金账号" />
        </label>
        <label>本次仿真密码
          <input v-model="orderPassword" @input="orderPreview = null" type="password" autocomplete="new-password" required placeholder="仅供本次测试委托" />
        </label>
        <button class="primary" type="submit" :disabled="orderBusy">预览测试委托</button>
      </form>
      <div v-if="orderPreview" class="test-order-preview" role="region" aria-label="测试委托确认">
        <strong>请核对：仿真买入 {{ testEtfs.find(([symbol]) => symbol === orderPreview?.symbol)?.[1] }} 100 份</strong>
        <p>限价 {{ orderPreview.price }} 元 · 最大委托金额 {{ money(Number(orderPreview.price) * 100) }} 元 · 仅发送一次到东方财富仿真服务器</p>
        <button v-if="!orderAttempted" class="primary" type="button" :disabled="orderBusy" @click="sendTestBuy">{{ orderBusy ? "正在提交" : "确认提交仿真测试委托" }}</button>
      </div>
      <p v-if="orderError" class="broker-error" role="alert">{{ orderError }}</p>
      <div v-if="orderResult" class="test-order-result" role="status">
        <strong>仿真委托号：{{ orderResult.order_id }}</strong>
        <p>{{ orderResult.state === 'reported' ? '已收到券商订单回报，请以仿真终端中的最终状态为准。' : '已获得委托号，但订单状态暂不明确；请到仿真终端核对，不要重复提交。' }}</p>
        <p v-if="orderResult.report">状态码 {{ orderResult.report.order_status }} · 已成交 {{ orderResult.report.quantity_traded }} 份 · 错误码 {{ orderResult.report.error_id }}</p>
      </div>
    </details>

    <div v-show="accountView === 'live'" class="live-grid">
      <section class="section">
        <div class="section-heading">
          <h2>实盘账户</h2>
          <span>{{ liveSnapshot ? `只读同步于 ${new Date(liveSnapshot.last_sync * 1000).toLocaleString('zh-CN')}` : "尚无账户数据" }}</span>
        </div>
        <p v-if="local && liveReadiness && !liveReadiness.configured" class="live-footnote">
          {{ liveReadiness.reason }}。需要真实证券资金账号、券商确认的 EMT 实盘交易服务器，以及本机出口 IP 白名单；当前只有测试账号，不能拿来接实盘。
          <a href="https://emt.18.cn/business/business-apply-all" target="_blank" rel="noopener noreferrer">查看东方财富申请入口</a>
        </p>
        <template v-else-if="local && liveReadiness?.configured">
          <p class="live-footnote">已录入本机实盘只读资料（{{ liveReadiness.account_masked }}）。这只表示本机资料已齐，是否开通仍以券商连接结果为准。不会发送任何订单。</p>
          <button class="primary live-read-button" type="button" :disabled="liveBusy || !liveReadiness.sdk_available" @click="readLiveAccount">
            <RefreshCw :size="16" />{{ liveBusy ? "正在核对" : "只读连接真实账户" }}
          </button>
          <p v-if="!liveReadiness.sdk_available" class="broker-error">本机缺少 EMT Python SDK。</p>
          <p v-if="liveError" class="broker-error" role="alert">{{ liveError }}</p>
        </template>
        <dl class="live-facts">
          <div><dt>交易通道</dt><dd>{{ liveSnapshot?.provider || "待连接" }}</dd></div>
          <div><dt>账户</dt><dd>{{ liveSnapshot?.account_masked || liveReadiness?.account_masked || "未连接" }}</dd></div>
          <div><dt>总资产</dt><dd>{{ liveSnapshot ? `${money(liveSnapshot.equity)} 元` : "—" }}</dd></div>
          <div><dt>可用资金</dt><dd>{{ liveSnapshot ? `${money(liveSnapshot.cash)} 元` : "—" }}</dd></div>
          <div><dt>持仓</dt><dd>{{ liveSnapshot ? `${liveSnapshot.positions.length} 只` : "—" }}</dd></div>
          <div><dt>未完成订单</dt><dd>{{ liveSnapshot ? `${liveSnapshot.open_orders.length} 笔` : "—" }}</dd></div>
        </dl>
        <div v-if="liveSnapshot?.open_orders?.length" class="broker-positions">
          <div v-for="order in liveSnapshot.open_orders" :key="order.order_id">
            <strong>{{ order.ticker }} · {{ order.side === 1 ? "买入" : "卖出" }}</strong>
            <span>{{ order.quantity }} 份 · 已成交 {{ order.quantity_traded }} · 限价 {{ money(order.price) }} 元</span>
          </div>
        </div>
        <p class="live-footnote">上方 EMT 仿真测试账户与此处真实账户完全独立；未连接时不使用仿真或项目模拟数据代替。</p>
      </section>

      <section class="section">
        <div class="section-heading">
          <h2>接入进度</h2>
          <span>{{ steps.filter((step) => step.done).length }} / {{ steps.length }} 完成</span>
        </div>
        <ol class="live-steps">
          <li v-for="(step, index) in steps" :key="step.title">
            <span class="live-step-number" :class="{ done: step.done }">
              <ShieldCheck v-if="step.done" :size="15" />{{ step.done ? "" : index + 1 }}
            </span>
            <div><strong>{{ step.title }}</strong><small>{{ step.detail }}</small></div>
            <span class="live-step-state">{{ step.done ? "完成" : "待完成" }}</span>
          </li>
        </ol>
      </section>
    </div>

    <details class="section">
      <summary>真实交易状态与计划指令</summary>
      <div class="section-heading">
        <h2>交易状态</h2>
        <span>默认关闭</span>
      </div>
      <div class="live-controls">
        <div><span>计划指令</span><strong>{{ live.requested_interval !== 'daily' ? live.requested_interval.replace('m', ' 分钟') : '日线' }} · {{ live.requested_multiple }}x</strong></div>
        <div><span>实盘执行</span><strong>未生效</strong></div>
        <div><span>真实下单</span><strong>{{ live.orders_enabled ? "开启" : "关闭" }}</strong></div>
        <div><span>自动下单</span><strong>{{ live.automatic_orders_enabled ? "开启" : "关闭" }}</strong></div>
        <div><span>远程下单</span><strong>{{ live.remote_orders_enabled ? "开启" : "关闭" }}</strong></div>
      </div>
      <div class="live-plan-reference">
        <Landmark :size="18" />
        <p>投资计划选择了 <strong>{{ live.requested_interval !== 'daily' ? live.requested_interval + ' T+0 规则策略' : methodNames[state.settings.plan.method] }} · {{ live.requested_multiple }}x</strong>。模拟账户会按该指令记账；上方手动仿真测试委托不执行此计划。实盘执行与融资权限尚未核验，不能据此真实下单。</p>
        <button class="link-button" @click="navigate('plan')">查看投资计划 <ChevronRight :size="15" /></button>
      </div>
    </details>
  </section>
</template>
