import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import { chromium, request, expect } from '@playwright/test';
const output = '../docs/reports/global-ui-20261001';
const http = await request.newContext({baseURL:'http://127.0.0.1:8772'});
const read = async () => (await (await http.get('/api/v2/investment')).json());
try {
  const state = await read();
  assert.equal(state.jobs.filter(j => ['queued','running'].includes(j.status)).length,0);
  if (process.argv.includes('--before')) {
    await fs.writeFile(`${output}/pre-deploy.json`,JSON.stringify(state));
    console.log(JSON.stringify({revision:state.settings.revision,slot:state.profile_library.active_slot,key:state.plan_key,activeJobs:0}));
  } else {
    const before = JSON.parse(await fs.readFile(`${output}/pre-deploy.json`,'utf8'));
    for(const key of ['settings','profile_library','account','started_at','curve','decision']) assert.deepEqual(state[key],before[key],key);
    const browser = await chromium.launch({channel:'msedge',headless:true});
    const errors = [];
    try {
      const page = await browser.newPage({viewport:{width:1440,height:1000}});
      page.on('pageerror',e => errors.push(e.message));
      await page.route('**/api/v2/**',route => {
        assert.equal(route.request().method(),'GET','Deployment smoke must be read-only');
        return route.continue();
      });
      await page.goto('http://127.0.0.1:8772/#validation');
      await expect(page.locator('.result-identity')).toBeVisible();
      await expect(page.getByText('折合年化',{exact:false})).toBeVisible();
      await page.screenshot({path:`${output}/production-validation.png`,fullPage:true});
      await page.getByRole('navigation',{name:'主导航',exact:true}).getByRole('button',{name:'总览',exact:true}).click();
      await page.getByRole('button',{name:'账户设置',exact:true}).click();
      await expect(page.getByRole('dialog',{name:'模拟账户设置'})).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await page.getByRole('link',{name:'研究',exact:true}).click();
      await expect(page.getByRole('heading',{name:'方案对比',exact:true})).toBeVisible();
      await page.getByRole('link',{name:'返回投资',exact:true}).click();
      await expect(page.getByRole('heading',{name:'账户总览'})).toBeVisible();
      assert.deepEqual(errors,[]);
    } finally {await browser.close();}
    const after = await read();
    for(const key of ['settings','profile_library','account','started_at','curve','decision']) assert.deepEqual(after[key],before[key],key);
    await fs.writeFile(`${output}/deployment.json`,JSON.stringify({passed:true,revision:state.settings.revision,slot:state.profile_library.active_slot,key:state.plan_key,errors},null,2));
    console.log('PASS: deployed UI, research return, readonly account dialog, original settings/account/curve preserved.');
  }
} finally {await http.dispose();}
