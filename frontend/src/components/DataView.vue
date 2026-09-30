<script setup lang="ts">
import { defineAsyncComponent, inject, onMounted, ref } from "vue";
import {
  Database,
  RefreshCw,
  Download,
  Upload,
  Save,
  ShieldCheck,
} from "@lucide/vue";
import { api, pct, sceneName } from "../api";
const Plot = defineAsyncComponent(() => import("./Plot.vue"));
const w = inject<any>("workspace"),
  coverage = ref<any[]>([]),
  labels = ref<any[]>([]),
  opened = ref<Record<string, boolean>>({}),
  loading = ref(true),
  labelOpen = ref(false),
  poolOpen = ref(false);
onMounted(async () => {
  try {
    coverage.value = (await api("/data")).items;
  } catch (e: any) {
    w.error = e.message;
  } finally {
    loading.value = false;
  }
});
async function loadLabels() {
  if (!labels.value.length)
    await w.act(async () => (labels.value = (await api("/labels")).items));
}
function exportConfig() {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(w.draft, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = "StockLab-方案.json";
  a.click();
  URL.revokeObjectURL(url);
}
async function captureDefaults() {
  if (
    !window.confirm(
      "将当前已启用的全部参数设为恢复默认基线？未保存的草稿不会计入。",
    )
  )
    return;
  await w.act(async () => {
    await api("/settings/defaults", {
      method: "POST",
      body: JSON.stringify({ revision: w.revision }),
    });
    w.catalog = await api("/catalog");
    w.flash("已保存默认参数，之后的方案修改不会覆盖这份基线");
  });
}
async function importConfig(event: Event) {
  const f = (event.target as HTMLInputElement).files?.[0];
  if (!f) return;
  await w.act(async () => {
    w.draft = (
      await api("/settings/validate", { method: "POST", body: await f.text() })
    ).config;
    w.navigate("strategy");
    w.flash("已导入编辑方案，尚未启用");
  });
}
function setLabel(symbol: string, year: number, value: string) {
  if (value === "auto") delete w.draft.annual_labels[`${symbol}:${year}`];
  else w.draft.annual_labels[`${symbol}:${year}`] = value;
}
</script>
<template>
  <div class="page-title">
    <div>
      <span class="overline">DATA & PREFERENCES</span>
      <h1>数据与维护</h1>
    </div>
    <button
      class="primary"
      :disabled="w.busy || !w.access.write_allowed"
      @click="w.submit('refresh')"
    >
      <RefreshCw :size="16" />更新行情
    </button>
  </div>
  <section class="data-section">
    <div class="section-title">
      <h2>历史数据覆盖</h2>
      <span class="muted">{{ coverage.length }} 个标的</span>
    </div>
    <div v-if="loading" class="empty-small">正在读取数据覆盖…</div>
    <div v-else class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>标的</th>
            <th>用途</th>
            <th>最早日期</th>
            <th>最新日期</th>
            <th>日线数量</th>
            <th>来源</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in coverage">
            <td>
              <strong>{{ row.name }}</strong
              ><small class="block">{{ row.symbol }}</small>
            </td>
            <td>
              <span class="badge">{{
                w.active.symbols.includes(row.symbol) ? "交易" : "补充训练"
              }}</span>
            </td>
            <td>{{ row.first || "无数据" }}</td>
            <td>{{ row.last || "无数据" }}</td>
            <td>{{ row.bars }}</td>
            <td>{{ row.source || "未下载" }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
  <details
    class="fold"
    @toggle="poolOpen = ($event.target as HTMLDetailsElement).open"
  >
    <summary>
      交易范围与补充训练标的
      <span
        >{{ w.draft.symbols.length }} /
        {{ w.draft.training_symbols.length }}</span
      >
    </summary>
    <div v-if="poolOpen" class="inner">
      <h3>参与模拟与回测</h3>
      <div class="symbol-grid">
        <label v-for="(name, symbol) in w.catalog.symbols"
          ><input
            type="checkbox"
            v-model="w.draft.symbols"
            :value="symbol"
            :disabled="!w.access.write_allowed"
          /><span
            >{{ name }}<small>{{ symbol }}</small></span
          ></label
        >
      </div>
      <h3>补充训练样本</h3>
      <div class="symbol-grid">
        <label v-for="(name, symbol) in w.catalog.symbols"
          ><input
            type="checkbox"
            v-model="w.draft.training_symbols"
            :value="symbol"
            :disabled="!w.access.write_allowed"
          /><span
            >{{ name }}<small>{{ symbol }}</small></span
          ></label
        >
      </div>
    </div>
  </details>
  <details
    class="fold"
    @toggle="
      labelOpen = ($event.target as HTMLDetailsElement).open;
      labelOpen && loadLabels();
    "
  >
    <summary>年度行情标签 <span>自动判断与人工修订</span></summary>
    <div v-if="labelOpen" class="inner">
      <p class="form-context">完整年度标签只用于当年结束之后的历史训练。</p>
      <details
        v-for="stock in labels"
        class="fold"
        @toggle="
          opened[stock.symbol] = ($event.target as HTMLDetailsElement).open
        "
      >
        <summary>
          {{ stock.name }} <span>{{ stock.years.length }} 年</span>
        </summary>
        <div v-if="opened[stock.symbol]" class="annual-grid">
          <article v-for="item in stock.years" class="annual-item">
            <div class="section-title">
              <strong>{{ item.year }}</strong
              ><span :class="item.return_pct >= 0 ? 'positive' : 'negative'">{{
                pct(item.return_pct)
              }}</span>
            </div>
            <Plot :points="item.curve" compact /><label class="field"
              ><span>{{ item.complete ? "完整年度" : "部分年度" }}</span
              ><select
                :value="
                  w.draft.annual_labels[`${stock.symbol}:${item.year}`] ||
                  'auto'
                "
                :disabled="!w.access.write_allowed"
                @change="
                  setLabel(
                    stock.symbol,
                    item.year,
                    ($event.target as HTMLSelectElement).value,
                  )
                "
              >
                <option value="auto">
                  自动：{{ sceneName(item.automatic) }}
                </option>
                <option value="bull">人工：牛市</option>
                <option value="bear">人工：熊市</option>
                <option value="sideways">人工：震荡</option>
              </select></label
            >
          </article>
        </div>
      </details>
    </div>
  </details>
  <section class="maintenance-row">
    <div>
      <h2>方案文件</h2>
      <p>包含五种模型全部档位、资金限制和标的设置。</p>
    </div>
    <div class="inline-actions">
      <button class="subtle" @click="exportConfig">
        <Download :size="16" />导出</button
      ><label class="button subtle"
        ><Upload :size="16" />导入<input
          hidden
          type="file"
          accept="application/json"
          @change="importConfig"
      /></label>
    </div>
  </section>
  <section class="maintenance-row">
    <div>
      <h2>恢复默认基线</h2>
      <p>
        {{
          w.catalog.defaults_saved_at
            ? "已保存 · " +
              new Date(w.catalog.defaults_saved_at * 1000).toLocaleString(
                "zh-CN",
              )
            : "使用初始内置参数"
        }}
      </p>
    </div>
    <button
      class="subtle"
      :disabled="w.busy || !w.access.write_allowed"
      @click="captureDefaults"
    >
      <Save :size="16" />将启用参数设为默认
    </button>
  </section>
  <section class="maintenance-row">
    <div>
      <h2>访问管理</h2>
      <p>
        {{ w.access.remote ? "当前为远程访问" : "当前为本机访问" }} ·
        {{ w.access.write_allowed ? "可管理" : "只读" }}
      </p>
    </div>
    <button class="subtle" @click="w.accessDialog = true">
      <ShieldCheck :size="16" />管理权限
    </button>
  </section>
  <div v-if="w.dirty()" class="strategy-actions">
    <span>标的或标签修改尚未启用</span
    ><button
      class="primary"
      :disabled="w.busy || !w.access.write_allowed"
      @click="w.save"
    >
      <Save :size="16" />保存并启用
    </button>
  </div>
</template>
