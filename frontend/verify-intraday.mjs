import assert from 'node:assert/strict';
import path from 'node:path';
import os from 'node:os';
import { chromium, request } from '@playwright/test';

// This acceptance test changes only the isolated preview on port 8772.
const base = 'http://127.0.0.1:8772';
const http = await request.newContext({ baseURL: base });
const health = await (await http.get('/api/v2/health')).json();
assert.equal(health.version, '4.7.0');
const before = await (await http.get('/api/v2/investment')).json();
assert.equal(before.settings.plan.auto_update, false);
const original = structuredClone(before.settings.plan);
if (before.started_at) assert.equal((await http.post('/api/v2/investment/reset')).ok(), true);
const next = { ...original, research_interval: '30m', research_multiple: 3, intraday_strategy: 'ml_edge' };
assert.equal((await http.put('/api/v2/investment/plan', {
  data: { plan: next, revision: before.settings.revision },
})).ok(), true);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const errors = [];
let testedJob;
try {
  for (const [device, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/#plan');
    await page.getByRole('heading', { name: '投资计划', exact: true }).waitFor();
    await page.getByLabel('盘中策略', { exact: true }).waitFor();
    assert.equal(await page.getByLabel('盘中策略', { exact: true }).inputValue(), 'ml_edge');
    assert.equal(await page.getByLabel('机器学习模型').count(), 0);
    assert.equal(await page.getByLabel('行情时间粒度').inputValue(), '30m');
    await page.screenshot({ path: path.join(os.tmpdir(), `stocklab-intraday-plan-${device}.png`), fullPage: true });
    await page.goto(base + '/#validation');
    if (device === 'desktop') {
      const queuedResponse = page.waitForResponse(response => response.url().endsWith('/investment/run') && response.request().method() === 'POST');
      await page.getByRole('button', { name: '运行盘中验证', exact: true }).click();
      const queued = await (await queuedResponse).json();
      testedJob = queued.id;
      assert.ok(testedJob);
      // Reload while running: the database-backed task/result must survive.
      await page.getByText('盘中验证正在后台运行，结果会保存在本机。', { exact: true }).waitFor();
      await page.reload();
      let status;
      for (let attempt = 0; attempt < 120; attempt++) {
        status = await (await http.get(`/api/v2/jobs/${testedJob}`)).json();
        if (!['queued', 'running'].includes(status.status)) break;
        await page.waitForTimeout(1000);
      }
      assert.equal(status.status, 'done', JSON.stringify({ id: status.id, error: status.error, progress: status.progress }));
      await page.reload();
    }
    const saved = await (await http.get('/api/v2/investment/intraday-research')).json();
    assert.equal(saved.result.job_id, testedJob);
    await page.getByRole('heading', { name: '参数筛选与后段验证' }).waitFor({ timeout: 180000 });
    await page.getByRole('heading', { name: '多时段对照 · 3x' }).waitFor();
    await page.locator('summary').filter({ hasText: '盘中回归 · 成本加倍后盈利' }).click();
    assert.equal(await page.getByText('未达到启用依据，不自动替换计划', { exact: true }).count(), 1);
    assert.equal(await page.getByLabel('验证起始年份').count(), 0);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    assert.equal(overflow, false);
    const canvas = page.locator('canvas').first();
    await canvas.waitFor();
    const rendered = await canvas.evaluate(element => {
      const rgba = element.getContext('2d').getImageData(0, 0, element.width, element.height).data;
      let colored = 0;
      for (let i = 0; i < rgba.length; i += 4) if (rgba[i + 3] && (rgba[i] < 230 || rgba[i + 1] < 230 || rgba[i + 2] < 230)) colored++;
      return colored;
    });
    assert.ok(rendered > 100);
    await page.screenshot({ path: path.join(os.tmpdir(), `stocklab-intraday-validation-${device}.png`), fullPage: true });
    await page.close();
  }
  const live = await (await http.get('/api/v2/investment')).json();
  assert.equal(live.live.orders_enabled, false);
  assert.equal(live.account.trades.length, 0);
  assert.deepEqual(errors, []);
  console.log('PASS: desktop/mobile, persistent background validation, ML/30m/3x, no overflow, nonblank charts, no live orders');
} finally {
  const latest = await (await http.get('/api/v2/investment')).json();
  const restored = await http.put('/api/v2/investment/plan', { data: { plan: original, revision: latest.settings.revision } });
  assert.equal(restored.ok(), true);
  await browser.close();
  await http.dispose();
}
