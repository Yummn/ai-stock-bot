import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { chromium, request } from '@playwright/test';

const base = 'http://127.0.0.1:8772';
const http = await request.newContext({ baseURL: base });
const initial = await (await http.get('/api/v2/investment')).json();
const study = await (await http.get('/api/v2/investment/observation')).json();
assert.ok(study.current);
assert.equal(study.current.status, 'active');
const id = study.current.id;
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const errors = [];
async function settled() {
  for (let i = 0; i < 90; i++) {
    const response = await (await http.get('/api/v2/investment/observation')).json();
    if (!['running', 'queued'].includes(response.job?.status)) {
      assert.notEqual(response.job?.status, 'failed', response.job?.error);
      return response;
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error('Observation worker did not settle');
}
try {
  for (const [device, viewport] of [['desktop', { width: 1440, height: 1000 }], ['mobile', { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/#validation');
    await page.getByRole('tab', { name: '前向观察', exact: true }).click();
    await page.getByRole('button', { name: '暂停观察', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: /验证当前计划/ }).count(), 0);
    assert.equal(await page.getByText('Invalid Date').count(), 0);
    if (device === 'desktop') {
      const paused = page.waitForResponse(r => r.url().endsWith('/investment/observation') && r.request().method() === 'POST');
      await page.getByRole('button', { name: '暂停观察', exact: true }).click();
      assert.equal((await paused).ok(), true);
      await page.reload();
      await page.getByRole('button', { name: '继续观察', exact: true }).waitFor();
      const resumed = page.waitForResponse(r => r.url().endsWith('/investment/observation') && r.request().method() === 'POST');
      await page.getByRole('button', { name: '继续观察', exact: true }).click();
      assert.equal((await resumed).ok(), true);
      await settled();
    }
    await page.reload();
    await page.getByRole('button', { name: '暂停观察', exact: true }).waitFor();
    assert.equal((await (await http.get('/api/v2/investment/observation')).json()).current.id, id);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await page.screenshot({ path: path.join(os.tmpdir(), `stocklab-forward-${device}.png`), fullPage: true });
    await page.locator('summary').filter({ hasText: '冻结参数与归档' }).click();
    assert.equal(await page.getByRole('button', { name: '归档本次观察', exact: true }).count(), 1);
    const downloading = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出观察记录', exact: true }).click();
    assert.equal((await downloading).suggestedFilename(), `observation-${id}.json`);
    await page.close();
  }
  // Synthetic UI-only fixture exercises future curves; never written to the actual database.
  const fixture = structuredClone(study);
  fixture.current.verdict = 'UI test fixture';
  for (const [index, book] of Object.values(fixture.current.books).entries()) {
    book.sessions = 4; book.observed_sessions = 3; book.reconstructed_sessions = 1;
    book.curve = [0, 1, 2, 3, 4].map(i => ({ date: `2026-09-${21 + i}`, equity: 100000 + (index + 1) * i * 80 }));
  }
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport });
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v2/investment/observation', route => route.fulfill({ json: fixture }));
    await page.goto(base + '/#validation');
    await page.getByRole('tab', { name: '前向观察', exact: true }).click();
    await page.getByRole('heading', { name: '启动后的模拟收益' }).waitFor();
    const canvas = page.locator('canvas').first();
    await canvas.waitFor();
    await page.waitForTimeout(250);
    assert.ok(await canvas.evaluate(c => {
      const data = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      let colored = 0;
      for (let i = 0; i < data.length; i += 4) if (data[i + 3] && Math.min(data[i], data[i + 1], data[i + 2]) < 230) colored++;
      return colored > 100;
    }));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await page.screenshot({ path: path.join(os.tmpdir(), `stocklab-forward-fixture-${viewport.width}.png`), fullPage: true });
    await page.close();
  }
  const final = await (await http.get('/api/v2/investment')).json();
  assert.deepEqual(final.settings, initial.settings);
  assert.deepEqual(final.account, initial.account);
  assert.equal(final.live.orders_enabled, false);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ pass: true, study: id, persisted: true, desktop: true, mobile: true, mainAccountUnchanged: true }));
} finally {
  const current = await (await http.get('/api/v2/investment/observation')).json();
  if (current.current?.id === id && current.current.status === 'paused') {
    await http.post('/api/v2/investment/observation', { data: { action: 'resume', revision: current.current.revision } });
  }
  await settled();
  await browser.close(); await http.dispose();
}
