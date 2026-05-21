import { test, expect, type Page, type ConsoleMessage } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const screenshotDir = path.resolve(currentDir, "..", "playwright-out");

type CapturedConsole = {
  type: string;
  text: string;
  location: string;
};

function attachConsoleCapture(page: Page): CapturedConsole[] {
  const captured: CapturedConsole[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() === "error" || msg.type() === "warning") {
      const loc = msg.location();
      captured.push({
        type: msg.type(),
        text: msg.text(),
        location: loc.url ? `${loc.url}:${loc.lineNumber}` : "(no loc)"
      });
    }
  });
  page.on("pageerror", (err) => {
    captured.push({ type: "pageerror", text: String(err.message ?? err), location: "" });
  });
  page.on("requestfailed", (req) => {
    captured.push({
      type: "requestfailed",
      text: `${req.method()} ${req.url()} — ${req.failure()?.errorText ?? "unknown"}`,
      location: ""
    });
  });
  return captured;
}

test.describe("StormReady frontend smoke", () => {
  test("walks Chat -> Forecast -> Plans on default operator", async ({ page }) => {
    const issues = attachConsoleCapture(page);

    // Use domcontentloaded — chat history pagination keeps long-poll style
    // requests open, so networkidle never settles.
    await page.goto("/", { waitUntil: "domcontentloaded" });

    // Wait for the app shell to render and the default operator's name in sidebar.
    await expect(page.locator(".app-shell")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".sidebar-operator-name, .sidebar-current-operator, h2").first()).toBeVisible({
      timeout: 15_000
    });

    // Capture which operator is loaded — sidebar shows the active operator.
    const activeOperator = await page
      .locator(".sidebar-operator-active, .sidebar-operator-name, .operator-status, .sidebar-active-operator")
      .first()
      .textContent()
      .catch(() => null);
    console.log("Active operator (best guess):", activeOperator);

    // Page state will already be one of chat/forecast/plans depending on auto-routing.
    // Walk all three explicitly via sidebar buttons.

    // Sidebar nav links — use class hooks rather than role+name so we don't
    // get bitten by label changes (e.g. "Plans & Actuals" vs "Plans").
    const navButtons = page.locator(".sidebar-nav-link");

    // --- CHAT ---
    await navButtons.nth(0).click({ timeout: 5_000 });
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(screenshotDir, "01-chat.png"), fullPage: true });

    // --- FORECAST ---
    await navButtons.nth(1).click({ timeout: 5_000 });
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(screenshotDir, "02-forecast.png"), fullPage: true });

    // Try the glyph legend popover
    const legendBtn = page.locator(".glyph-legend-trigger, [aria-label*='legend' i]").first();
    if (await legendBtn.count()) {
      await legendBtn.click({ timeout: 1500 }).catch(() => {});
      await page.waitForTimeout(250);
      await page.screenshot({
        path: path.join(screenshotDir, "02b-forecast-legend.png"),
        fullPage: true
      });
      // close popover
      await page.keyboard.press("Escape").catch(() => {});
    }

    // Try the table view toggle (icon-tab inside dashboard-toolbar)
    const tableToggle = page
      .locator(".dashboard-toolbar .icon-tab")
      .nth(1);
    if (await tableToggle.count()) {
      await tableToggle.click({ timeout: 1500 }).catch(() => {});
      await page.waitForTimeout(400);
      await page.screenshot({
        path: path.join(screenshotDir, "02c-forecast-table.png"),
        fullPage: true
      });
      // switch back to detail
      const detailToggle = page.locator(".dashboard-toolbar .icon-tab").first();
      await detailToggle.click({ timeout: 1500 }).catch(() => {});
    }

    // --- PLANS ---
    await navButtons.nth(2).click({ timeout: 5_000 });
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(screenshotDir, "03-plans.png"), fullPage: true });

    // Mobile breakpoint check on Plans (most layout-dense page)
    await page.setViewportSize({ width: 900, height: 1000 });
    await page.waitForTimeout(300);
    await page.screenshot({
      path: path.join(screenshotDir, "03b-plans-narrow.png"),
      fullPage: true
    });
    await page.setViewportSize({ width: 1440, height: 900 });

    // Report any captured issues for human review (don't fail the run on warnings).
    if (issues.length > 0) {
      console.log("\n=== Console / network issues captured ===");
      for (const i of issues) {
        console.log(`[${i.type}] ${i.text} ${i.location}`);
      }
    } else {
      console.log("\n=== No console errors / failed requests captured ===");
    }

    // Hard-fail only on uncaught page errors and request failures, not warnings.
    const fatal = issues.filter((i) => i.type === "pageerror" || i.type === "requestfailed");
    expect(fatal, `Fatal browser errors:\n${fatal.map((f) => f.text).join("\n")}`).toEqual([]);
  });
});
