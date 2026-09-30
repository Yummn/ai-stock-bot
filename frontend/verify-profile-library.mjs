import assert from 'node:assert/strict';
import path from 'node:path';
import { chromium, request, expect } from '@playwright/test';

// This fixture writes only to the isolated server created by profile_ui_test_server.py.
const base = 'http://127.0.0.1:8773';
const http = await request.newContext({ baseURL: base });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const errors = [];
const read = async () => (await (await http.get('/api/v2/investment')).json());
async function write(slot, plan) {
  const state = await read();
  const response = await http.put('/api/v2/investment/plan', { data: { profile_slot: slot, plan, revision: state.settings.revision } });
  assert.equal(response.status(), 200, await response.text());
}
async function seed() {
  const state = await read();
  assert.equal(state.profile_algorithm_study.study, 'profile-algorithms-20260930');
  for (const slot of ['conservative', 'balanced', 'aggressive']) {
    const { label, ...preset } = state.profiles[slot];
    await write(slot, { ...state.settings.plan, ...preset, profile: slot, preset_version: 1 });
  }
}
const choose = (page, name) => page.getByRole('group', { name: '风险方案' }).getByRole('button', { name, exact: true }).click();
const edit = page => page.getByRole('button', { name: '调整本档', exact: true }).click();
async function save(page, label) {
  const response = page.waitForResponse(r => r.url().endsWith('/investment/plan') && r.request().method() === 'PUT');
  await page.getByRole('button', { name: `保存并使用${label}`, exact: true }).click();
  const result = await response;
  assert.equal(result.status(), 200, await result.text());
  await expect(page.getByRole('button', { name: `保存并使用${label}`, exact: true })).toBeDisabled();
}
try {
  await seed();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(base + '/#plan');
  await page.getByRole('region', { name: '固定方案摘要' }).waitFor();
  assert.equal(await page.locator('select:visible').count(), 0);
  await choose(page, '均衡');
  await edit(page);
  await page.getByRole('radio', { name: /趋势组合/ }).check();
  await save(page, '均衡');
  let state = await read();
  assert.equal(state.profile_library.entries.balanced.plan.method, 'rotation');
  assert.equal(state.profile_library.entries.aggressive.plan.model_recipe, 'hgb20_regularized');
  assert.equal(state.profile_library.entries.conservative.plan.model_recipe, 'hgb20_regularized');
  await choose(page, '激进');
  await edit(page);
  await page.getByLabel('机器学习模型', { exact: true }).selectOption('xgb');
  await page.getByTitle('打开模型参数设置').click();
  await page.getByLabel('模型配方', { exact: true }).selectOption('xgb20_smooth');
  await expect(page.getByLabel('正则化强度', { exact: true })).toHaveValue('50');
  await page.getByRole('button', { name: '完成', exact: true }).click();
  await save(page, '激进');
  await page.reload();
  await page.getByRole('region', { name: '固定方案摘要' }).waitFor();
  await choose(page, '均衡');
  await expect(page.getByRole('region', { name: '固定方案摘要' })).toContainText('趋势分散组合');
  await edit(page);
  await page.getByRole('radio', { name: /机器学习组合/ }).check();
  await page.getByLabel('机器学习模型', { exact: true }).selectOption('ridge');
  await choose(page, '保守');
  await choose(page, '均衡');
  await edit(page);
  await expect(page.getByLabel('机器学习模型', { exact: true })).toHaveValue('ridge');
  assert.equal((await read()).profile_library.entries.balanced.plan.method, 'rotation');
  // A second device writes a different slot while the first has an unsaved draft.
  state = await read();
  await write('conservative', { ...state.profile_library.entries.conservative.plan, model_recipe: 'extra20' });
  const failed = page.waitForResponse(r => r.url().endsWith('/investment/plan') && r.request().method() === 'PUT');
  await page.getByRole('button', { name: '保存并使用均衡', exact: true }).click();
  assert.equal((await failed).status(), 409);
  state = await read();
  assert.equal(state.profile_library.entries.balanced.plan.method, 'rotation');
  assert.equal(state.profile_library.entries.aggressive.plan.model_recipe, 'xgb20_smooth');
  assert.equal(state.profile_library.entries.conservative.plan.model_recipe, 'extra20');
  await page.close();
  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  mobile.on('pageerror', e => errors.push(e.message));
  await mobile.goto(base + '/#plan');
  await mobile.getByRole('region', { name: '固定方案摘要' }).waitFor();
  await expect(mobile.getByRole('group', { name: '风险方案' }).getByRole('button', { name: '保守', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await choose(mobile, '均衡');
  await expect(mobile.getByRole('region', { name: '固定方案摘要' })).toContainText('趋势分散组合');
  await choose(mobile, '激进');
  await expect(mobile.getByRole('region', { name: '固定方案摘要' })).toContainText('XGBoost');
  await mobile.close();
  await seed();
  for (const [device, viewport] of [['desktop', { width: 1440, height: 1000 }], ['mobile', { width: 390, height: 844 }]]) {
    const view = await browser.newPage({ viewport });
    view.on('pageerror', e => errors.push(e.message));
    await view.goto(base + '/#plan');
    await view.getByRole('region', { name: '固定方案摘要' }).waitFor();
    await choose(view, '均衡');
    const study = view.locator('.profile-study');
    assert.equal(await study.getAttribute('open'), null);
    await study.locator(':scope > summary').click();
    assert.equal(await view.getByRole('table', { name: '均衡算法选择对照', exact: true }).getByRole('row').count(), 7);
    await view.getByText('参数复测明细', { exact: true }).click();
    assert.equal(await view.getByRole('table', { name: '均衡参数复测', exact: true }).getByRole('row').count(), 13);
    assert.equal(await view.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    if (device === 'mobile') assert.equal(await study.locator('.table-scroll').first().evaluate(e => e.scrollWidth > e.clientWidth), true);
    await view.evaluate(() => scrollTo(0, 0));
    await expect.poll(() => view.evaluate(() => scrollY)).toBe(0);
    await view.screenshot({ path: path.resolve(`../docs/reports/profile-algorithms-20260930/ui-library-${device}.png`), fullPage: true });
    await view.close();
  }
  assert.deepEqual(errors, []);
  console.log('PASS independent slots, models/parameters, reload, cross-device, draft retention, revision conflicts, desktop/mobile layout.');
} finally {
  await browser.close();
  await http.dispose();
}
