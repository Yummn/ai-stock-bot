<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ evidence: any; catalog: any }>();
const rows = computed(() =>
  Array.isArray(props.evidence?.references)
    ? props.evidence.references
    : props.evidence?.model
      ? [props.evidence]
      : [],
);
const available = computed(() =>
  rows.value.filter((row: any) => row.status === "ok"),
);
const count = (side: string) =>
  available.value.filter((row: any) => row.side === side).length;
const sideName = (side: string) =>
  ({ buy: "买入", sell: "卖出", hold: "观望" })[side as "buy"] || "不可用";
const targetName = (row: any) =>
  `${row.target === "trend5" ? "五分类" : "二分类"} · ${row.horizon || 1} 日`;
</script>

<template>
  <details class="ml-reference-panel">
    <summary>
      机器学习参考 · {{ available.length }}/{{ rows.length }} 组有效
      <span v-if="available.length">
        买 {{ count("buy") }} · 卖 {{ count("sell") }} · 观望
        {{ count("hold") }}
      </span>
    </summary>
    <p class="ml-reference-note">
      各模型的机会分未经统一校准，预测目标和时间也可能不同；AI
      综合判断时不会直接平均这些分数。
    </p>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>模型</th>
            <th>档位</th>
            <th>建议</th>
            <th>机会分</th>
            <th>预测</th>
            <th>训练样本</th>
            <th>训练截止</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="`${row.model}-${row.profile}`">
            <td>{{ catalog.models[row.model] || row.model }}</td>
            <td>{{ catalog.profiles[row.profile] || row.profile || "—" }}</td>
            <td>{{ row.status === "ok" ? sideName(row.side) : "不可用" }}</td>
            <td>
              {{
                row.opportunity_score == null
                  ? "—"
                  : `${(row.opportunity_score * 100).toFixed(1)}%`
              }}
            </td>
            <td>{{ row.target ? targetName(row) : "—" }}</td>
            <td>{{ row.samples ?? "—" }}</td>
            <td>{{ row.training_cutoff || "—" }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </details>
</template>

<style scoped>
.ml-reference-panel {
  grid-column: 1 / -1;
  min-width: 0;
  border-top: 1px solid var(--line);
  padding-top: 8px;
}
.ml-reference-panel summary {
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
}
.ml-reference-panel summary span {
  color: var(--muted);
  font-weight: 400;
  margin-left: 8px;
}
.ml-reference-note {
  margin: 10px 0;
  color: var(--muted);
  font-size: 12px;
}
.ml-reference-panel table {
  min-width: 660px;
  font-size: 12px;
}
.ml-reference-panel th,
.ml-reference-panel td {
  padding: 9px 10px;
  white-space: nowrap;
}
</style>
