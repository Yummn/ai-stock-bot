import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';

const base = 'http://127.0.0.1:8769';
const output = '../data/v3/research/decision-upgrade-2026-09-28/ui';
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const errors = [];
try {
  for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    const page = await browser.newPage({ viewport: { width, height } });
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto(`${base}/#strategy`);
    await page.getByRole('button', { name: '查看与调整参数' }).click();
    await page.getByRole('button', { name: '训练数据', exact: true }).click();
    const select = page.locator('label').filter({ has: page.getByText('训练收益口径', { exact: true }) }).locator('select');
    assert.equal(await select.inputValue(), 'close_return');
    await select.selectOption('next_open_net');
    assert.equal(await select.inputValue(), 'next_open_net');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    assert.equal(overflow, false, `${name}: page overflow`);
    await page.screenshot({ path: `${output}/${name}-training.png`, fullPage: true });
    await page.getByRole('button', { name: '关闭参数编辑器' }).click();
    // Closing/reloading this draft must not silently activate a new target.
    await page.reload();
    const settings = await (await page.request.get(`${base}/api/v2/settings`)).json();
    assert.equal(settings.config.models.cnn.aggressive.label_basis, 'close_return');
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log('Desktop/mobile target selection, no overflow, no silent save, no page errors: PASS');
} finally {
  await browser.close();
}
