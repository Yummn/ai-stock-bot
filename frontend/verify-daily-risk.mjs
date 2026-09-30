import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { chromium, request } from '@playwright/test';

const base = 'http://127.0.0.1:8772';
const http = await request.newContext({ baseURL: base });
assert.equal((await (await http.get('/api/v2/health')).json()).version, '4.8.0');
const before = await (await http.get('/api/v2/investment')).json();
assert.equal(before.started_at, null);
assert.equal(before.settings.plan.auto_update, false);
assert.equal(before.account.trades.length, 0);
const original = structuredClone(before.settings.plan);
const study = JSON.parse(await fs.readFile('../docs/reports/daily-4-7-stability/results.json', 'utf8'));
const candidate = { ...study.later['2'].plan, validation_start_year: 2020, validation_end_year: 2025 };
assert.equal((await http.put('/api/v2/investment/plan', {
  data: { plan: candidate, revision: before.settings.revision },
})).ok(), true);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const errors = [];
let jobId;
try {
  for (const [device, viewport] of [['desktop', { width: 1440, height: 1000 }], ['mobile', { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/#plan');
    await page.getByLabel('仓位管理', { exact: true }).waitFor();
    assert.equal(await page.getByLabel('仓位管理', { exact: true }).inputValue(), 'volatility_target');
    assert.equal(await page.getByLabel('趋势过滤', { exact: true }).inputValue(), 'multi_horizon');
    await page.screenshot({ path: path.join(os.tmpdir(), `stocklab-daily-plan-${device}.png`), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    if (device === 'desktop') {
      await page.getByRole('tab', { name: '详细设置', exact: true }).click();
      await page.getByLabel('目标年化波动', { exact: true }).fill('13');
      let saving = page.waitForResponse(r => r.url().endsWith('/investment/plan') && r.request().method() === 'PUT');
      await page.getByRole('button', { name: '保存计划', exact: true }).click();
      assert.equal((await saving).ok(), true);
      await page.reload();
      await page.getByRole('tab', { name: '详细设置', exact: true }).click();
      assert.equal(await page.getByLabel('目标年化波动', { exact: true }).inputValue(), '13');
      await page.getByLabel('目标年化波动', { exact: true }).fill('12');
      saving = page.waitForResponse(r => r.url().endsWith('/investment/plan') && r.request().method() === 'PUT');
      await page.getByRole('button', { name: '保存计划', exact: true }).click();
      assert.equal((await saving).ok(), true);
    }
    await page.goto(base + '/#validation');
    if (device === 'desktop') {
      const queued = page.waitForResponse(r => r.url().endsWith('/investment/run') && r.request().method() === 'POST');
      await page.getByRole('button', { name: /^(验证当前计划|重新验证当前计划)$/ }).click();
      const response = await queued;
      assert.equal(response.ok(), true);
      jobId = (await response.json()).id;
      await page.reload();
      let job;
      for (let i = 0; i < 180; i++) {
        job = await (await http.get(`/api/v2/jobs/${jobId}`)).json();
        if (!['queued', 'running'].includes(job.status)) break;
        await page.waitForTimeout(1000);
      }
      assert.equal(job.status, 'done', JSON.stringify({ status: job.status, error: job.error }));
      const evidence = (await (await http.get('/api/v2/investment/evidence')).json()).report;
      assert.equal(evidence.id, jobId);
      assert.equal(evidence.scope, 'current_plan');
      assert.ok(evidence.results.unlevered && evidence.results.hold_1x && evidence.results.stress);
      assert.equal(evidence.results.ml, undefined);
      assert.equal(evidence.results.cnn, undefined);
      await page.reload();
    }
    await page.getByRole('heading', { name: '逐笔执行后的资产走势' }).waitFor();
    await page.locator('summary').filter({ hasText: '逐年表现与其他策略' }).click();
    await page.getByRole('cell', { name: '当前策略 · 1x', exact: true }).waitFor();
    const canvas = page.locator('canvas').first();
    await canvas.waitFor();
    const colored = await canvas.evaluate(c => {
      const rgba = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      let count = 0;
      for (let i = 0; i < rgba.length; i += 4) if (rgba[i + 3] && Math.min(rgba[i], rgba[i + 1], rgba[i + 2]) < 230) count++;
      return count;
    });
    assert.ok(colored > 100);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(150);
    await page.screenshot({ path: path.join(os.tmpdir(), `stocklab-daily-validation-viewport-${device}.png`) });
    await page.screenshot({ path: path.join(os.tmpdir(), `stocklab-daily-validation-${device}.png`), fullPage: true });
    if (device === 'desktop') {
      await page.goto(base + '/#plan');
      await page.getByLabel('趋势过滤', { exact: true }).selectOption('legacy');
      let saved = page.waitForResponse(r => r.url().endsWith('/investment/plan') && r.request().method() === 'PUT');
      await page.getByRole('button', { name: '保存计划', exact: true }).click();
      assert.equal((await saved).ok(), true);
      assert.equal((await (await http.get('/api/v2/investment/evidence')).json()).report, null);
      await page.getByLabel('趋势过滤', { exact: true }).selectOption('multi_horizon');
      saved = page.waitForResponse(r => r.url().endsWith('/investment/plan') && r.request().method() === 'PUT');
      await page.getByRole('button', { name: '保存计划', exact: true }).click();
      assert.equal((await saved).ok(), true);
      assert.equal((await (await http.get('/api/v2/investment/evidence')).json()).report.id, jobId);
    }
    await page.close();
  }
  const live = await (await http.get('/api/v2/investment')).json();
  assert.equal(live.live.orders_enabled, false);
  assert.equal(live.account.trades.length, 0);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ pass: true, jobId, desktop: true, mobile: true, persisted: true, liveOrders: false }));
} finally {
  const latest = await (await http.get('/api/v2/investment')).json();
  assert.equal((await http.put('/api/v2/investment/plan', {
    data: { plan: original, revision: latest.settings.revision },
  })).ok(), true);
  await browser.close();
  await http.dispose();
}
