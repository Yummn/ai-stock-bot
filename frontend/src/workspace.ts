import { reactive, watch } from "vue";
import { api, clone } from "./api";

export const methods = [
  {
    key: "hist_gradient_boosting",
    name: "梯度提升树",
    code: "HistGradientBoosting",
    type: "ml",
    detail: "逐轮修正预测误差",
  },
  {
    key: "random_forest",
    name: "随机森林",
    code: "RandomForest",
    type: "ml",
    detail: "多棵决策树共同判断",
  },
  {
    key: "xgboost",
    name: "XGBoost",
    code: "XGBoost",
    type: "ml",
    detail: "带正则约束的提升树",
  },
  {
    key: "mlp",
    name: "神经网络",
    code: "MLP",
    type: "ml",
    detail: "学习指标之间的组合关系",
  },
  {
    key: "cnn",
    name: "时序卷积网络",
    code: "1D-CNN",
    type: "ml",
    detail: "学习一段历史中的价格形态",
  },
  {
    key: "local",
    name: "规则策略",
    code: "Local",
    type: "local",
    detail: "按明确的技术指标规则判断",
  },
  {
    key: "ai",
    name: "AI 分析",
    code: "Codex / OpenAI",
    type: "ai",
    detail: "由大模型解读当前指标",
  },
];
export const profileNotes: Record<string, string> = {
  conservative: "优先控制下跌风险",
  balanced: "兼顾收益与回撤",
  aggressive: "争取更多上涨机会",
  custom: "独立保存的实验参数",
};
export const modelName = (c: any) =>
  methods.find((m) => m.key === (c?.engine === "ml" ? c.model : c?.engine))
    ?.name || "";
export const selectionName = (c: any) =>
  !c?.auto_profile && c?.profile_selection !== "trend" && c?.profile_selection !== "recent"
    ? "固定档位"
    : c?.profile_selection === "recent" && c?.engine === "ml"
      ? "按近三个月模拟成绩选档"
      : "按价格走势选档";
export const preset = (c: any) =>
  c.engine === "ml" ? c.models[c.model][c.profile] : c[c.engine][c.profile];
export const taskName = (kind: string) =>
  ({
    evaluate: "跨年评估",
    backtest: "单段回测",
    learn: "参数优化",
    paper: "模拟运行",
    refresh: "更新行情",
    research: "热点研究",
  })[kind] || kind;

export function taskStyle(job: any, catalog: any) {
  if (job.kind === "research") return "公开资料 · 当前市场";
  const c = job.request?.config || job.summary;
  if (job.kind === "learn") {
    const profiles = job.request?.profiles || job.summary?.profiles;
    return profiles?.length
      ? profiles.map((p: string) => catalog.profiles[p]).join("、")
      : "多档独立优化";
  }
  return c.auto_profile ? selectionName(c) : catalog.profiles[c.profile];
}

