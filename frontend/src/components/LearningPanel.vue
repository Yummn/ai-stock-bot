<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ArrowRight, ChevronLeft, ChevronRight } from "@lucide/vue";
import { api, pct } from "../api";
const props = defineProps<{ job: any; catalog: any; busy: boolean }>();
defineEmits(["apply"]);
const profile = ref("balanced"),
  page = ref(0),
  items = ref<any[]>([]),
  error = ref("");
const profiles = computed(() => Object.keys(props.job.result.best || {}));
const suggestion = computed(() => props.job.result.best[profile.value]);
const checksComplete = computed(() =>
  (props.job.request.check_years || []).every((year: number) =>
    suggestion.value?.check_years?.some(
      (row: any) => row.year === year && row.return_pct != null,
    ),
  ),
);
const rows = computed(() =>
  suggestion.value
    ? [
        ...suggestion.value.per_year,
        ...(suggestion.value.check_years || []).map((r: any) => ({
          ...r,
          check: true,
        })),
      ]
    : [],
);
const names: Record<string, string> = {
  training_years: "训练年数",
  training_mode: "样本来源",
  target: "分类目标",
  label_basis: "训练收益口径",
  horizon: "预测跨度",
  buy_threshold: "买入门槛",
  sell_threshold: "卖出门槛",
  min_samples: "最小样本",
  max_buys: "每日买入上限",
  allow_add: "允许加仓",
  calibrate_regime: "行情校准",
  buy_score: "规则买入分",
  ai_prompt: "AI 提示词",
};
watch(
  profiles,
  (keys) => {
    if (!keys.includes(profile.value)) profile.value = keys[0] || "balanced";
  },
  { immediate: true },
);
watch(
  () => [props.job.id, props.job.result_version, page.value],
  async () => {
    try {
      items.value = (
        await api(
          `/jobs/${props.job.id}/trials?limit=15&offset=${page.value * 15}`,
        )
      ).items;
      error.value = "";
    } catch (e: any) {
      error.value = e.message;
    }
  },
  { immediate: true },
);
</script>
<template>
  <section class="learning-results">
    <div class="section-title">
      <h2>优化建议</h2>
      <span class="muted"
        >{{ job.result.rounds }} 轮 · {{ job.result.total }} 个候选</span
      >
    </div>
    <div class="profile-tabs">
      <button
        v-for="key in profiles"
        :class="{ active: profile === key }"
        @click="profile = key"
      >
        {{ catalog.profiles[key] }}
      </button>
    </div>
    <div v-if="suggestion" class="suggestion-focus">
      <div class="section-title">
        <div>
          <h3>{{ catalog.profiles[profile] }} · {{ suggestion.label }}</h3>
          <p>
            调参期平均收益 {{ pct(suggestion.mean_return) }} · 最差年份
            {{ pct(suggestion.worst_return) }}
          </p>
        </div>
        <button
          class="primary"
          :disabled="busy || !checksComplete"
          @click="$emit('apply', profile)"
        >
          载入方案<ArrowRight :size="16" />
        </button>
      </div>
      <p v-if="!checksComplete" class="notice warning">
        验收年份尚未全部完成，暂不能载入建议。
      </p>
      <p
        v-if="
          suggestion.deployment_reason ===
          'lower drawdown accepted with bull-return tradeoff'
        "
        class="notice"
      >
        这组参数的回撤更低，同时强市收益低于参照。
      </p>
      <div class="year-comparison">
        <div class="year-head">
          <span>年份</span><span>方案收益</span><span>买入持有</span
          ><span>超额</span>
        </div>
        <div class="year-row" v-for="row in rows">
          <strong
            >{{ row.year
            }}<small>{{ row.check ? "验收" : "调参" }}</small></strong
          ><template v-if="row.return_pct != null"
            ><span :class="row.return_pct >= 0 ? 'positive' : 'negative'">{{
              pct(row.return_pct)
            }}</span
            ><span>{{ pct(row.benchmark_return) }}</span
            ><span :class="row.excess_return >= 0 ? 'positive' : 'negative'">{{
              pct(row.excess_return)
            }}</span></template
          ><span v-else class="year-error negative">{{ row.error }}</span>
        </div>
      </div>
      <p v-if="job.result.protocol" class="form-context">
        调参：{{ job.result.protocol.search_years.join("、") }}；验收：{{
          job.result.protocol.check_years.join("、") || "未设置"
        }}。验收结果不参与候选排名。
      </p>
      <details class="fold">
        <summary>完整建议参数</summary>
        <div class="parameter-readout">
          <template
            v-for="(value, key) in suggestion.config.models[
              job.request.config.model
            ][profile]"
            ><div v-if="key !== 'params' && key !== 'ai_prompt'">
              <span>{{ names[key] || key }}</span
              ><strong>{{ value }}</strong>
            </div></template
          >
          <div
            v-for="(value, key) in suggestion.config.models[
              job.request.config.model
            ][profile].params"
          >
            <span>{{
              catalog.parameters[job.request.config.model].find(
                (f: any) => f.key === key,
              )?.label || key
            }}</span
            ><strong>{{ value }}</strong>
          </div>
        </div>
      </details>
    </div>
    <div v-else class="empty-small">完整跑通所选年份后会生成建议。</div>
    <details class="fold">
      <summary>
        搜索记录 <span>{{ job.result.total }} 个候选</span>
      </summary>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>候选</th>
              <th>有效年份</th>
              <th>平均收益</th>
              <th>评分</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items">
              <td>
                <details>
                  <summary>{{ item.label }}</summary>
                  <p v-for="r in item.per_year">
                    {{ r.year }}：{{ r.error || pct(r.return_pct) }}
                  </p>
                </details>
              </td>
              <td>{{ item.valid_years }}/{{ item.expected_years }}</td>
              <td>{{ pct(item.mean_return) }}</td>
              <td>{{ item.score?.toFixed(4) ?? "未完成" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="pagination">
        <button
          class="icon-btn"
          title="上一页候选"
          :disabled="page === 0"
          @click="page--"
        >
          <ChevronLeft :size="17" /></button
        ><span
          >{{ page + 1 }} /
          {{ Math.max(1, Math.ceil(job.result.total / 15)) }}</span
        ><button
          class="icon-btn"
          title="下一页候选"
          :disabled="(page + 1) * 15 >= job.result.total"
          @click="page++"
        >
          <ChevronRight :size="17" />
        </button>
      </div>
    </details>
    <p v-if="error" class="notice warning">{{ error }}</p>
  </section>
</template>
