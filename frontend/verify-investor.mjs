import { chromium } from "@playwright/test";
import fs from "node:fs/promises";

const base = process.env.INVESTOR_URL || "http://127.0.0.1:8771";
const directory = "../data/v4/ui-checks";
await fs.mkdir(directory, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: "msedge" });
const errors = [];
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.on("pageerror", (error) => errors.push(error.message));
const receipt = { views: [], checks: [] };
try {
  for (const viewport of [
    { name: "desktop", width: 1440, height: 1000 },
    { name: "mobile", width: 390, height: 844 },
    { name: "small", width: 320, height: 720 },
  ]) {
    await page.setViewportSize(viewport);
    for (const view of ["overview", "live", "plan", "validation"]) {
      const started = performance.now();
      await page.goto(`${base}/#${view}`);
      await page
        .getByRole("heading", {
          name: {
            overview: "账户总览",
            live: "实盘账户",
            plan: "投资计划",
            validation: "验证结果",
          }[view],
          exact: true,
        })
        .waitFor();
      if (["overview", "validation"].includes(view)) await page.locator("canvas").first().waitFor();
      if (view === "live") {
        await page.getByText("当前不会发生真实交易", { exact: true }).waitFor();
        if (await page.getByRole("button", { name: /买入|卖出|连接券商/ }).count())
          throw new Error("Unavailable live execution control is visible");
      }
      await page.waitForTimeout(500);
      const widths = await page.evaluate(() => ({
        viewport: innerWidth,
        content: document.documentElement.scrollWidth,
      }));
      if (widths.content > widths.viewport + 1)
        throw new Error(
          `Horizontal page overflow: ${viewport.name}/${view}: ${JSON.stringify(widths)}`,
        );
      const pixels = await page.locator("canvas").evaluateAll((canvases) =>
        canvases.map((canvas) => {
          const data = canvas
            .getContext("2d")
            .getImageData(0, 0, canvas.width, canvas.height).data;
          let painted = 0;
          for (let i = 3; i < data.length; i += 4) if (data[i] > 0) painted++;
          return painted;
        }),
      );
      if (["overview", "validation"].includes(view) && !pixels.some((n) => n > 100))
        throw new Error("Blank chart");
      await page.screenshot({
        path: `${directory}/${viewport.name}-${view}.png`,
        fullPage: false,
      });
      receipt.views.push({
        viewport: viewport.name,
        view,
        loadMs: Math.round(performance.now() - started),
        widths,
        pixels,
      });
      if (view === "plan") {
        await page.locator('input[type="radio"][value="machine"]').check();
        await page.getByLabel("机器学习模型").selectOption("cnn_class");
        await page.locator(".signal-compare-row").filter({ hasText: "1D-CNN · 涨跌分类" }).waitFor();
        await page.locator(".signal-compare-row").filter({ hasText: "1D-CNN · 净收益回归" }).waitFor();
        await page.screenshot({ path: `${directory}/${viewport.name}-plan-machine.png` });
        await page.getByRole("button", { name: "参数" }).click();
        const bounds = await page.getByRole("dialog", { name: "模型参数设置" }).boundingBox();
        if (!bounds || bounds.x < 0 || bounds.x + bounds.width > viewport.width + 1)
          throw new Error(`Model settings dialog overflow: ${viewport.name}: ${JSON.stringify(bounds)}`);
        await page.screenshot({ path: `${directory}/${viewport.name}-parameters.png` });
        await page.getByRole("button", { name: "关闭模型参数设置" }).click();
        await page.locator('input[type="radio"][value="rotation"]').check();
      }
      if (view === "validation") {
        await page.getByRole("tab", { name: "频率实验" }).click();
        await page.getByRole("heading", { name: "日线调仓对照" }).waitFor();
        await page.locator(".frequency-view table tbody tr").first().waitFor();
        const frequencyWidths = await page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }));
        if (frequencyWidths.content > frequencyWidths.viewport + 1)
          throw new Error(`Frequency view overflow: ${viewport.name}: ${JSON.stringify(frequencyWidths)}`);
        await page.screenshot({ path: `${directory}/${viewport.name}-frequency.png` });
        await page.getByRole("tab", { name: "2x/3x 风险" }).click();
        await page.getByRole("heading", { name: "同一组合的逐日倍数路径" }).waitFor();
        if (await page.locator(".leverage-view tbody tr").count() !== 3)
          throw new Error(`Missing leverage scenarios: ${viewport.name}`);
        const riskWidths = await page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }));
        if (riskWidths.content > riskWidths.viewport + 1)
          throw new Error(`Leverage view overflow: ${viewport.name}: ${JSON.stringify(riskWidths)}`);
        await page.screenshot({ path: `${directory}/${viewport.name}-leverage.png` });
        await page.getByRole("tab", { name: "策略对照" }).click();
      }
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${base}/#plan`);
  const input = page.getByLabel("总投入上限 / %");
  await input.waitFor();
  const original = await input.inputValue();
  const other = await context.newPage();
  await other.goto(`${base}/#plan`);
  await other.getByLabel("总投入上限 / %").fill("68");
  try {
    await input.fill("69");
    await page.getByRole("button", { name: "保存计划", exact: true }).click();
    await page.getByText("整个投资计划已保存", { exact: true }).waitFor();
    await page.reload();
    await page.getByLabel("总投入上限 / %").waitFor();
    if ((await page.getByLabel("总投入上限 / %").inputValue()) !== "69")
      throw new Error("Save did not persist");
    await other.getByRole("button", { name: "保存计划", exact: true }).click();
    await other
      .getByRole("alert")
      .filter({ hasText: "其他设备已更新" })
      .waitFor();
    receipt.checks.push(
      "Settings persist after reload; stale second-device draft cannot overwrite",
    );
  } finally {
    await page.getByLabel("总投入上限 / %").fill(original);
    await page.getByRole("button", { name: "保存计划", exact: true }).click();
    await page.getByText("整个投资计划已保存", { exact: true }).waitFor();
    await other.close();
  }
  await page.locator('input[type="radio"][value="machine"]').check();
  await page.getByLabel("机器学习模型").selectOption("cnn_class");
  await page.getByRole("button", { name: "参数" }).click();
  await page.getByRole("dialog", { name: "模型参数设置" }).waitFor();
  await page.getByLabel("上涨概率门槛 / %").waitFor();
  await page.getByLabel("观察长度 / 交易日").waitFor();
  await page.getByRole("button", { name: "关闭模型参数设置" }).click();
  await page.getByLabel("机器学习模型").selectOption("ml");
  await page.getByRole("button", { name: "参数" }).click();
  await page.getByLabel("迭代次数").waitFor();
  if (await page.getByLabel("上涨概率门槛 / %").count())
    throw new Error("Classification threshold leaked into regression settings");
  await page.getByRole("button", { name: "关闭模型参数设置" }).click();
  await page.locator('input[type="radio"][value="rotation"]').check();
  if (await page.getByLabel("机器学习模型").count())
    throw new Error("Model picker visible for trend plan");
  receipt.checks.push("Trend and machine-learning plan hierarchy; classification and regression parameters remain scoped");
  receipt.checks.push("Live account is separate and cannot submit an order before broker connection");
  receipt.checks.push("Saved daily-versus-weekly and exposure-aware leverage research load on desktop and mobile");
  await page.goto(`${base}/#overview`);
  await page.getByLabel("行情粒度").selectOption("60m");
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${directory}/desktop-hourly.png` });
  await page.goto(`${base}/#validation`);
  await page.getByRole("button", { name: "导出完整验证结果" }).waitFor();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出完整验证结果" }).click();
  const file = await download;
  await file.saveAs(`${directory}/export.json`);
  const exported = JSON.parse(
    await fs.readFile(`${directory}/export.json`, "utf8"),
  );
  if (!exported.complete || Object.keys(exported.results).length !== 8 || !exported.results.cnn_class || !exported.results.ml_class)
    throw new Error("Export incomplete");
  await page.getByLabel("对照模型").selectOption("cnn");
  await page.getByRole("columnheader", { name: "1D-CNN · 涨跌分类" }).waitFor();
  await page.getByRole("columnheader", { name: "1D-CNN · 净收益回归" }).waitFor();
  receipt.checks.push("Complete report JSON download");
  for (const width of [1440, 320]) {
    await page.setViewportSize({ width, height: width === 320 ? 720 : 1000 });
    await page.goto(`${base}/?lab=1#experiments`);
    await page.getByRole("link", { name: "返回个人投资" }).waitFor();
    if ((await page.locator("body").innerText()).includes("新版界面需要"))
      throw new Error("Legacy lab rejected current backend");
    const widths = await page.evaluate(() => ({
      viewport: innerWidth,
      content: document.documentElement.scrollWidth,
    }));
    if (widths.content > widths.viewport + 1)
      throw new Error(`Research overflow: ${JSON.stringify(widths)}`);
    if (width === 320)
      await page.screenshot({ path: `${directory}/small-research.png` });
    await page.getByRole("link", { name: "返回个人投资" }).click();
    await page.waitForURL((url) => !url.searchParams.has("lab"));
    await page
      .getByRole("navigation", { name: width === 320 ? "移动端导航" : "主导航" })
      .getByRole("button", { name: "总览" })
      .waitFor();
  }
  receipt.checks.push("Research workspace returns to investor overview on desktop and mobile");
  if (errors.length) throw new Error(errors.join("\n"));
  receipt.errors = errors;
  await fs.writeFile(
    `${directory}/receipt.json`,
    JSON.stringify(receipt, null, 2),
  );
  console.log(JSON.stringify(receipt, null, 2));
} finally {
  await browser.close();
}
