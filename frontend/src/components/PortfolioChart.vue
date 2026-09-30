<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from "vue";
const props = defineProps<{
  series: {
    name: string;
    points: { date: string; value: number }[];
    color: string;
  }[];
  percentage?: boolean;
}>();
const el = ref<HTMLElement>();
let chart: any,
  observer: ResizeObserver,
  alive = true;
function draw() {
  if (!chart) return;
  const dates = props.series[0]?.points.map((p) => p.date) || [];
  chart.setOption(
    {
      animation: false,
      textStyle: { fontFamily: "Segoe UI, Microsoft YaHei, sans-serif" },
      grid: {
        left: 58,
        right: 18,
        top: props.series.length > 3 && chart.getWidth() < 360 ? 66 : 35,
        bottom: 36,
      },
      tooltip: {
        trigger: "axis",
        confine: true,
        valueFormatter: (v: number) =>
          `${v.toFixed(2)}${props.percentage ? "%" : ""}`,
      },
      legend: {
        type: "plain",
        top: 0,
        left: 0,
        right: 0,
        icon: "roundRect",
        itemWidth: 13,
        itemHeight: 3,
        textStyle: { fontSize: 11, color: "#626970" },
      },
      xAxis: {
        type: "category",
        data: dates,
        boundaryGap: false,
        axisTick: { show: false },
        axisLine: { lineStyle: { color: "#dce2e5" } },
        axisLabel: {
          hideOverlap: true,
          fontSize: 10,
          formatter: (s: string) => s.includes(" ") ? s.slice(5, 16) : s.slice(2),
        },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: {
          fontSize: 10,
          formatter: (v: number) =>
            props.percentage ? `${v}%` : v.toLocaleString(),
        },
        splitLine: { lineStyle: { color: "#eef1f2" } },
      },
      series: props.series.map((s) => ({
        name: s.name,
        type: "line",
        data: s.points.map((p) => p.value),
        showSymbol: false,
        connectNulls: false,
        lineStyle: { color: s.color, width: 2 },
        itemStyle: { color: s.color },
      })),
    },
    true,
  );
}
onMounted(async () => {
  const ec = await import("echarts/core");
  const { LineChart } = await import("echarts/charts");
  const { GridComponent, TooltipComponent, LegendComponent } =
    await import("echarts/components");
  const { CanvasRenderer } = await import("echarts/renderers");
  if (!alive || !el.value) return;
  ec.use([
    LineChart,
    GridComponent,
    TooltipComponent,
    LegendComponent,
    CanvasRenderer,
  ]);
  chart = ec.init(el.value);
  observer = new ResizeObserver(() => {
    chart.resize();
    draw();
  });
  observer.observe(el.value);
  draw();
});
watch(() => props.series, draw, { deep: true });
onBeforeUnmount(() => {
  alive = false;
  observer?.disconnect();
  chart?.dispose();
});
</script>
<template>
  <div
    ref="el"
    class="portfolio-chart"
    role="img"
    aria-label="时间与数值走势对照图"
  ></div>
</template>
<style scoped>
.portfolio-chart {
  width: 100%;
  height: 285px;
  min-width: 0;
}
@media (max-width: 600px) {
  .portfolio-chart {
    height: 270px;
  }
}
</style>
