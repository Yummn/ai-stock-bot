import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium, request, expect } from '@playwright/test';

const base = 'http://127.0.0.1:8773';
const output = path.resolve('../docs/reports/research-workspace-20261001');
const http = await request.newContext({baseURL: base});
const browser = await chromium.launch({channel:'msedge', headless:true});
const errors = [];
const snapshots = [];
const state = async () => (await (await http.get('/api/v2/investment')).json());
let before = await state();
if (before.profile_library.active_slot !== 'balanced') {
  const reset = await http.put('/api/v2/investment/plan', {data:{plan:before.profile_library.entries.balanced.plan,
    profile_slot:'balanced',revision:before.settings.revision}});
  assert.equal(reset.status(),200);
  before = await state();
}
const record = JSON.parse(await fs.readFile(path.join(output, 'compare.json'), 'utf8'));
const job = await (await http.get(`/api/v2/jobs/${record.id}`)).json();
try {
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(base + '/?lab=1#experiments');
  await expect(page.getByRole('heading',{name:'方案对比',exact:true})).toBeVisible();
  assert.equal(await page.getByRole('navigation',{name:'研究导航'}).getByRole('button').count(),3);
  assert.equal(await page.getByRole('button',{name:/手动买入|模拟下单|账户重置/}).count(),0);
  await page.getByRole('button',{name:'参数优化',exact:true}).click();
  await page.getByLabel('研究模型配方').selectOption('ridge20');
  await page.getByLabel('正则强度',{exact:true}).fill('37');
  await page.getByLabel('研究模型配方').selectOption('hgb20_regularized');
  await page.getByLabel('最大叶节点',{exact:true}).fill('9');
  await page.getByLabel('研究模型配方').selectOption('ridge20');
  await expect(page.getByLabel('正则强度',{exact:true})).toHaveValue('37');
  assert.equal(await page.getByLabel('最大叶节点',{exact:true}).count(),0);
  await page.reload();
  await expect(page.getByLabel('正则强度',{exact:true})).toHaveValue('37');
  let submitted;
  await page.route('**/api/v2/research-workspace/run', async route => {
    submitted = route.request().postDataJSON();
    await route.fulfill({status:202,contentType:'application/json',body:JSON.stringify(job)});
  });
  await page.getByRole('button',{name:'开始参数优化',exact:true}).click();
  await expect(page.getByRole('heading',{name:'研究记录',exact:true})).toBeVisible();
  assert.equal(submitted.study.recipe,'ridge20');
  assert.equal(submitted.study.tuning.regularization,37);
  assert.equal(submitted.study.mode,'tune');
  assert.equal(submitted.base_plan_key,before.plan_key);
  await expect(page.locator('.study-results')).toBeVisible();
  assert.equal((await state()).settings.revision,before.settings.revision);
  await page.unroute('**/api/v2/research-workspace/run');
  for (const [device, viewport] of [['desktop',{width:1440,height:1000}],['wide',{width:1920,height:1080}],['mobile',{width:390,height:844}],['small',{width:360,height:780}]]) {
    await page.setViewportSize(viewport);
    for (const tab of ['方案对比','参数优化','研究记录']) {
      await page.getByRole('navigation',{name:'研究导航'}).getByRole('button',{name:tab,exact:true}).click();
      await expect(page.getByRole('heading',{name:tab,exact:true})).toBeVisible();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1),false,`${device}:${tab} horizontal page overflow`);
      if (tab !== '研究记录') {
        assert.ok(await page.locator('.research-controls input').first().evaluate(e => e.clientWidth) >= 100, `${device}: usable year inputs`);
        assert.ok(await page.locator('.research-run').evaluate(e => e.clientHeight) < 80, `${device}: normal button height`);
      }
      if (tab === '研究记录') {
        await expect(page.locator('.portfolio-chart canvas').first()).toBeVisible();
        const colored = await page.locator('.portfolio-chart canvas').first().evaluate(canvas => {
          const data = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
          let count=0; for(let i=0;i<data.length;i+=4) if(data[i+3]>0 && Math.max(data[i],data[i+1],data[i+2])-Math.min(data[i],data[i+1],data[i+2])>25) count++;
          return count;
        });
        assert.ok(colored>150,`${device}: chart is nonblank`);
        if (viewport.width >= 1400) assert.ok(await page.locator('.research-history-detail').evaluate(e=>e.clientWidth)>800);
      }
      if (['desktop','mobile'].includes(device)) {
        const target=path.join(output, `${device}-${tab}.png`);
        await page.screenshot({path:target,fullPage:true});snapshots.push(target);
      }
    }
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('button',{name:'Ridge · 正则100',exact:true}).click();
  await page.getByRole('link',{name:'送到投资计划草稿',exact:true}).click();
  await expect(page.getByText(/已载入研究草稿/)).toBeVisible();
  assert.equal((await state()).settings.revision,before.settings.revision);
  await expect(page.getByRole('button',{name:'保存并使用自定义',exact:true})).toBeEnabled();
  const saved = page.waitForResponse(r=>r.url().endsWith('/investment/plan') && r.request().method()==='PUT');
  await page.getByRole('button',{name:'保存并使用自定义',exact:true}).click();
  assert.equal((await saved).status(),200);
  const after = await state();
  assert.equal(after.profile_library.entries.custom.plan.model_recipe,'ridge20');
  for(const slot of ['conservative','balanced','aggressive'])
    assert.deepEqual(after.profile_library.entries[slot].plan,before.profile_library.entries[slot].plan);
  assert.deepEqual(after.account,before.account);
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(output,'browser-verification.json'),JSON.stringify({passed:true,errors,snapshots,viewports:[1440,1920,390,360],checks:['legacy-hash-routing','no-duplicate-trading','per-model-draft-persistence','request-parameters','real-result-rendering','nonblank-chart','no-page-overflow','draft-only-handoff','explicit-save','other-profiles-and-account-preserved']},null,2));
  console.log('PASS research navigation, parameter persistence, real result rendering, 4 responsive widths, draft handoff and explicit save.');
} finally {await browser.close();await http.dispose();}
