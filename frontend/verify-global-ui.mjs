import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { chromium, request, expect } from '@playwright/test';

const output = '../docs/reports/global-ui-20261001';
await fs.mkdir(output, {recursive:true});
const http = await request.newContext();
const original = await (await http.get('http://127.0.0.1:8772/api/v2/investment')).json();
const evidence = await (await http.get('http://127.0.0.1:8772/api/v2/investment/evidence')).json();
assert.ok(evidence.report, 'A real saved report is required for this visual fixture');
await fs.writeFile(`${output}/production-before.json`, JSON.stringify(original));
const browser = await chromium.launch({channel:'msedge',headless:true});
const errors = [], receipts = [];
const onlyStrategy = plan => Object.fromEntries(Object.entries(plan).filter(([k]) => !['auto_update','validation_start_year','validation_end_year'].includes(k)));
try {
  for (const width of [360,390,768,1440,1920]) {
    const page = await browser.newPage({viewport:{width,height:width < 760 ? 844 : 1000}});
    page.on('pageerror', e => errors.push(e.message));
    let current = structuredClone(original), report = structuredClone(evidence.report);
    let writes = [], runs = [], charts = 0, failState = false, failEvidence = false;
    const initialEntries = structuredClone(current.profile_library.entries);
    await page.route('**/api/v2/**', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname;
      const answer = json => route.fulfill({json});
      if (p.endsWith('/access')) return answer({remote:false,write_allowed:true});
      if (p.endsWith('/investment')) return failState ? route.fulfill({status:503,json:{error:'fixture offline'}}) : answer(current);
      if (p.endsWith('/investment/evidence')) return failEvidence ? route.fulfill({status:503,json:{error:'fixture evidence failure'}}) : answer({report:report?.plan_key === current.plan_key && Number(report.start.slice(0,4)) === current.settings.plan.validation_start_year ? report : null});
      if (p.includes('/investment/chart/')) {charts++; return answer({name:'行情测试', points:[{date:'2025-01-02',close:3},{date:'2025-01-03',close:4}]});}
      if (p.endsWith('/investment/plan') && req.method() === 'PUT') {
        const body = req.postDataJSON(); writes.push(body);
        if (body.revision !== current.settings.revision) return route.fulfill({status:409,json:{error:'版本冲突'}});
        if (JSON.stringify(onlyStrategy(body.plan)) !== JSON.stringify(onlyStrategy(current.settings.plan))) current.plan_key = 'fixture-new-plan-identity';
        current.settings = {...current.settings, plan:body.plan, profile_slot:body.profile_slot, revision:current.settings.revision + 1};
        current.profile_library.active_slot = body.profile_slot;
        current.profile_library.entries[body.profile_slot].plan = structuredClone(body.plan);
        for (const entry of Object.values(current.profile_library.entries)) for (const key of current.profile_library.shared_fields) entry.plan[key] = body.plan[key];
        return answer(current.settings);
      }
      if (p.endsWith('/investment/run')) {runs.push(req.postDataJSON()); return answer({id:'fixture-only'});}
      if (p.endsWith('/broker/availability')) return answer({available:true,manual_test_order_available:true});
      if (p.endsWith('/broker/live-readiness')) return answer({configured:false,reason:'测试夹具未配置'});
      if (req.method() !== 'GET') throw new Error(`Unexpected write ${p}`);
      return route.fulfill({response:await http.get('http://127.0.0.1:8773' + p + url.search)});
    });
    async function nav(name) {
      await page.getByRole('navigation',{name:width < 760 ? '移动端导航' : '主导航',exact:true}).getByRole('button',{name,exact:true}).click();
      await page.locator('main h1').waitFor();
    }
    async function shot(name) {
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1),false, `${name} overflow at ${width}`);
      await page.screenshot({path:`${output}/${name}-${width}.png`,fullPage:true});
    }
    await page.goto('http://127.0.0.1:8773/#validation');
    await expect(page.locator('.result-identity')).toBeVisible();
    await expect(page.getByText('折合年化', {exact:false})).toBeVisible();
    await expect(page.locator('canvas').first()).toBeVisible();
    await expect.poll(() => page.locator('canvas').first().evaluate(c => {
      const d = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
      let n = 0; for(let i=3;i<d.length;i+=4) if(d[i]) n++; return n;
    })).toBeGreaterThan(100);
    assert.equal(charts,0,'Validation must not fetch an unrelated overview quote chart');
    await shot('validation');
    await page.getByRole('tab',{name:'独立跟踪',exact:true}).click();
    await expect(page.getByRole('heading',{name:'独立跟踪',exact:true})).toBeVisible();
    await shot('forward');
    await page.getByRole('tab',{name:'历史验证',exact:true}).click();
    await page.getByLabel('验证起始年份').selectOption('2021');
    await expect(page.locator('.result-identity')).toHaveCount(0);
    await expect(page.locator('.draft-reminder')).toHaveCount(0);
    await nav('投资计划');
    await expect(page.locator('.plan-status strong')).not.toContainText('草稿');
    await expect(page.getByLabel('模拟起始资金 / 元')).toHaveCount(0);
    await expect(page.getByLabel('自动更新模拟账户')).toHaveCount(0);
    await expect(page.locator('.plan-summary')).toHaveCount(0);
    await shot('plan');
    await nav('验证');
    await expect(page.getByLabel('验证起始年份')).toHaveValue('2021');
    await page.getByRole('button',{name:'验证当前计划',exact:true}).click();
    await expect.poll(() => runs.length).toBe(1);
    assert.equal(writes.length,1);
    assert.equal(current.profile_library.active_slot,original.profile_library.active_slot);
    assert.deepEqual(onlyStrategy(current.settings.plan),onlyStrategy(original.settings.plan));
    await nav('总览');
    await expect(page.getByRole('button',{name:'账户设置',exact:true})).toBeVisible();
    await shot('overview');
    await page.getByRole('button',{name:'账户设置',exact:true}).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByLabel('模拟起始资金 / 元')).toBeDisabled();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.getByRole('button',{name:'账户设置',exact:true})).toBeFocused();
    await page.getByRole('button',{name:'账户设置',exact:true}).click();
    await page.getByLabel('自动更新模拟账户').uncheck();
    await page.getByRole('button',{name:'保存账户设置',exact:true}).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    assert.equal(current.settings.plan.auto_update,false);
    for(const key of Object.keys(initialEntries)) assert.deepEqual(onlyStrategy(current.profile_library.entries[key].plan),onlyStrategy(initialEntries[key].plan));
    await nav('投资计划');
    await page.getByRole('button',{name:'均衡',exact:true}).click();
    await page.getByRole('button',{name:'调整本档',exact:true}).click();
    await page.getByLabel('机器学习模型').selectOption('ridge');
    await page.getByTitle('打开模型参数设置').click();
    await expect(page.getByRole('dialog',{name:'模型参数设置'})).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await shot('plan-edit');
    // Saving shared preferences while previewing another profile must retain the draft.
    await nav('总览');
    await page.getByRole('button',{name:'账户设置',exact:true}).click();
    await page.getByLabel('自动更新模拟账户').check();
    await page.getByRole('button',{name:'保存账户设置',exact:true}).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    assert.equal(current.profile_library.active_slot,original.profile_library.active_slot);
    assert.equal(current.settings.plan.model_recipe, original.settings.plan.model_recipe);
    await nav('验证');
    await expect(page.getByRole('button',{name:'先保存投资计划'})).toBeVisible();
    await page.getByRole('button',{name:'先保存投资计划'}).click();
    await expect(page.getByRole('heading',{name:'投资计划',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'调整本档',exact:true}).click();
    await expect(page.getByLabel('机器学习模型')).toHaveValue('ridge');
    if (width === 390) {
      current.settings.revision++;
      await expect(page.getByText('另一设备已保存新版本。当前草稿仍保留，保存冲突时不会覆盖服务器参数。')).toBeVisible({timeout:15000});
      await nav('总览');
      await page.getByRole('button',{name:'账户设置',exact:true}).click();
      const count = writes.length;
      await page.getByRole('button',{name:'保存账户设置',exact:true}).click();
      await expect(page.getByRole('dialog').getByRole('alert')).toContainText('先处理或放弃旧草稿');
      assert.equal(writes.length,count,'Account save must not rebase a conflicting strategy draft');
      await page.keyboard.press('Escape');
      await nav('投资计划');
    }
    await page.getByRole('button',{name:'放弃所有草稿',exact:true}).click();
    await nav('总览');
    await page.getByRole('button',{name:'账户设置',exact:true}).click();
    const dialogRevision = current.settings.revision;
    current.settings.revision++;
    await page.getByRole('button',{name:'保存账户设置',exact:true}).click();
    await expect(page.getByRole('dialog').getByRole('alert')).toHaveText('版本冲突');
    assert.equal(writes.at(-1).revision,dialogRevision);
    await page.keyboard.press('Escape');
    await nav('券商');
    await expect(page.getByRole('tab',{name:'真实账户 · 只读'})).toHaveAttribute('aria-selected','true');
    await expect(page.getByRole('button',{name:'查询仿真账户'})).not.toBeVisible();
    await shot('broker');
    await page.getByRole('tab',{name:'券商仿真 · 测试'}).click();
    await expect(page.getByRole('button',{name:'查询仿真账户'})).toBeVisible();
    await expect(page.getByRole('button',{name:'预览测试委托'})).not.toBeVisible();
    // An unavailable initial state needs a working recovery path, not an empty shell.
    failState = true;
    await page.goto('http://127.0.0.1:8773/#overview');
    await page.reload();
    await expect(page.getByRole('button',{name:'重试连接'})).toBeVisible();
    failState = false;
    await page.getByRole('button',{name:'重试连接'}).click();
    await expect(page.getByRole('heading',{name:'账户总览'})).toBeVisible();
    // A result read failure remains distinct from an unvalidated plan.
    failEvidence = true;
    await page.goto('http://127.0.0.1:8773/#validation');
    await page.reload();
    await expect(page.getByRole('button',{name:'重试读取结果'})).toBeVisible();
    failEvidence = false;
    await page.getByRole('button',{name:'重试读取结果'}).click();
    await expect(page.getByRole('button',{name:'重试读取结果'})).toHaveCount(0);
    assert.deepEqual(current.account, original.account);
    receipts.push({width,writes:writes.length,runs:runs.length,charts,accountUnchanged:true});
    await page.close();
  }
  assert.deepEqual(errors,[]);
  const after = await (await http.get('http://127.0.0.1:8772/api/v2/investment')).json();
  for (const key of ['settings','profile_library','account','started_at','curve','decision']) assert.deepEqual(after[key],original[key]);
  await fs.writeFile(`${output}/browser-verification.json`,JSON.stringify({receipts,errors,productionUnchanged:true},null,2));
  console.log('PASS: five viewports, lazy quotes, saved/draft/period isolation, account preferences, modal focus/Escape, broker separation, recovery, real chart pixels, no production writes.');
} finally { await browser.close(); await http.dispose(); }
