<script setup lang="ts">
import { computed } from "vue";
const props = defineProps<{ report: any; catalog: any }>();
const old = computed(
  () => Date.now() - new Date(props.report.fetched_at).getTime() > 86400000,
);
const source = (id: string) =>
  props.report.sources.find((s: any) => s.id === id);
</script>
<template>
  <div class="research-report">
    <p class="muted">
      研究于 {{ new Date(report.fetched_at).toLocaleString() }} ·
      {{ report.provider }} · {{ report.search_count }} 次联网检索
    </p>
    <p v-if="old || report.status === 'limited'" class="notice warning">
      {{
        old
          ? "这份研究超过一天未更新。"
          : "缺少可确认日期的近期来源，不能据此判断当前热点。"
      }}
    </p>
    <p>{{ report.summary }}</p>
    <p>{{ report.market_outlook }}</p>
    <section v-for="topic in report.topics" class="research-topic">
      <h3>
        {{ topic.name }}
        <small v-if="!topic.current_evidence">背景资料 / 日期待核实</small>
      </h3>
      <p><strong>报道依据：</strong>{{ topic.evidence }}</p>
      <p><strong>分析推断：</strong>{{ topic.analysis }}</p>
      <p><strong>风险：</strong>{{ topic.risks }}</p>
      <p v-if="topic.related_symbols.length" class="muted">
        相关标的：{{
          topic.related_symbols
            .map((s: string) => catalog.symbols[s] || s)
            .join("、")
        }}
      </p>
      <ul class="research-sources">
        <li v-for="id in topic.source_ids">
          <a
            :href="source(id)?.url"
            target="_blank"
            rel="noopener noreferrer"
            >{{ source(id)?.title }}</a
          ><small>
            · {{ source(id)?.publisher }} ·
            {{ source(id)?.published_at || "发布日期不明" }}</small
          >
        </li>
      </ul>
    </section>
    <details class="fold">
      <summary>日线趋势与资料限制</summary>
      <div class="inner">
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>标的</th>
                <th>行情日期</th>
                <th>20日涨跌</th>
                <th>指标分类</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in report.trends">
                <td>{{ r.name }}</td>
                <td>
                  {{ r.date || "缺少数据"
                  }}<small v-if="r.stale_days > 7">（已过期）</small>
                </td>
                <td>
                  {{
                    r.momentum20 == null
                      ? "—"
                      : (r.momentum20 * 100).toFixed(2) + "%"
                  }}
                </td>
                <td>
                  {{
                    ({ bull: "强势", bear: "弱势", sideways: "震荡" } as any)[
                      r.scene
                    ] || "待更新"
                  }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-for="line in report.uncertainties">{{ line }}</p>
        <p class="muted">{{ report.note }}</p>
      </div>
    </details>
  </div>
</template>
<style scoped>
.research-report {
  overflow-wrap: anywhere;
}
.research-topic {
  padding: 18px 0;
  border-top: 1px solid #dbe3e7;
}
.research-topic h3 {
  margin: 0 0 10px;
  font-size: 17px;
}
.research-topic p {
  line-height: 1.7;
  margin: 8px 0;
}
.research-topic h3 small {
  font-size: 12px;
  color: #687783;
  font-weight: 400;
}
.research-sources {
  padding-left: 18px;
  line-height: 1.7;
}
.research-sources a {
  color: #087f72;
  text-decoration: underline;
}
</style>
