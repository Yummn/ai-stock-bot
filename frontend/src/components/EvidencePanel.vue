<script setup lang="ts">
import { pct } from "../api";
import { ArrowUpRight, CircleCheck, CircleDashed } from "@lucide/vue";
defineProps<{ evidence: any; loading?: boolean; compact?: boolean; title?: string }>();
defineEmits(["open", "test"]);
</script>
<template>
  <section class="evidence-panel">
    <div class="section-title">
      <h2>{{ title || "历史表现" }}</h2>
      <span class="badge" v-if="evidence?.matched"
        ><CircleCheck :size="13" />参数一致</span
      >
    </div>
    <div v-if="loading" class="empty-small">正在匹配评估记录…</div>
    <template v-else-if="evidence?.matched">
      <div class="evidence-verdict">
        <strong>{{ evidence.scorecard.verdict }}</strong
        ><span
          >{{ evidence.scorecard.beat_hold }} /
          {{ evidence.scorecard.count }} 年跑赢持有</span
        >
      </div>
      <div class="evidence-stats">
        <div>
          <span>年度平均收益</span
          ><strong
            :class="
              evidence.scorecard.mean_return >= 0 ? 'positive' : 'negative'
            "
            >{{ pct(evidence.scorecard.mean_return) }}</strong
          >
        </div>
        <div>
          <span>最差一年</span
          ><strong
            :class="
              evidence.scorecard.worst_return >= 0 ? 'positive' : 'negative'
            "
            >{{ pct(evidence.scorecard.worst_return) }}</strong
          >
        </div>
        <div>
          <span>最大回撤</span
          ><strong>{{ pct(evidence.scorecard.max_drawdown) }}</strong>
        </div>
      </div>
      <div v-if="!compact" class="year-comparison">
        <div class="year-head">
          <span>年份</span><span>方案收益</span><span>买入持有</span
          ><span>超额</span>
        </div>
        <div v-for="row in evidence.per_year" class="year-row" :key="row.year">
          <strong>{{ row.year }}<small v-if="row.check">验收</small></strong
          ><template v-if="row.return_pct != null"
            ><span :class="row.return_pct >= 0 ? 'positive' : 'negative'">{{
              pct(row.return_pct)
            }}</span
            ><span>{{ pct(row.benchmark_return) }}</span
            ><span :class="row.excess_return >= 0 ? 'positive' : 'negative'">{{
              pct(row.excess_return)
            }}</span></template
          ><span v-else class="negative year-error">{{
            row.error || "未完成"
          }}</span>
        </div>
      </div>
      <div class="evidence-caption">
        <span>{{ evidence.origin }}</span
        ><button
          v-if="evidence.job_id"
          class="link-button"
          @click="$emit('open', evidence.job_id)"
        >
          原始记录<ArrowUpRight :size="14" />
        </button>
      </div>
    </template>
    <div v-else class="evidence-empty">
      <CircleDashed :size="27" />
      <h3>这组参数还没有匹配的评估</h3>
      <p>{{ evidence?.error || "修改参数、资金或标的后，需要重新评估。" }}</p>
      <button v-if="title" class="subtle" @click="$emit('test')">测试这种方式</button>
    </div>
  </section>
</template>
