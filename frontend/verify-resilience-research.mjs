import assert from 'node:assert/strict';
import path from 'node:path';
import os from 'node:os';
import { chromium, request } from '@playwright/test';

const base = 'http://127.0.0.1:8772';
const http = await request.newContext({ baseURL: base });
const before = await (await http.get('/api/v2/investment')).json();
const report = (await (await http.get('/api/v2/investment/research-combinations')).json()).report;
assert.deepEqual(report.resilience_studies.map(s => s.replays), [36, 42, 22]);
assert.ok(report.resilience_studies.every(s => s.applied === false && s.passed.length === 0));
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const errors = [], writes = [];
try {
  for (const [device, viewport] of [['desktop', { width: 1440, height: 1000 }], ['mobile', { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport });
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') writes.push(r.url()); });
    await page.goto(base + '/#validation');
    await page.getByRole('tab', { name: '历史验证', exact: true }).click();
    const outer = page.locator('.research-section');
    await outer.locator('summary').first().click();
    await page.locator('.resilience-study').first().waitFor();
    assert.equal(await page.locator('.resilience-study[open]').count(), 0);
    for (const [i, study] of report.resilience_studies.entries()) {
      const section = page.locator('.resilience-study').nth(i);
      await section.locator('summary').first().click();
      for (const period of ['continuous', 'later', 'partial']) {
        await section.locator('select').selectOption(period);
        const value = study.rows[0].periods[period].base[period === 'partial' ? 'liquidation_return' : 'liquidation_cagr'];
        assert.ok((await section.locator('table').first().locator('tbody tr').first().locator('td').nth(1).innerText()).includes((value*100).toFixed(2)));
      }
      await section.getByText('淘汰原因与不确定性', { exact: true }).click();
      assert.equal(await section.locator('.candidate-audit').count(), study.rows.length);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      if (i === 0) await section.screenshot({ path: path.join(os.tmpdir(), `stocklab-resilience-${device}.png`) });
      await section.locator('summary').first().click();
    }
    await page.close();
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
  assert.deepEqual((await (await http.get('/api/v2/investment')).json()).settings, before.settings);
  console.log('PASS desktop/mobile: 3 collapsed studies, 100 replays, correct period values, rejection reasons, uncertainty, no overflow, no writes.');
} finally {
  await browser.close();
  await http.dispose();
}
