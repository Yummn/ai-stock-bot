import { computed, onMounted, onBeforeUnmount, ref, watch } from "vue";
import {
  Activity,
  SlidersHorizontal,
  ChartNoAxesCombined,
  Landmark,
  RefreshCw,
  Save,
  FlaskConical,
  ArrowUpRight,
  Sparkles,
  ShieldCheck,
  CircleAlert,
  Check,
  Square,
  Play,
  Download,
  X,
  LockKeyhole,
} from "@lucide/vue";
import { api, clone } from "../api";
import { inject, type InjectionKey } from "vue";
export const intradayNames: Record<string, string> = {
  legacy: "原始均线", cost_aware: "成本过滤 + 波动限仓",
  breakout: "通道突破", ml_edge: "盘中回归 · 实验",
};
export const profileNames: Record<string, string> = {
  conservative: "保守", balanced: "均衡", aggressive: "激进", custom: "自定义",
};
export function createInvestorWorkspace() {
  const tabs = [
    { id: "overview", name: "总览", icon: Activity },
    { id: "plan", name: "投资计划", icon: SlidersHorizontal },
    { id: "validation", name: "验证", icon: ChartNoAxesCombined },
    { id: "live", name: "券商", icon: Landmark },
  ];
  const page = ref(location.hash.slice(1) || "overview");
  const state = ref<any>(),
    draft = ref<any>(),
    report = ref<any>(),
    auditData = ref<any>();
  const reportError = ref("");
  const reportMatchesPlan = computed(() => !!report.value?.plan_key &&
    report.value.plan_key === state.value?.plan_key);
  const saved = ref(""),
    error = ref(""),
    toast = ref(""),
    busy = ref(false),
    connected = ref(true),
    loading = ref(true);
  const draftRevision = ref(1);
  const profileSlot = ref('custom');
  const profileDrafts = ref<Record<string, any>>({});
  const asset = ref("510300.SH"),
    interval = ref("daily"),
    chartData = ref<any>(),
    chartBusy = ref(false);
  const startYear = ref(new Date().getFullYear() - 5),
    endYear = ref(new Date().getFullYear() - 1);
  const periodDirty = computed(() => !!state.value && (
    startYear.value !== state.value.settings.plan.validation_start_year ||
    endYear.value !== state.value.settings.plan.validation_end_year));
  const activeProfileName = computed(() => profileNames[state.value?.profile_library?.active_slot] || '自定义');
  const draftConflict = computed(() => dirty.value && draftRevision.value !== state.value?.settings?.revision);
  const remote = ref(false),
    writable = ref(true),
    showLogin = ref(false),
    loginKey = ref(""),
    showReset = ref(false);
  const jobNames: Record<string, string> = {
    portfolio_update: "模拟更新",
    portfolio_observe: "前向对照更新",
    portfolio_test: "历史验证",
    portfolio_frequency: "频率与倍数组合实验",
    portfolio_research: "模型研究",
    portfolio_review: "AI 解读",
    intraday_refresh: "小时行情更新",
    intraday_test: "盘中滚动验证",
  };
  const methodNames: Record<string, string> = {
    rotation: "趋势分散组合",
    ml_class: "梯度树 · 涨跌分类",
    ml: "机器学习 · 净收益回归",
    cnn_class: "1D-CNN · 涨跌分类",
    cnn: "1D-CNN · 净收益回归",
    hold: "同板块偏好持有",
    hold_full: "全额持有",
    hold_1x: "1x 同偏好持有",
    unlevered: "当前策略 · 1x",
    stress: "当前方案成本加倍",
  };
  const comparisonFamily = ref<"ml" | "cnn">("ml");
  const comparisonKeys = computed(() => [
    "rotation",
    `${comparisonFamily.value}_class`,
    comparisonFamily.value,
    "hold",
    "hold_full",
  ]);
  const dirty = computed(
    () => !!draft.value && (JSON.stringify(draft.value) !== saved.value ||
      profileSlot.value !== (state.value?.profile_library?.active_slot || state.value?.settings?.plan?.profile)),
  );
  const activeJobs = computed(() =>
    (state.value?.jobs || []).filter((j: any) =>
      ["queued", "running"].includes(j.status),
    ),
  );
  const resultRows = computed(() =>
    report.value
      ? ["rotation", "ml_class", "ml", "cnn_class", "cnn", "unlevered", "hold_1x", "hold", "hold_full", "stress"]
          .filter((key) => report.value.results[key])
          .map((key) => ({ key, ...report.value.results[key] }))
      : [],
  );
  const comparisonSeries = computed(() =>
    comparisonKeys.value
      .filter((k) => report.value?.results[k])
      .map((k) => ({
        name: k === 'ml' ? signalName(report.value.plan) : ({ rotation: "趋势", ml_class: "梯度树分类", cnn_class: "CNN 分类", cnn: "CNN 回归", hold: "同偏好持有", hold_full: "全额持有" } as Record<string, string>)[k],
        color: ({ rotation: "#0e8074", ml_class: "#b96f30", ml: "#4267c6", cnn_class: "#aa6e91", cnn: "#c45c49", hold: "#8e9670", hold_full: "#775f80" } as Record<string, string>)[k],
        points: report.value.results[k].curve.map((p: any) => ({
          date: p.date,
          value: (p.equity / report.value.plan.starting_cash - 1) * 100,
        })),
      })),
  );
  const years = computed(
    () =>
      report.value?.results.rotation?.per_year.map((r: any) => r.year) || [],
  );
  const yearly = (key: string, year: number) =>
    report.value?.results[key]?.per_year.find((r: any) => r.year === year)
      ?.return_pct;
  const name = (symbol: string) => state.value?.catalog[symbol]?.name || symbol;
  const positionRows = computed(() =>
    Object.entries(state.value?.account?.positions || {}).map(
      ([symbol, p]) => ({ symbol, ...(p as any) }),
    ),
  );
  const jobsByKind = (kind: string) =>
    activeJobs.value.some((j: any) => j.kind === kind);
  const gainClass = (n: number) =>
    n > 0 ? "positive" : n < 0 ? "negative" : "";
  const totalTarget = computed(() =>
    Object.values(state.value?.decision?.weights || {}).reduce(
      (a: number, b: any) => a + Number(b),
      0,
    ) * Number(state.value?.settings?.plan?.research_multiple || 1),
  );
  const targetRows = computed(() => Object.entries(state.value?.decision?.weights || {})
    .filter(([, weight]) => Number(weight) > 0)
    .map(([symbol, weight]) => ({ symbol, weight: Number(weight) * Number(state.value?.settings?.plan?.research_multiple || 1),
      name: state.value?.catalog?.[symbol]?.name || symbol,
      group: state.value?.catalog?.[symbol]?.group || "" })));
  const summarySeries = computed(() => [
    {
      name: "模拟资产",
      color: "#0e8074",
      points: (state.value?.curve || []).map((p: any) => ({
        date: p.date,
        value: p.equity,
      })),
    },
  ]);
  let timer: ReturnType<typeof setTimeout>;
  let alive = true,
    lastReportJob = "";
  let refreshRequest = 0, chartRequest = 0;

  function notify(message: string) {
    toast.value = message;
  }
  function navigate(id: string) {
    page.value = tabs.some(tab => tab.id === id) ? id : 'overview';
    location.hash = page.value;
  }
  function hashChanged() {
    page.value = tabs.some((t) => t.id === location.hash.slice(1))
      ? location.hash.slice(1)
      : "overview";
  }
  function signalName(plan: any) {
    return plan?.method === 'ml' && plan?.model_recipe !== 'legacy'
      ? (state.value?.model_recipes?.[plan.model_recipe]?.label || plan.model_recipe) + (plan.model_tuning ? ' · 研究参数' : '')
      : methodNames[plan?.method] || plan?.method;
  }
  function hasProfileEdits(slot: string) {
    const candidate = slot === profileSlot.value ? draft.value : profileDrafts.value[slot];
    const original = state.value?.profile_library?.entries?.[slot]?.plan;
    const ignored = [...(state.value?.profile_library?.shared_fields || []), 'profile', 'preset_version'];
    return !!candidate && !!original && Object.keys(original).some(key =>
      !ignored.includes(key) && JSON.stringify(candidate[key]) !== JSON.stringify(original[key]));
  }
  function beforeUnload(event: BeforeUnloadEvent) {
    if (dirty.value || Object.keys(profileDrafts.value).some(hasProfileEdits)) {
      event.preventDefault(); event.returnValue = '';
    }
  }
  function selectProfileSlot(slot: string) {
    if (draft.value) profileDrafts.value[profileSlot.value] = clone(draft.value);
    const stored = state.value?.profile_library?.entries?.[slot]?.plan;
    const next = profileDrafts.value[slot] || stored;
    if (!next) return;
    const shared = Object.fromEntries((state.value.profile_library.shared_fields || [])
      .map((key: string) => [key, draft.value[key]]));
    draft.value = { ...clone(next), ...shared };
    profileSlot.value = slot;
    saved.value = JSON.stringify(stored || next);
  }
  function restoreDraft(clearBuffers = true) {
    if (clearBuffers) profileDrafts.value = {};
    draft.value = clone(state.value.settings.plan);
    profileSlot.value = state.value.settings.profile_slot || state.value.profile_library?.active_slot || draft.value.profile;
    if (draft.value.method.startsWith("cnn")) comparisonFamily.value = "cnn";
    else if (draft.value.method.startsWith("ml")) comparisonFamily.value = "ml";
    saved.value = JSON.stringify(draft.value);
    draftRevision.value = state.value.settings.revision;
  }
  async function refresh(initial = false) {
    const request = ++refreshRequest;
    const currentRequest = () => alive && request === refreshRequest;
    try {
      const current = await api("/investment");
      if (!currentRequest() || current.settings.revision < (state.value?.settings?.revision || 0)) return;
      const clean = !dirty.value;
      const cleanPeriod = !periodDirty.value;
      state.value = current;
      if (initial || cleanPeriod) {
        startYear.value = current.settings.plan.validation_start_year;
        endYear.value = current.settings.plan.validation_end_year;
      }
      connected.value = true;
      if (!reportMatchesPlan.value || report.value?.start !== `${current.settings.plan.validation_start_year}-01-01` ||
          report.value?.end !== `${current.settings.plan.validation_end_year}-12-31`) report.value = null;
      if (
        initial ||
        !draft.value ||
        (clean && draftRevision.value !== current.settings.revision)
      )
        restoreDraft(initial || !draft.value);
      const key =
        (current.jobs || [])
          .filter(
            (j: any) => j.kind === "portfolio_test" && j.status === "done",
          )
          .map((j: any) => j.id)
          .join(",") + current.settings.revision;
      if (initial || key !== lastReportJob) {
        try {
          const evidence = (await api("/investment/evidence")).report;
          if (!currentRequest()) return;
          if (evidence && (evidence.plan_key !== current.plan_key ||
              evidence.start !== `${current.settings.plan.validation_start_year}-01-01` ||
              evidence.end !== `${current.settings.plan.validation_end_year}-12-31`)) {
            report.value = null;
            reportError.value = "计划已更新，正在等待匹配的验证结果";
            return;
          }
          report.value = evidence;
          reportError.value = "";
          lastReportJob = key;
        } catch (e: any) {
          if (currentRequest()) reportError.value = "验证结果读取失败：" + e.message;
        }
      }
    } catch (e: any) {
      if (!currentRequest()) return;
      connected.value = false;
      if (initial) error.value = e.message;
    } finally {
      if (currentRequest()) loading.value = false;
    }
  }
  async function loop() {
    await refresh();
    if (alive) timer = setTimeout(loop, activeJobs.value.length ? 2500 : 10000);
  }
  async function save() {
    if (busy.value) return false;
    if (!writable.value) {
      showLogin.value = true;
      return false;
    }
    busy.value = true;
    error.value = "";
    try {
      const value = await api("/investment/plan", {
        method: "PUT",
        body: JSON.stringify({
          plan: draft.value,
          revision: draftRevision.value,
          profile_slot: profileSlot.value,
        }),
      });
      // Invalidate reads started before this successful write, including pending evidence.
      ++refreshRequest;
      state.value.settings = value;
      state.value.plan_key = null;
      report.value = null;
      reportError.value = "";
      lastReportJob = "";
      if (state.value.profile_library) {
        state.value.profile_library.active_slot = value.profile_slot;
        state.value.profile_library.entries[value.profile_slot].plan = clone(value.plan);
      }
      delete profileDrafts.value[profileSlot.value];
      restoreDraft(false);
      await refresh();
      notify("当前档位已独立保存并启用，其他档位保持不变");
      return true;
    } catch (e: any) {
      error.value = e.message;
      return false;
    } finally {
      busy.value = false;
    }
  }
  async function savePreferences(values: Partial<{starting_cash: number; auto_update: boolean;
    validation_start_year: number; validation_end_year: number}>, expectedRevision = draftRevision.value) {
    if (busy.value) return false;
    if (!writable.value) { showLogin.value = true; return false; }
    if (draftConflict.value) {
      error.value = '另一设备已修改计划，请先处理或放弃旧草稿，再保存账户设置。';
      return false;
    }
    // Shared account/period changes must never activate the profile being previewed.
    busy.value = true;
    error.value = '';
    try {
      const patch = Object.fromEntries(Object.entries(values).filter(([key]) =>
        ['starting_cash', 'auto_update', 'validation_start_year', 'validation_end_year'].includes(key)));
      const value = await api('/investment/plan', {method:'PUT', body:JSON.stringify({
        plan: {...state.value.settings.plan, ...patch}, revision: expectedRevision,
        profile_slot: state.value.profile_library.active_slot,
      })});
      ++refreshRequest;
      state.value.settings = value;
      state.value.plan_key = null;
      report.value = null;
      lastReportJob = '';
      draftRevision.value = value.revision;
      Object.assign(draft.value, patch);
      saved.value = JSON.stringify({...JSON.parse(saved.value), ...patch});
      for (const buffered of Object.values(profileDrafts.value)) Object.assign(buffered, patch);
      for (const entry of Object.values(state.value.profile_library.entries) as any[]) Object.assign(entry.plan, patch);
      await refresh();
      return true;
    } catch (e: any) { error.value = e.message; return false; }
    finally { busy.value = false; }
  }
  async function run(kind: string, researchMethod?: string, compareModels = false) {
    if (busy.value) return;
    if (!writable.value) {
      showLogin.value = true;
      return;
    }
    if (dirty.value) {
      error.value = "计划有未保存修改，请先保存或撤销。";
      navigate("plan");
      return;
    }
    busy.value = true;
    error.value = "";
    try {
      await api("/investment/run", {
        method: "POST",
        body: JSON.stringify({
          kind,
          research_method: researchMethod,
          compare_models: compareModels,
        }),
      });
      await refresh();
      notify("任务已保存，关闭网页不会取消");
    } catch (e: any) {
      error.value = e.message;
    } finally {
      busy.value = false;
    }
  }
  async function jobAction(job: any, action: string) {
    try {
      await api(`/jobs/${job.id}/${action}`, { method: "POST" });
      await refresh();
    } catch (e: any) {
      error.value = e.message;
    }
  }
  async function loadChart() {
    const request = ++chartRequest;
    const symbol = asset.value, selectedInterval = interval.value;
    const currentRequest = () => alive && request === chartRequest &&
      symbol === asset.value && selectedInterval === interval.value;
    chartBusy.value = true;
    chartData.value = null;
    try {
      const result = await api(`/investment/chart/${symbol}?interval=${selectedInterval}`);
      if (currentRequest()) chartData.value = result;
    } catch (e: any) {
      if (currentRequest()) error.value = e.message;
    } finally {
      if (currentRequest()) chartBusy.value = false;
    }
  }
  async function inspectData() {
    try {
      auditData.value = await api("/investment/data");
    } catch (e: any) {
      error.value = e.message;
    }
  }
  async function login() {
    try {
      await api("/access/login", {
        method: "POST",
        body: JSON.stringify({ key: loginKey.value }),
      });
      writable.value = true;
      showLogin.value = false;
      loginKey.value = "";
    } catch (e: any) {
      error.value = e.message;
    }
  }
  async function reset() {
    if (busy.value) return;
    if (!writable.value) { showReset.value = false; showLogin.value = true; return; }
    busy.value = true;
    try {
      await api("/investment/reset", { method: "POST" });
      showReset.value = false;
      await refresh();
      notify("原记录已归档，下一次更新将开始新的模拟");
    } catch (e: any) {
      error.value = e.message;
    } finally {
      busy.value = false;
    }
  }
  function exportReport() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(report.value, null, 2)], {
        type: "application/json",
      }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `portfolio-${report.value.start}-${report.value.end}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }
  function ratio(key: string, event: Event) {
    draft.value[key] = Number((event.target as HTMLInputElement).value) / 100;
    if (["max_exposure", "max_weight", "drawdown_pause", "drift_band"].includes(key))
      draft.value.profile = "custom";
  }
  watch([asset, interval], () => { if (page.value === 'overview') void loadChart(); });
  watch(page, (id) => {
    window.scrollTo({top:0});
    if (id === 'overview') void loadChart();
  });
  onMounted(async () => {
    document.title = "StockLab · 个人投资";
    hashChanged();
    window.addEventListener("hashchange", hashChanged);
    window.addEventListener('beforeunload', beforeUnload);
    try {
      const access = await api("/access");
      remote.value = access.remote;
      writable.value = access.write_allowed;
    } catch {}
    await refresh(true);
    const handoff = new URLSearchParams(location.search);
    if (state.value && handoff.has('research_job') && handoff.has('research_candidate')) {
      try {
        const imported = await api(`/research-workspace/draft?job_id=${encodeURIComponent(handoff.get('research_job')!)}&candidate_id=${encodeURIComponent(handoff.get('research_candidate')!)}`);
        draft.value = {...clone(imported.plan), name: state.value.settings.plan.name};
        profileSlot.value = 'custom';
        draftRevision.value = state.value.settings.revision;
        navigate('plan');
        notify(`已载入研究草稿「${imported.label}」，尚未保存或启用；原账户与其他档位不变。`);
        history.replaceState(null, '', '/#plan');
      } catch (e: any) { error.value = e.message; }
    }
    if (page.value === 'overview') await loadChart();
    if (alive) timer = setTimeout(loop, 3000);
  });
  onBeforeUnmount(() => {
    alive = false;
    clearTimeout(timer);
    window.removeEventListener("hashchange", hashChanged);
    window.removeEventListener('beforeunload', beforeUnload);
  });

  return {
    tabs,
    page,
    state,
    draft,
    report,
    reportError,
    reportMatchesPlan,
    auditData,
    saved,
    error,
    toast,
    busy,
    connected,
    loading,
    draftRevision,
    profileSlot,
    profileDrafts,
    selectProfileSlot,
    signalName,
    hasProfileEdits,
    asset,
    interval,
    chartData,
    chartBusy,
    startYear,
    endYear,
    periodDirty,
    activeProfileName,
    draftConflict,
    savePreferences,
    remote,
    writable,
    showLogin,
    loginKey,
    showReset,
    jobNames,
    methodNames,
    comparisonFamily,
    comparisonKeys,
    dirty,
    activeJobs,
    resultRows,
    comparisonSeries,
    years,
    yearly,
    name,
    positionRows,
    jobsByKind,
    gainClass,
    totalTarget,
    targetRows,
    summarySeries,
    notify,
    navigate,
    hashChanged,
    restoreDraft,
    refresh,
    loop,
    save,
    run,
    jobAction,
    loadChart,
    inspectData,
    login,
    reset,
    exportReport,
    ratio,
  };
}
export type InvestorWorkspace = ReturnType<typeof createInvestorWorkspace>;
export const investorKey: InjectionKey<InvestorWorkspace> = Symbol("investor");
export function useInvestor() {
  const value = inject(investorKey);
  if (!value) throw new Error("Investment workspace unavailable");
  return value;
}
