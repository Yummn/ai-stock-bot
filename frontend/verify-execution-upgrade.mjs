import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { chromium, request } from '@playwright/test';

const base = 'http://127.0.0.1:8772';
const http = await request.newContext({ baseURL: base });
const before = await (await http.get('/api/v2/investment')).json();
const evidence = (await (await http.get('/api/v2/investment/evidence')).json()).report;
const research = (await (await http.get('/api/v2/investment/research-combinations')).json()).report;
assert.equal(before.settings.plan.model_recipe, 'hgb20_regularized');
assert.equal(before.settings.plan.execution_policy, 'cost_aware');
assert.equal(before.plan_key, evidence.plan_key);
assert.equal(evidence.complete, true);
assert.equal(before.live.orders_enabled, false);
assert.deepEqual(research.execution_upgrades.map(r => r.replays), [118, 62]);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const errors = [], mutations = [];
try {
  for (const [device, viewport] of [['desktop', { width: 1440, height: 1000 }], ['mobile', { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport });
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') mutations.push(r.url()); });
    await page.goto(base + '/#plan');
    await page.getByRole('group', { name: '风险方案' }).getByRole('button', { name: '自定义', exact: true }).click();
    await page.getByRole('tab', { name: '计划概览', exact: true }).click();
    await page.getByTitle('打开模型参数设置').click();
    const dialog = page.getByRole('dialog', { name: '模型参数设置' });
    assert.equal(await dialog.getByLabel('梯度树配置').inputValue(), 'hgb20_regularized');
    assert.equal(await dialog.getByLabel('预测持有期 / 交易日').inputValue(), '20');
    assert.equal(await dialog.getByLabel('每棵树最多叶子').isDisabled(), true);
    await dialog.screenshot({ path: path.join(os.tmpdir(), `stocklab-recipe-${device}.png`) });
    await dialog.getByLabel('梯度树配置').selectOption('legacy');
    assert.equal(await dialog.getByLabel('每棵树最多叶子').isDisabled(), false);
    await page.reload();
    await page.getByRole('group', { name: '风险方案' }).getByRole('button', { name: '自定义', exact: true }).click();
    await page.getByRole('tab', { name: '详细设置', exact: true }).click();
    assert.equal(await page.getByLabel('执行规则').inputValue(), 'cost_aware');
    await page.getByLabel('执行规则').selectOption('corrected');
    await page.getByText('已有结果只对应已保存的版本', { exact: false }).waitFor();
    await page.reload();
    await page.getByRole('group', { name: '风险方案' }).getByRole('button', { name: '自定义', exact: true }).click();
    await page.getByRole('tab', { name: '详细设置', exact: true }).click();
    assert.equal(await page.getByLabel('执行规则').inputValue(), 'cost_aware');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await page.goto(base + '/#validation');
    await page.getByRole('tab', { name: '历史验证', exact: true }).click();
    const outer = page.locator('.research-section');
    await outer.locator('summary').first().click();
    await outer.getByLabel('研究方案').waitFor();
    for (const [i, study] of research.execution_upgrades.entries()) {
      const section = outer.locator('.execution-study').nth(i);
      await section.locator('summary').click();
      assert.equal(await section.locator('tbody tr').count(), study.rows.length);
      const selector = section.locator('select');
      for (const period of ['continuous', 'later', 'partial']) {
        await selector.selectOption(period);
        const first = study.rows[0].periods[period].base;
        const value = (first[period === 'partial' ? 'liquidation_return' : 'liquidation_cagr'] * 100).toFixed(2);
        assert.ok((await section.locator('tbody tr').first().locator('td').nth(1).innerText()).includes(value));
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      await section.scrollIntoViewIfNeeded();
      await section.screenshot({ path: path.join(os.tmpdir(), `stocklab-execution-${i}-${device}.png`) });
      await section.locator('summary').click();
    }
    await page.close();
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(mutations, []);
  const after = await (await http.get('/api/v2/investment')).json();
  assert.deepEqual(after.settings, before.settings);
  console.log('PASS desktop/mobile: actual saved recipe, disabled parameters, dirty warning, reload persistence, matching evidence, 180 replays, period metrics, no overflow, no writes.');
} finally {
  await browser.close();
  await http.dispose();
}