export function createWorkspace() {
  const w = reactive<any>({
    active: null,
    draft: null,
    catalog: null,
    revision: 0,
    overview: null,
    market: [],
    jobs: [],
    selected: null,
    loading: true,
    busy: false,
    online: true,
    upgradeRequired: false,
    serverVersion: "",
    capabilities: [],
    error: "",
    notice: "",
    view: "account",
    editor: "",
    experimentMode: "evaluate",
    experimentSource: "active",
    access: { remote: false, write_allowed: true },
    accessDialog: false,
    evidence: null,
    evidenceLoading: false,
    profilePreview: null,
    profilePreviewLoading: false,
    externalChange: false,
  });
  let poll: ReturnType<typeof setInterval>,
    timer: ReturnType<typeof setTimeout>,
    polling = false,
    evidenceSequence = 0,
    previewSequence = 0,
    tickCount = 0;
  const dirty = () => JSON.stringify(w.active) !== JSON.stringify(w.draft);
  const flash = (message: string) => {
    w.notice = message;
    clearTimeout(timer);
    timer = setTimeout(() => (w.notice = ""), 6500);
  };
  const act = async (fn: () => Promise<any>) => {
    if (w.busy) return;
    w.busy = true;
    w.error = "";
    try {
      return await fn();
    } catch (e: any) {
      w.error = e.message;
    } finally {
      w.busy = false;
    }
  };
  function navigate(view: string) {
    w.view = view;
    location.hash = view;
    window.scrollTo({ top: 0 });
  }
  function readHash() {
    const key = location.hash.slice(1);
    w.view =
      (
        {
          overview: "account",
          settings: "strategy",
          test: "experiments",
          learn: "experiments",
        } as any
      )[key] ||
      (["account", "strategy", "experiments", "data"].includes(key)
        ? key
        : "account");
    if (key === "learn") w.experimentMode = "learn";
  }
  async function refreshJobs() {
    w.jobs = (await api("/jobs?limit=30")).items;
  }
  async function refreshOverview() {
    w.overview = await api("/overview");
  }
  async function refreshMarket() {
    w.market = (await api("/market")).items;
  }
  async function refreshEvidence() {
    const sequence = ++evidenceSequence;
    if (!w.draft) return;
    w.evidenceLoading = true;
    try {
      const result = await api(
        "/evidence",
        w.access.write_allowed
          ? { method: "POST", body: JSON.stringify(w.draft) }
          : {},
      );
      if (sequence === evidenceSequence) w.evidence = result;
    } catch (e: any) {
      if (sequence === evidenceSequence)
        w.evidence = { matched: false, error: e.message };
    } finally {
      if (sequence === evidenceSequence) w.evidenceLoading = false;
    }
  }
  function invalidateEvidence() {
    evidenceSequence++;
    w.evidence = null;
    w.evidenceLoading = true;
  }
  async function refreshProfilePreview() {
    const sequence = ++previewSequence;
    if (!w.draft || (!w.draft.auto_profile && w.draft.profile_selection !== "trend" && w.draft.profile_selection !== "recent")) {
      w.profilePreview = null;
      w.profilePreviewLoading = false;
      return;
    }
    if (!w.capabilities.includes("profile_selection_preview")) {
      w.profilePreview = { rows: [], error: "请重启网页服务以查看自动选档" };
      w.profilePreviewLoading = false;
      return;
    }
    w.profilePreviewLoading = true;
    try {
      const result = await api(
        "/profile-selection/preview",
        w.access.write_allowed
          ? { method: "POST", body: JSON.stringify(w.draft) }
          : {},
      );
      if (sequence === previewSequence) w.profilePreview = result;
    } catch (e: any) {
      if (sequence === previewSequence) w.profilePreview = { rows: [], error: e.message };
    } finally {
      if (sequence === previewSequence) w.profilePreviewLoading = false;
    }
  }
  function invalidateProfilePreview() {
    previewSequence++;
    w.profilePreview = null;
    w.profilePreviewLoading = true;
  }
  async function reload() {
    return act(async () => {
      const s = await api("/settings");
      w.active = clone(s.config);
      w.draft = clone(s.config);
      w.revision = s.revision;
      w.externalChange = false;
      flash("已载入当前启用方案");
      await refreshEvidence();
      await refreshProfilePreview();
    });
  }
  async function save() {
    return act(async () => {
      const s = await api("/settings", {
        method: "PUT",
        body: JSON.stringify({ config: w.draft, revision: w.revision }),
      });
      w.active = clone(s.config);
      w.draft = clone(s.config);
      w.revision = s.revision;
      w.externalChange = false;
      flash("已保存并启用，下一次模拟将使用这组参数");
      await refreshOverview();
      await refreshEvidence();
      await refreshProfilePreview();
      return s;
    });
  }
  async function selectJob(id: string) {
    return act(async () => {
      w.selected = await api(`/jobs/${id}`);
      localStorage.setItem("stocklab-workbench-job", id);
      w.experimentMode =
        w.selected.kind === "learn"
          ? "learn"
          : w.selected.kind === "backtest"
            ? "backtest"
            : "evaluate";
      navigate("experiments");
    });
  }
  async function submit(kind: string, options: any = {}) {
    return act(async () => {
      const config =
        kind === "paper"
          ? undefined
          : clone(w.experimentSource === "draft" ? w.draft : w.active);
      if (config && options.period === "random14")
        config.seed = Math.floor(Math.random() * 1000000);
      const j = await api("/jobs", {
        method: "POST",
        body: JSON.stringify({
          kind,
          ...options,
          ...(config ? { config } : {}),
        }),
      });
      if (["evaluate", "backtest", "learn"].includes(kind)) {
        w.selected = await api(`/jobs/${j.id}`);
        localStorage.setItem("stocklab-workbench-job", j.id);
        navigate("experiments");
      }
      await refreshJobs();
      flash("任务已开始，进度和结果保存在本机");
    });
  }
  async function control(command: string) {
    return act(async () => {
      await api(`/jobs/${w.selected.id}/${command}`, { method: "POST" });
      w.selected = await api(`/jobs/${w.selected.id}`);
      await refreshJobs();
    });
  }
  function loadSuggestion(profile: string) {
    const c = w.selected.result.best[profile].config;
    w.draft.models[c.model][profile] = clone(c.models[c.model][profile]);
    w.draft.engine = "ml";
    w.draft.model = c.model;
    w.draft.profile = profile;
    navigate("strategy");
    flash("建议已载入编辑方案");
    void refreshEvidence();
  }
  function testDraft() {
    w.experimentSource = "draft";
    w.experimentMode = w.draft.engine === "ai" ? "backtest" : "evaluate";
    w.selected = null;
    navigate("experiments");
  }
  async function tick() {
    if (polling || document.hidden || w.loading) return;
    polling = true;
    try {
      const wasActive = w.jobs.some((j: any) =>
        ["queued", "running"].includes(j.status),
      );
      const wasPaper = w.jobs.some((j: any) =>
        j.kind === "paper" && ["queued", "running"].includes(j.status),
      );
      await refreshJobs();
      if (wasPaper && !w.jobs.some((j: any) =>
        j.kind === "paper" && ["queued", "running"].includes(j.status)) && w.view === "strategy")
        await refreshProfilePreview();
      if (w.selected && ["queued", "running"].includes(w.selected.status)) {
        const status = await api(`/jobs/${w.selected.id}/status`);
        w.selected =
          status.result_version !== w.selected.result_version ||
          !["queued", "running"].includes(status.status)
            ? await api(`/jobs/${w.selected.id}`)
            : { ...w.selected, ...status };
      }
      if (wasActive) {
        await refreshOverview();
        if (!w.jobs.some((j: any) => ["queued", "running"].includes(j.status)))
          await refreshMarket();
      }
      if (++tickCount % 5 === 0) {
        const latest = await api("/settings");
        if (latest.revision !== w.revision) {
          const editing = dirty();
          w.active = clone(latest.config);
          if (editing) {
            w.externalChange = true;
          } else {
            w.draft = clone(latest.config);
            w.revision = latest.revision;
            await refreshEvidence();
          }
        }
      }
      w.online = true;
    } catch {
      w.online = false;
    } finally {
      polling = false;
    }
  }
  async function init() {
    w.loading = true;
    w.error = "";
    w.upgradeRequired = false;
    readHash();
    try {
      const [catalog, s, overview, access, health] = await Promise.all([
        api("/catalog"),
        api("/settings"),
        api("/overview"),
        api("/access"),
        api("/health"),
      ]);
      w.serverVersion = health.version || "unknown";
      w.capabilities = health.capabilities || [];
      if (!/^[34]\./.test(w.serverVersion)) {
        w.upgradeRequired = true;
        w.loading = false;
        return;
      }
      Object.assign(w, {
        catalog,
        active: clone(s.config),
        draft: clone(s.config),
        revision: s.revision,
        overview,
        access,
      });
      const draft =
        localStorage.getItem("stocklab-workbench-draft") ||
        localStorage.getItem("stocklab-draft");
      if (draft) {
        try {
          const d = JSON.parse(draft);
          if (d.revision === s.revision) {
            w.draft = d.config;
            flash("已恢复未启用的编辑草稿");
          }
        } catch {
          localStorage.removeItem("stocklab-workbench-draft");
        }
      }
      await refreshJobs();
      const id =
        localStorage.getItem("stocklab-workbench-job") ||
        w.jobs.find((j: any) =>
          ["evaluate", "learn", "backtest"].includes(j.kind),
        )?.id;
      if (id) {
        try {
          w.selected = await api(`/jobs/${id}`);
        } catch {
          localStorage.removeItem("stocklab-workbench-job");
        }
      }
      w.loading = false;
      void refreshMarket().catch((e: any) => (w.error = e.message));
      void refreshEvidence();
      poll = setInterval(tick, 4000);
      window.addEventListener("hashchange", readHash);
    } catch (e: any) {
      w.error = e.message;
      w.online = false;
      w.loading = false;
    }
  }
  watch(
    () => w.draft,
    () => {
      if (w.draft && dirty())
        localStorage.setItem(
          "stocklab-workbench-draft",
          JSON.stringify({ revision: w.revision, config: w.draft }),
        );
      else localStorage.removeItem("stocklab-workbench-draft");
    },
    { deep: true },
  );
  return Object.assign(w, {
    dirty,
    flash,
    act,
    navigate,
    refreshJobs,
    refreshOverview,
    refreshMarket,
    refreshEvidence,
    invalidateEvidence,
    refreshProfilePreview,
    invalidateProfilePreview,
    reload,
    save,
    selectJob,
    submit,
    control,
    loadSuggestion,
    testDraft,
    init,
    dispose() {
      clearInterval(poll);
      clearTimeout(timer);
      window.removeEventListener("hashchange", readHash);
    },
  });
}
