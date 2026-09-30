import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { api, clone } from '../api';

export const fieldNames: Record<string, string> = {
  years: '训练窗口（年）', horizon: '预测跨度（交易日）', iterations: '迭代 / 树数量',
  leaves: '最大叶节点', leaf_samples: '叶节点最少样本', regularization: '正则强度',
  window: '序列窗口（交易日）', epochs: '训练轮数',
};
export const fieldHints: Record<string, string> = {
  years: '更长覆盖更多行情，但旧规律可能失效', horizon: '预测未来净持有收益的跨度，不是调仓频率',
  iterations: '更多通常更慢，也可能过拟合', leaves: '更多可拟合更复杂的关系，也更易过拟合',
  leaf_samples: '更多使预测更平滑，减少小样本拟合', regularization: '更大抑制复杂拟合，过大可能欠拟合',
  window: '每个样本包含的历史序列长度', epochs: '重复训练次数；更多不一定泛化更好',
};
export const modelNames: Record<string, string> = {hgb: 'HistGradientBoosting', ridge: 'Ridge', extra: 'Extra Trees', xgb: 'XGBoost', cnn: '1D-CNN'};
export const slotNames: Record<string, string> = {active: '当前启用计划', conservative: '保守', balanced: '均衡', aggressive: '激进', custom: '自定义'};
const memoryKey = 'stocklab-research-draft-v1';
export function createResearchWorkspace() {
  const page = ref('compare'), context = ref<any>(), error = ref(''), busy = ref(false), loading = ref(true);
  const connected = ref(true), writable = ref(true), showLogin = ref(false), loginKey = ref('');
  const slot = ref('active'), recipe = ref('hgb20_regularized');
  const compareRecipes = ref(['rotation', 'hgb20_regularized', 'ridge20']);
  const tuningByRecipe = reactive<Record<string, Record<string, number>>>({});
  const lastYear = new Date().getFullYear() - 1;
  const form = reactive({start_year: lastYear - 4, end_year: lastYear - 1, check_start_year: lastYear, check_end_year: lastYear, budget: 5});
  const jobs = ref<any[]>([]), total = ref(0), offset = ref(0), selected = ref<any>(), detailId = ref('');
  const legacy = ref<any[]>([]), legacyResult = ref<any>();
  const legacyKind = ref('evaluate'), legacyOffset = ref(0), legacyTotal = ref(0);
  let alive = true, hydrated = false, timer: ReturnType<typeof setTimeout>, listSeq = 0, detailSeq = 0;
  const baseline = computed(() => slot.value === 'active'
    ? {plan: context.value?.settings.plan, plan_key: context.value?.plan_key}
    : context.value?.profiles.entries[slot.value]);
  const spec = computed(() => context.value?.recipes[recipe.value]);
  const fields = computed<string[]>(() => context.value?.fields[spec.value?.model] || []);
  const tuning = computed(() => tuningByRecipe[recipe.value] || {});
  const active = computed(() => jobs.value.find(j => ['running', 'queued'].includes(j.status)));
  const canRun = computed(() => baseline.value?.plan?.research_interval === 'daily' && !busy.value);
  function navigate(next: string) { location.hash = next; page.value = next; }
  function hashChanged() {
    const next = location.hash.slice(1);
    page.value = ['compare', 'tune', 'history'].includes(next) ? next : 'compare';
  }
  function initializeTuning() {
    if (!spec.value || tuningByRecipe[recipe.value]) return;
    tuningByRecipe[recipe.value] = Object.fromEntries(fields.value.map(k => [k, spec.value[k]]));
    const plan = baseline.value?.plan;
    if (plan?.model_recipe === recipe.value && plan.model_tuning)
      Object.assign(tuningByRecipe[recipe.value], Object.fromEntries(Object.entries(plan.model_tuning).filter(([, v]) => v != null)));
  }
  function resetTuning() {
    tuningByRecipe[recipe.value] = Object.fromEntries(fields.value.map(k => [k, spec.value[k]]));
  }
  async function reloadContext() {
    context.value = await api('/research-workspace');
    initializeTuning();
  }
  async function selectJob(id: string) {
    detailId.value = id;
    const sequence = ++detailSeq;
    try {
      const result = await api(`/jobs/${id}`);
      if (alive && sequence === detailSeq && id === detailId.value) selected.value = result;
    } catch (e: any) { if (sequence === detailSeq) error.value = e.message; }
  }
  async function refreshJobs() {
    const sequence = ++listSeq;
    try {
      const result = await api(`/jobs?kind=portfolio_research&limit=12&offset=${offset.value}`);
      if (!alive || sequence !== listSeq) return;
      jobs.value = result.items; total.value = result.total; connected.value = true;
      if (!detailId.value && result.items[0]) await selectJob(result.items[0].id);
      else {
        const latest = result.items.find((j: any) => j.id === detailId.value);
        if (latest && (latest.status !== selected.value?.status || latest.result_version !== selected.value?.result_version || latest.updated !== selected.value?.updated))
          await selectJob(latest.id);
      }
    } catch (e: any) { connected.value = false; error.value = e.message; }
  }
  async function paginate(direction: number) { offset.value = Math.max(0, offset.value + direction * 12); await refreshJobs(); }
  async function run() {
    if (busy.value) return;
    if (!writable.value) { showLogin.value = true; return; }
    busy.value = true; error.value = '';
    try {
      const study = {mode: page.value === 'tune' ? 'tune' : 'compare', ...form,
        recipes: page.value === 'tune' ? [recipe.value] : compareRecipes.value,
        recipe: recipe.value, tuning: clone(tuning.value)};
      const job = await api('/research-workspace/run', {method: 'POST', body: JSON.stringify({slot: slot.value, base_plan_key: baseline.value.plan_key, study})});
      offset.value = 0;
      await selectJob(job.id); await refreshJobs(); navigate('history');
    } catch (e: any) { error.value = e.message; }
    finally { busy.value = false; }
  }
  async function jobAction(action: string) {
    if (!writable.value) { showLogin.value = true; return; }
    if (!selected.value) return;
    busy.value = true;
    try { await api(`/jobs/${selected.value.id}/${action}`, {method: 'POST'}); await refreshJobs(); await selectJob(detailId.value); }
    catch (e: any) { error.value = e.message; } finally { busy.value = false; }
  }
  async function login() {
    try { await api('/access/login', {method: 'POST', body: JSON.stringify({key: loginKey.value})}); writable.value = true; showLogin.value = false; loginKey.value = ''; }
    catch (e: any) { error.value = e.message; }
  }
  async function loadLegacy(direction = 0) {
    legacyOffset.value = Math.max(0, legacyOffset.value + direction * 12);
    try {
      const result = await api(`/jobs?kind=${legacyKind.value}&limit=12&offset=${legacyOffset.value}`);
      legacy.value = result.items; legacyTotal.value = result.total;
    }
    catch (e: any) { error.value = e.message; }
  }
  async function inspectLegacy(id: string) {
    try { legacyResult.value = await api(`/jobs/${id}`); }
    catch (e: any) { error.value = e.message; }
  }
  function exportJson(value = selected.value) {
    if (!value) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type: 'application/json'}));
    const a = document.createElement('a'); a.href = url; a.download = `research-${value.id}.json`; a.click(); URL.revokeObjectURL(url);
  }
  function repeat() {
    const study = selected.value?.request?.study;
    if (!study) return;
    Object.assign(form, {start_year: study.start_year, end_year: study.end_year, check_start_year: study.check_start_year, check_end_year: study.check_end_year, budget: study.budget});
    recipe.value = study.recipe; compareRecipes.value = clone(study.recipes);
    tuningByRecipe[study.recipe] = {...Object.fromEntries((context.value.fields[context.value.recipes[study.recipe].model] as string[]).map(k => [k, context.value.recipes[study.recipe][k]])), ...Object.fromEntries(Object.entries(study.tuning).filter(([, v]) => v != null))} as Record<string, number>;
    navigate(study.mode);
  }
  function saveDraft() {
    if (!hydrated) return;
    try { localStorage.setItem(memoryKey, JSON.stringify({form, recipe: recipe.value, compareRecipes: compareRecipes.value, tuningByRecipe, slot: slot.value})); } catch {}
  }
  watch(recipe, initializeTuning);
  watch(legacyKind, () => {legacyOffset.value = 0; legacyResult.value = undefined; loadLegacy();});
  watch([form, recipe, compareRecipes, tuningByRecipe, slot], saveDraft, {deep: true});
  async function loop() { await refreshJobs(); if (alive) timer = setTimeout(loop, active.value ? 2500 : 8000); }
  onMounted(async () => {
    document.title = 'StockLab · 研究工作台'; hashChanged(); window.addEventListener('hashchange', hashChanged);
    try {
      const access = await api('/access'); writable.value = access.write_allowed;
      await reloadContext();
      try {
        const local = JSON.parse(localStorage.getItem(memoryKey) || 'null');
        if (local && context.value.recipes[local.recipe]) {
          Object.assign(form, local.form); Object.assign(tuningByRecipe, local.tuningByRecipe);
          recipe.value = local.recipe; compareRecipes.value = local.compareRecipes; slot.value = slotNames[local.slot] ? local.slot : 'active';
        }
      } catch {}
      initializeTuning(); hydrated = true; await refreshJobs();
    } catch (e: any) { error.value = e.message; connected.value = false; }
    finally { loading.value = false; if (alive) timer = setTimeout(loop, 2500); }
  });
  onBeforeUnmount(() => { alive = false; ++detailSeq; ++listSeq; clearTimeout(timer); window.removeEventListener('hashchange', hashChanged); });
  return {page, context, error, busy, loading, connected, writable, showLogin, loginKey, slot, recipe,
    compareRecipes, form, jobs, total, offset, selected, detailId, baseline, spec, fields, tuning, active, canRun,
    navigate, reloadContext, resetTuning, selectJob, refreshJobs, paginate, run, jobAction, login, exportJson, repeat,
    legacy, legacyResult, legacyKind, legacyOffset, legacyTotal, loadLegacy, inspectLegacy};
}
