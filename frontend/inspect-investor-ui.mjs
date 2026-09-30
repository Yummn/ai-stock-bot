import { chromium } from '@playwright/test';
import fs from 'node:fs/promises';
const root = '../docs/reports/global-ui-20261001';
await fs.mkdir(root, {recursive:true});
const browser = await chromium.launch({channel:'msedge',headless:true});
try {
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  for (const tab of ['validation','plan','overview']) {
    await page.goto(`http://127.0.0.1:8772/#${tab}`);
    await page.locator('main h1').waitFor();
    await page.screenshot({path:`${root}/before-${tab}.png`,fullPage:true});
    console.log(tab, await page.locator('main h1, main h2').allTextContents());
  }
} finally {await browser.close();}
