import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { chromium, request } from '@playwright/test';

const base = 'http://127.0.0.1:8772';
const http = await request.newContext({ baseURL: base });
const before = await (await http.get('/api/v2/investment')).json();
const study = await (await http.get('/api/v2/investment/observation')).json();
const published = await (await http.get('/api/v2/investment/research-combinations')).json();
assert.equal(published.report.candidate_count, 83);
assert.equal(published.report.replays, 328);
assert.equal(published.report.applied, false);
assert.equal(published.report.goal_proven, false);
assert.equal(published.report.rows.find(r => r.id === 'hold-1x').periods.continuous.base.trade_count, 6);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const errors = [], mutations = [];
try {
  for (const [device, viewport] of [['desktop', { width: 1440, height: 1000 }], ['mobile', { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport });
    let reads = 0;
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', req => {
      if (req.method() !== 'GET' && req.url().includes('/api/')) mutations.push(req.url());
      if (req.url().endsWith('/investment/research-combinations')) reads++;
    });
    await page.goto(base + '/#validation');
    await page.getByRole('tab', { name: '历史验证', exact: true }).click();
    const section = page.locator('.research-section');
    await section.locator('summary').first().waitFor();
    assert.equal(reads, 0, 'Research must load only when expanded');
    await section.locator('summary').first().click();
    await section.getByLabel('研究方案').waitFor();
    assert.equal(reads, 1);
    for (const selected of ['xgb20-trend', 'risk-volatility_target-weekly-3x', 'hold-1x']) {
      await section.getByLabel('研究方案').selectOption(selected);
      for (const period of ['continuous', 'later', 'partial']) {
        await section.getByLabel('研究区间', { exact: true }).selectOption(period);
        const expected = published.report.rows.find(r => r.id === selected).periods[period].base;
        await page.waitForFunction(({ value }) => document.querySelector('.research-metrics strong')?.textContent?.includes(value), { value: (expected.liquidation_cagr * 100).toFixed(2) });
      }
    }
    await section.getByLabel('研究方案').selectOption('xgb20-trend');
    await section.getByLabel('研究区间', { exact: true }).selectOption('continuous');
    await section.locator('canvas').waitFor();
    await page.waitForFunction(() => {
      const canvas = document.querySelector('.research-section canvas');
      if (!canvas?.width || !canvas?.height) return false;
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      let colored = 0;
      for (let i = 0; i < pixels.length; i += 4) if (pixels[i + 3] && Math.max(pixels[i], pixels[i+1], pixels[i+2]) - Math.min(pixels[i], pixels[i+1], pixels[i+2]) > 50) colored++;
      return colored > 300;
    });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await section.scrollIntoViewIfNeeded();
    await section.screenshot({ path: path.join(os.tmpdir(), `stocklab-combinations-${device}.png`) });
    await section.getByText('冻结参数', { exact: true }).click();
    assert.ok((await section.locator('pre').innerText()).includes('"horizon": 20'));
    await section.getByText('全部候选 · 2020–2022 开发期', { exact: true }).click();
    assert.equal(await section.locator('details').filter({ has: page.getByText('全部候选 · 2020–2022 开发期', { exact: true }) }).locator('tbody tr').count(), 83);
    if (device === 'desktop') {
      const download = page.waitForEvent('download');
      await section.getByLabel('下载研究结果').click();
      assert.equal((await download).suggestedFilename(), 'combination-research.json');
    }
    await page.reload();
    await page.locator('.research-section summary').first().waitFor();
    assert.equal(await page.locator('.research-section').getAttribute('open'), null);
    await page.close();
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(mutations, []);
  const after = await (await http.get('/api/v2/investment')).json();
  assert.deepEqual(after.settings, before.settings);
  const observed = await (await http.get('/api/v2/investment/observation')).json();
  assert.equal(observed.current.id, study.current.id);
  assert.equal(observed.current.status, 'active');
  console.log('PASS desktop/mobile: lazy load, 18 state transitions, chart pixels, overflow, parameters, all 83 candidates, export; no writes; frozen study unchanged.');
} finally {
  await browser.close();
  await http.dispose();
}
