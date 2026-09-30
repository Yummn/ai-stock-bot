import assert from 'node:assert/strict';
import path from 'node:path';
import { chromium, request } from '@playwright/test';

const base = 'http://127.0.0.1:8772';
const http = await request.newContext({ baseURL: base });
const before = await (await http.get('/api/v2/investment')).json();
const custom = before.profile_library.entries.custom.plan;
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const errors = [], writes = [];
try {
  for (const [device, viewport] of [['desktop', { width: 1440, height: 1000 }], ['mobile', { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport });
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') writes.push(r.url()); });
    await page.goto(base + '/#plan');
    const buttons = page.getByRole('group', { name: '风险方案' });
    for (const [key, label] of [['conservative', '保守'], ['balanced', '均衡']]) {
      const evidence = before.profile_evidence[key];
      assert.equal(evidence.historical_passed, true);
      assert.equal(evidence.forward_proven, false);
      await buttons.getByRole('button', { name: label, exact: true }).click();
      const details = page.locator('.preset-evidence');
      await details.waitFor();
      if (await details.getAttribute('open') !== null) await details.locator('summary').click();
      assert.equal(await page.locator('select:visible').count(), 0);
      assert.ok((await page.getByRole('region', { name: '固定方案摘要' }).innerText()).includes('历史实际波动'));
      await details.locator('summary').click();
      const table = page.getByRole('table', { name: label + '优化验证对照' });
      assert.equal(await table.getByRole('row').count(), 7);
      assert.ok((await table.innerText()).includes((evidence.after.cagr * 100).toFixed(2) + '%'));
      assert.ok((await table.innerText()).includes((evidence.after.drawdown * 100).toFixed(2) + '%'));
      assert.equal(await table.evaluate(el => el.scrollWidth > el.clientWidth + 1), false);
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({ path: path.resolve(`../docs/reports/core-profiles-20260930/ui-${key}-${device}.png`), fullPage: true });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      await buttons.getByRole('button', { name: '自定义', exact: true }).click();
      await page.getByRole('tab', { name: '详细设置', exact: true }).click();
      assert.equal(await page.getByLabel('组合政策', { exact: true }).inputValue(), custom.allocation_policy);
      if (custom.allocation_policy !== 'legacy') {
        assert.equal(Number(await page.getByLabel('核心配置约束强度').inputValue()), custom.core_anchor);
        assert.equal(Number(await page.getByLabel('风险惩罚系数').inputValue()), custom.risk_aversion);
      }
      assert.equal(await page.locator('.preset-evidence').count(), 0);
    }
    await buttons.getByRole('button', { name: '激进', exact: true }).click();
    await buttons.getByRole('button', { name: '自定义', exact: true }).click();
    await page.getByRole('tab', { name: '详细设置', exact: true }).click();
    assert.equal(await page.getByLabel('组合政策', { exact: true }).inputValue(), custom.allocation_policy);
    assert.equal(await page.getByLabel('核心配置约束强度').count(), custom.allocation_policy === 'legacy' ? 0 : 1);
    await page.reload();
    await page.getByRole('region', { name: '固定方案摘要' }).waitFor();
    const activeLabel = { conservative: '保守', balanced: '均衡', aggressive: '激进', custom: '自定义' }[before.profile_library.active_slot];
    assert.equal(await buttons.getByRole('button', { name: activeLabel, exact: true }).getAttribute('aria-pressed'), 'true');
    await page.close();
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
  assert.deepEqual((await (await http.get('/api/v2/investment')).json()).settings, before.settings);
  console.log('PASS desktop/mobile profiles, scoped evidence, custom controls, aggressive reset, no overflow, no writes.');
} finally {
  await browser.close();
  await http.dispose();
}
