<script setup lang="ts">
import { onMounted, onBeforeUnmount, watch, ref } from "vue";

const props = defineProps<{
  points: any[];
  trades?: any[];
  compact?: boolean;
  equity?: boolean;
}>();
const host = ref<HTMLElement>();
let chart: any,
  observer: ResizeObserver,
  alive = true;
function draw() {
  if (!chart || !props.points.length) return;
  const dates = props.points.map((p) => p.date);
  const series: any[] = [
    {
      name: props.equity ? "策略资产" : "收盘价",
      type: "line",
      showSymbol: false,
      data: props.points.map((p) => (props.equity ? p.equity : p.close)),
      lineStyle: { color: "#059669", width: props.compact ? 1.8 : 2 },
      areaStyle: props.compact ? undefined : { color: "rgba(5,150,105,.04)" },
      animation: false,
    },
  ];
  if (props.equity)
    series.push({
      name: "买入持有",
      type: "line",
      showSymbol: false,
      data: props.points.map((p) => p.hold_equity),
      lineStyle: { color: "#a1a1aa", width: 1.5, type: "dashed" },
      animation: false,
    });
  if (props.trades)
    for (const side of ["buy", "sell"]) {
      series.push({
        name: side === "buy" ? "买入" : "卖出",
        type: "scatter",
        symbolSize: 6,
        data: props.trades
          .filter((t) => t.side === side && dates.includes(t.date))
          .map((t) => [t.date, t.price]),
        itemStyle: {
          color: side === "buy" ? "#eab308" : "#fff",
          borderColor: side === "buy" ? "#a16207" : "#2563eb",
          borderWidth: 1.5,
        },
        z: 5,
        animation: false,
      });
    }
  series[0].itemStyle = { color: "#059669" };
  chart.setOption(
    {
      animation: false,
      textStyle: { fontFamily: "Segoe UI, Microsoft YaHei, sans-serif" },
      tooltip: {
        trigger: "axis",
        confine: true,
        valueFormatter: (value: any) =>
          Number(
            Array.isArray(value) ? value[value.length - 1] : value,
          ).toLocaleString("zh-CN", {
            minimumFractionDigits: props.equity ? 2 : 3,
            maximumFractionDigits: props.equity ? 2 : 3,
          }),
      },
      grid: props.compact
        ? { left: 0, right: 0, top: 5, bottom: 5 }
        : { left: 64, right: 18, top: 22, bottom: 56 },
      legend: props.equity
        ? { right: 10, top: 0, textStyle: { color: "#71717a", fontSize: 11 } }
        : undefined,
      xAxis: {
        type: "category",
        data: dates,
        boundaryGap: false,
        show: !props.compact,
        axisLine: { lineStyle: { color: "#e4e4e7" } },
        axisTick: { show: false },
        axisLabel: {
          color: "#71717a",
          fontSize: 10,
          formatter: (v: string) => v.slice(2),
        },
      },
      yAxis: {
        type: "value",
        scale: true,
        show: !props.compact,
        axisLabel: { color: "#71717a", fontSize: 10 },
        splitLine: { lineStyle: { color: "#f0f0f1" } },
      },
      dataZoom: props.compact
        ? []
        : [
            { type: "inside" },
            {
              type: "slider",
              height: 16,
              bottom: 4,
              borderColor: "#e4e4e7",
              fillerColor: "rgba(5,150,105,.1)",
              handleStyle: { color: "#059669" },
              textStyle: { fontSize: 10 },
            },
          ],
      series,
    },
    true,
  );
}
onMounted(async () => {
  const echarts = await import("echarts/core");
  const { LineChart, ScatterChart } = await import("echarts/charts");
  const {
    GridComponent,
    TooltipComponent,
    LegendComponent,
    DataZoomComponent,
  } = await import("echarts/components");
  const { CanvasRenderer } = await import("echarts/renderers");
  if (!alive || !host.value) return;
  echarts.use([
    LineChart,
    ScatterChart,
    GridComponent,
    TooltipComponent,
    LegendComponent,
    DataZoomComponent,
    CanvasRenderer,
  ]);
  chart = echarts.init(host.value);
  observer = new ResizeObserver(() => chart?.resize());
  observer.observe(host.value);
  draw();
});
watch(() => [props.points, props.trades], draw, { deep: false });
onBeforeUnmount(() => {
  alive = false;
  observer?.disconnect();
  chart?.dispose();
});
</script>
<template>
  <div
    ref="host"
    :class="['plot', { spark: compact }]"
    role="img"
    :aria-label="equity ? '策略与持有资产曲线' : '行情走势，黄色买入，蓝色卖出'"
  ></div>
</template>
