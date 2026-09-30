import { chromium } from "@playwright/test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";

const base = "http://127.0.0.1:8770";
const root = "../data/v3/research/all-references-2026-09-28";
const decisions = Object.values(
  JSON.parse(await fs.readFile(`${root}/hybrid.json`, "utf8")),
);
await fs.mkdir(`${root}/ui`, { recursive: true });
const browser = await chromium.launch({ channel: "msedge", headless: true });
const errors = [];
try {
  for (const [name, width, height] of [
    ["desktop", 1440, 1000],
    ["mobile", 390, 844],
  ]) {
    const page = await browser.newPage({ viewport: { width, height } });
    page.on("pageerror", (e) => errors.push(String(e)));
    // Exercise the real-provider decision renderer without writing any account.
    await page.route("**/api/v2/overview", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.decisions = decisions.map((d) => ({
        ...d,
        execution: "联合判断验证，未成交",
      }));
      await route.fulfill({ response, json: body });
    });
    await page.goto(`${base}/#account`);
    await page.getByText("市场研究报告", { exact: false }).click();
    await page.locator(".research-report").waitFor({ state: "visible" });
    assert.ok((await page.locator(".research-sources a").count()) >= 4);
    await page
      .getByText("热点与走势", { exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${root}/ui/${name}-research.png` });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth + 1,
      ),
      false,
    );
    await page.getByText("最近决策与成交原因", { exact: false }).click();
    await page.locator(".ml-reference-panel summary").first().click();
    assert.equal(
      await page.locator(".ml-reference-panel tbody tr").count(),
      15,
    );
    await page
      .getByText("最近决策与成交原因", { exact: false })
      .scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${root}/ui/${name}-review.png` });
    await page.goto(`${base}/#strategy`);
    await page.locator(".method-choice select").selectOption("ai");
    await page.getByRole("button", { name: "查看与调整参数" }).click();
    await page.getByText("共 15 组参考", { exact: false }).waitFor();
    assert.equal(await page.getByText("参考模型", { exact: true }).count(), 0);
    await page.screenshot({ path: `${root}/ui/${name}-ai-settings.png` });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth + 1,
      ),
      false,
    );
    await page.getByRole("button", { name: "关闭参数编辑器" }).click();
    await page.getByRole("button", { name: /均衡/ }).click();
    await page.getByRole("button", { name: "查看与调整参数" }).click();
    await page.getByText("共 15 组参考", { exact: false }).waitFor();
    const config = await (
      await page.request.get(`${base}/api/v2/settings`)
    ).json();
    assert.equal(config.config.engine, "ml");
    assert.equal(config.config.models.cnn.aggressive.buy_threshold > 0, true);
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(
    "Desktop/mobile: citations, 15 ML references, AI settings, no overflow, no saved-setting mutation: PASS",
  );
} finally {
  await browser.close();
}
