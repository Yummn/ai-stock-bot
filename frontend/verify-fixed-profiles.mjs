import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { chromium, request } from '@playwright/test';

const base = 'http://127.0.0.1:8772';
const http = await request.newContext({ baseURL: base });
const before = await (await http.get('/api/v2/investment')).json();
assert.equal(before.settings.plan.profile, 'aggressive');
assert.equal(before.settings.plan.preset_version, 1);
const evidence = (await (await http.get('/api/v2/investment/evidence')).json()).report;
assert.equal(before.plan_key, evidence.plan_key);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const errors = [], writes = [];
try {
  for (const [device, viewport] of [['desktop', { width: 1440, height: 1000 }], ['mobile', { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport });
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') writes.push(r.url()); });
    await page.goto(base + '/#plan');
    const buttons = page.getByRole('group', { name: '风险方案' });
    const summary = page.getByRole('region', { name: '固定方案摘要' });
    await summary.waitFor();
    for (const [label, multiple, vol] of [['保守', '1x', '4.00%'], ['均衡', '1x', '10.00%'], ['激进', '2x', '30.00%']]) {
      await buttons.getByRole('button', { name: label, exact: true }).click();
      assert.equal(await buttons.getByRole('button', { name: label, exact: true }).getAttribute('aria-pressed'), 'true');
      assert.ok((await summary.innerText()).includes(multiple));
      assert.ok((await summary.innerText()).includes(vol));
      assert.equal(await page.locator('select:visible').count(), 0);
      assert.equal(await page.getByRole('tab', { name: '详细设置' }).count(), 0);
    }
    await page.screenshot({ path: path.join(os.tmpdir(), `stocklab-fixed-${device}.png`), fullPage: true });
    await buttons.getByRole('button', { name: '自定义', exact: true }).click();
    assert.equal(await summary.count(), 0);
    assert.equal(await page.getByLabel('目标年化波动', { exact: true }).inputValue(), '30');
    await page.getByRole('tab', { name: '计划概览', exact: true }).click();
    assert.equal(await page.getByLabel('目标仓位倍数').inputValue(), '2');
    await page.getByTitle('打开模型参数设置').click();
    assert.equal(await page.getByLabel('梯度树配置').inputValue(), 'hgb20_regularized');
    await page.getByRole('button', { name: '关闭模型参数设置', exact: true }).click();
    await buttons.getByRole('button', { name: '保守', exact: true }).click();
    await page.reload();
    await summary.waitFor();
    assert.equal(await buttons.getByRole('button', { name: '激进', exact: true }).getAttribute('aria-pressed'), 'true');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await page.close();
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
  assert.deepEqual((await (await http.get('/api/v2/investment')).json()).settings, before.settings);
  console.log('PASS desktop/mobile preset selection, hidden controls, complete risk reset, custom editor, reload, matching evidence, no overflow or mutations.');
} finally {
  await browser.close();
  await http.dispose();
}
