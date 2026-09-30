import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';

const base = 'http://127.0.0.1:8770';
const output = '../data/v3/research/profile-selection-2026-09-28/ui';
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const errors = [];
try {
  for (const [name, width, height] of [['desktop', 1440, 950], ['mobile', 390, 844]]) {
    const page = await browser.newPage({ viewport: { width, height } });
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto(`${base}/#strategy`);
    await page.getByRole('heading', { name: '策略方案' }).waitFor();
    const select = page.getByLabel('交易时怎样选档');
    assert.equal(await select.inputValue(), 'fixed');
    await page.getByRole('heading', { name: '固定档位 · 历史表现' }).waitFor();
    assert.equal(await page.locator('.strategy-layout .evidence-panel').count(), 1);
    assert.equal(await page.locator('.style-options > button').count(), 4);
    await select.selectOption('trend');
    await page.getByRole('heading', { name: '根据价格走势选择 · 历史表现' }).waitFor();
    assert.equal(await page.getByRole('heading', { name: '固定档位 · 历史表现' }).count(), 0);
    assert.equal(await page.locator('.style-options > button').count(), 0);
    await page.locator('.auto-profile-row').first().waitFor();
    assert.equal(await page.locator('.auto-profile-row').count(), 10);
    assert.equal(await page.getByRole('heading', { name: '当前自动选档' }).count(), 1);
    await page.screenshot({ path: `${output}/${name}-trend.png`, fullPage: true });
    await page.getByText('正在匹配评估记录…').waitFor({ state: 'hidden' });
    await select.selectOption('recent');
    assert.equal(await select.inputValue(), 'recent');
    await page.getByRole('heading', { name: '根据近三个月模拟成绩选择 · 历史表现' }).waitFor();
    assert.equal(await page.getByRole('heading', { name: '根据价格走势选择 · 历史表现' }).count(), 0);
    assert.equal(await page.locator('.strategy-layout .evidence-panel').count(), 1);
    assert.equal(await page.locator('.style-options > button').count(), 0);
    await page.getByRole('heading', { name: '最近一次自动选档' }).waitFor();
    await page.getByText('当前选档尚未计算').waitFor();
    assert.equal(await page.locator('.auto-profile-row').count(), 0);
    assert.equal(await page.getByText('每只 ETF 分别模拟三档近 60 个交易日的净收益', { exact: false }).count(), 1);
    await page.getByText('正在匹配评估记录…').waitFor({ state: 'hidden' });
    await page.getByRole('heading', { name: '这组参数还没有匹配的评估' }).waitFor();
    await page.screenshot({ path: `${output}/${name}.png`, fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    assert.equal(overflow, false, `${name}: page overflow`);
    const saved = await (await page.request.get(`${base}/api/v2/settings`)).json();
    assert.notEqual(saved.config.profile_selection, 'recent');
    await page.getByRole('button', { name: '测试这种方式' }).click();
    await page.getByRole('heading', { name: '实验中心' }).waitFor();
    assert.equal(await page.getByText('按近三个月模拟成绩选档', { exact: false }).count(), 1);
    await page.close();
  }
  const page = await browser.newPage();
  await page.route('**/api/v2/profile-selection/preview', async route => {
    const config = route.request().postDataJSON();
    const isRecent = config.profile_selection === 'recent';
    await route.fulfill({ json: { mode: config.profile_selection, rows: [{
      symbol: '510300.SH', name: '沪深300ETF', date: '2025-12-30', status: 'ready',
      profile: isRecent ? 'conservative' : 'aggressive',
      reason: isRecent ? '激进与均衡档近三个月模拟均亏损' : '近20日涨幅 +5.0%，5日均线高于20日均线',
    }] }});
  });
  await page.route('**/api/v2/evidence', async route => {
    const config = route.request().postDataJSON();
    const value = ({ fixed: .01, trend: .02, recent: .03 })[config.profile_selection] ?? .01;
    await route.fulfill({ json: {
      matched: true,
      scorecard: { count: 1, complete: true, verdict: '测试记录', beat_hold: 1,
        mean_return: value, worst_return: value, max_drawdown: -.01 },
      per_year: [{ year: 2025, return_pct: value, benchmark_return: 0, excess_return: value }],
      origin: '测试记录',
    }});
  });
  await page.goto(`${base}/#strategy`);
  const select = page.getByLabel('交易时怎样选档');
  await page.getByRole('heading', { name: '固定档位 · 历史表现' }).waitFor();
  assert.equal(await page.locator('.style-options > button').count(), 4);
  await page.getByText('1.00%', { exact: true }).first().waitFor();
  await select.selectOption('trend');
  await page.getByText('2.00%', { exact: true }).first().waitFor();
  await page.getByText('近20日涨幅 +5.0%，5日均线高于20日均线').waitFor();
  assert.equal(await page.locator('.style-options > button').count(), 0);
  assert.equal(await page.getByText('1.00%', { exact: true }).count(), 0);
  await select.selectOption('recent');
  await page.getByText('3.00%', { exact: true }).first().waitFor();
  await page.getByText('激进与均衡档近三个月模拟均亏损').waitFor();
  assert.equal(await page.locator('.auto-profile-grade.conservative').count(), 1);
  assert.equal(await page.getByText('2.00%', { exact: true }).count(), 0);
  await page.close();
  assert.deepEqual(errors, []);
  console.log('One history panel follows the selected mode, no accidental save, desktop/mobile overflow: PASS');
} finally {
  await browser.close();
}
