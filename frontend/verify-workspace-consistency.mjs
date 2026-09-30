import assert from 'node:assert/strict';
import { chromium, request, expect } from '@playwright/test';

// Use the isolated fixture server; all mutations below are intercepted, never persisted.
const base = 'http://127.0.0.1:8773';
const ui = 'http://127.0.0.1:8873';
const http = await request.newContext({ baseURL: base });
const initial = await (await http.get('/api/v2/investment')).json();
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const errors = [];
const gate = () => {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release };
};
const evidence = state => ({
  id: 'fixture-only', complete: true, plan_key: state.plan_key, plan: state.settings.plan,
  start: `${state.settings.plan.validation_start_year}-01-01`,
  end: `${state.settings.plan.validation_end_year}-12-31`, results: {},
});
async function fixture(viewport) {
  const page = await browser.newPage({ viewport });
  page.on('pageerror', e => errors.push(e.message));
  let current = structuredClone(initial);
  let readState = () => current;
  let readEvidence = () => ({ report: evidence(current) });
  let readChart = symbol => ({ symbol, name: symbol, points: [
    { date: '2025-01-02', close: 3 }, { date: '2025-01-03', close: 4 },
  ] });
  let writes = 0;
  await page.route('**/api/v2/**', async route => {
    const url = new URL(route.request().url());
    const fulfill = json => route.fulfill({ json });
    if (url.pathname.endsWith('/investment') && route.request().method() === 'GET') {
      return fulfill(await readState());
    }
    if (url.pathname.endsWith('/investment/evidence')) {
      const result = await readEvidence();
      return result === 'failed' ? route.fulfill({ status: 503, json: { error: 'fixture unavailable' } }) : fulfill(result);
    }
    if (url.pathname.includes('/investment/chart/')) return fulfill(await readChart(url.pathname.split('/').at(-1)));
    if (url.pathname.endsWith('/investment/plan') && route.request().method() === 'PUT') {
      writes++;
      const value = route.request().postDataJSON();
      current = { ...current, plan_key: 'changed-fixture-plan', settings: {
        plan: value.plan, revision: current.settings.revision + 1, profile_slot: value.profile_slot,
      } };
      current.profile_library.active_slot = value.profile_slot;
      current.profile_library.entries[value.profile_slot].plan = value.plan;
      return fulfill(current.settings);
    }
    if (route.request().method() !== 'GET') throw new Error('Unexpected mutation: ' + url.pathname);
    return route.fulfill({ response: await http.get(url.pathname + url.search) });
  });
  await page.goto(ui + '/#plan');
  await expect(page.locator('.plan-status strong')).toHaveText('当前计划已有历史验证');
  await page.evaluate(() => {
    const instance = document.querySelector('.investor-shell').__vueParentComponent;
    const key = Object.getOwnPropertySymbols(instance.provides).find(k => k.description === 'investor');
    window.testWorkspace = instance.provides[key];
  });
  const refresh = () => page.evaluate(() => window.testWorkspace.refresh());
  const inspect = () => page.evaluate(() => {
    const w = window.testWorkspace;
    return { revision: w.state.value.settings.revision, planKey: w.state.value.plan_key,
      report: w.report.value, reportError: w.reportError.value, connected: w.connected.value,
      chart: w.chartData.value, chartBusy: w.chartBusy.value };
  });
  return { page, refresh, inspect, get current() { return current; }, set current(v) { current = v; },
    set readState(fn) { readState = fn; }, set readEvidence(fn) { readEvidence = fn; },
    set readChart(fn) { readChart = fn; }, get writes() { return writes; } };
}
try {
  for (const [device, viewport] of [['desktop', { width: 1440, height: 1000 }], ['mobile', { width: 390, height: 844 }]]) {
    const f = await fixture(viewport);
    const { page } = f;
    // Delayed old state must not win against a later refresh.
    const old = structuredClone(f.current), held = gate(), seen = gate();
    f.readState = async () => { seen.release(); await held.promise; return old; };
    const pending = f.refresh();
    await seen.promise;
    f.current = { ...f.current, settings: { ...f.current.settings, revision: old.settings.revision + 1 }, plan_key: 'new-fixture' };
    f.readState = () => f.current;
    await f.refresh();
    held.release();
    await pending;
    assert.equal((await f.inspect()).revision, f.current.settings.revision);
    assert.equal((await f.inspect()).report.plan_key, 'new-fixture');

    // The overview may already be updated while an older evidence request is pending.
    f.current = { ...f.current, settings: { ...f.current.settings, revision: f.current.settings.revision + 1 } };
    const oldEvidence = evidence(f.current), evidenceHeld = gate(), evidenceSeen = gate();
    f.readEvidence = async () => { evidenceSeen.release(); await evidenceHeld.promise; return { report: oldEvidence }; };
    const delayedEvidence = f.refresh();
    await evidenceSeen.promise;
    f.current = { ...f.current, settings: { ...f.current.settings, revision: f.current.settings.revision + 1 }, plan_key: 'evidence-fixture' };
    f.readEvidence = () => ({ report: evidence(f.current) });
    await f.refresh();
    evidenceHeld.release();
    await delayedEvidence;
    assert.equal((await f.inspect()).report.plan_key, 'evidence-fixture');

    // Evidence failures after a plan change cannot relabel the old report as current.
    f.current = { ...f.current, settings: { ...f.current.settings, revision: f.current.settings.revision + 1 }, plan_key: 'newer-fixture' };
    f.readEvidence = () => 'failed';
    await f.refresh();
    assert.equal((await f.inspect()).report, null);
    assert.equal((await f.inspect()).connected, true);
    await expect(page.getByRole('button', { name: '重试读取结果' })).toBeVisible();
    await expect(page.locator('.plan-status strong')).not.toHaveText('当前计划已有历史验证');
    f.readEvidence = () => ({ report: evidence(f.current) });
    await page.getByRole('button', { name: '重试读取结果' }).click();
    await expect(page.locator('.plan-status strong')).toHaveText('当前计划已有历史验证');

    // A mismatched response is rejected even if the overview is temporarily unchanged.
    f.current = { ...f.current, settings: { ...f.current.settings, revision: f.current.settings.revision + 1 } };
    f.readEvidence = () => ({ report: evidence(old) });
    await f.refresh();
    assert.equal((await f.inspect()).report, null);
    f.readEvidence = () => ({ report: evidence(f.current) });
    await f.refresh();

    // A pre-save response arriving after the PUT must not roll the revision back.
    const preSave = structuredClone(f.current), heldSave = gate(), seenSave = gate();
    f.readState = async () => { seenSave.release(); await heldSave.promise; return preSave; };
    const stale = f.refresh();
    await seenSave.promise;
    f.readState = () => f.current;
    await page.evaluate(async () => {
      const w = window.testWorkspace;
      w.draft.value.name = 'fixture updated plan';
      await w.save();
    });
    heldSave.release();
    await stale;
    assert.equal(f.writes, 1);
    assert.equal((await f.inspect()).revision, preSave.settings.revision + 1);
    assert.equal((await f.inspect()).report.plan_key, 'changed-fixture-plan');

    // Switching to a same-parameter but different slot must not silently activate on validation.
    await page.evaluate(async () => {
      const w = window.testWorkspace;
      w.state.value.profile_library.entries.custom.plan = JSON.parse(JSON.stringify(w.state.value.settings.plan));
      w.selectProfileSlot('custom');
      w.navigate('validation');
    });
    await expect(page.locator('.validation-plan strong')).toHaveText('投资计划有未保存修改');
    assert.equal(f.writes, 1);

    // Delayed chart A must not replace newer chart B under B's label.
    await page.evaluate(() => window.testWorkspace.navigate('overview'));
    const chartHeld = gate(), chartSeen = gate();
    f.readChart = async symbol => {
      if (symbol === '510500.SH') { chartSeen.release(); await chartHeld.promise; }
      return { symbol, name: symbol, points: [
        { date: '2025-01-02', close: 3 }, { date: symbol === '518880.SH' ? '2025-01-07' : '2025-01-06', close: 4 },
      ] };
    };
    await page.getByLabel('行情标的').selectOption('510500.SH');
    await chartSeen.promise;
    await page.getByLabel('行情标的').selectOption('518880.SH');
    await expect.poll(async () => (await f.inspect()).chart?.symbol).toBe('518880.SH');
    chartHeld.release();
    await expect.poll(async () => (await f.inspect()).chartBusy).toBe(false);
    await expect(page.getByText(/2025-01-02 至 2025-01-07/)).toBeVisible();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await page.screenshot({ path: `../docs/reports/profile-algorithms-20260930/ui-consistency-${device}.png`, fullPage: true });
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log('PASS desktop/mobile: out-of-order state/evidence/charts, save races, evidence retry, explicit slot activation, no page overflow.');
} finally {
  await browser.close();
  await http.dispose();
}
