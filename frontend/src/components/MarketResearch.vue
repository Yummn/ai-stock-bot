<script setup lang="ts">
import { inject, ref, computed, onMounted, onBeforeUnmount } from "vue";
import { Globe, Square } from "@lucide/vue";
import { api } from "../api";
import ResearchReport from "./ResearchReport.vue";
const w = inject<any>("workspace");
const data = ref<any>({ report: null, jobs: [] }),
  error = ref(""),
  open = ref(false);
const active = computed(() =>
  data.value.jobs.find((j: any) => ["queued", "running"].includes(j.status)),
);
let timer: ReturnType<typeof setInterval>,
  loading = false;
async function refresh() {
  if (loading || document.hidden) return;
  loading = true;
  try {
    data.value = await api("/research");
    error.value = "";
  } catch (e: any) {
    error.value = e.message;
  } finally {
    loading = false;
  }
}
async function run() {
  await w.act(async () => {
    await api("/jobs", {
      method: "POST",
      body: JSON.stringify({ kind: "research", config: w.active }),
    });
    open.value = true;
    await refresh();
    await w.refreshJobs();
  });
}
async function stop() {
  await w.act(async () => {
    await api(`/jobs/${active.value.id}/stop`, { method: "POST" });
    await refresh();
    await w.refreshJobs();
  });
}
onMounted(() => {
  void refresh();
  timer = setInterval(() => {
    if (
      active.value ||
      w.jobs.some(
        (j: any) =>
          j.kind === "research" &&
          j.updated > (data.value.jobs[0]?.updated || 0),
      )
    )
      void refresh();
  }, 5000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>
<template>
  <section class="research-band">
    <div class="section-title">
      <h2>热点与走势</h2>
      <div class="inline-actions">
        <button
          v-if="active"
          class="subtle"
          @click="stop"
          :disabled="w.busy || !w.access.write_allowed"
        >
          <Square :size="15" />停止</button
        ><button
          class="subtle"
          @click="run"
          :disabled="w.busy || !!active || !w.access.write_allowed"
        >
          <Globe :size="16" />{{ data.report ? "更新研究" : "联网研究" }}
        </button>
      </div>
    </div>
    <p v-if="active" class="notice">
      {{ active.message }} · {{ Math.round(active.progress) }}%
    </p>
    <p v-if="error" class="notice warning">{{ error }}</p>
    <p v-if="data.jobs[0]?.status === 'failed'" class="notice warning">
      本次研究失败：{{ data.jobs[0].error }}。{{
        data.report ? "下方保留上一次报告。" : ""
      }}
    </p>
    <p v-if="!data.report && !active" class="muted">尚无联网研究记录</p>
    <details
      v-if="data.report"
      class="fold"
      :open="open"
      @toggle="open = ($event.target as HTMLDetailsElement).open"
    >
      <summary>
        市场研究报告
        <span>{{ new Date(data.report.fetched_at).toLocaleDateString() }}</span>
      </summary>
      <div class="inner">
        <ResearchReport :report="data.report" :catalog="w.catalog" />
      </div>
    </details>
  </section>
</template>
<style scoped>
.research-band {
  padding: 24px 0;
  border-top: 1px solid #dbe3e7;
}
.research-band h2 {
  font-size: 20px;
}
</style>
