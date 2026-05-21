import { test } from "@playwright/test";

test("dump plans page text", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".sidebar-nav-link", { timeout: 15_000 });
  await page.locator(".sidebar-nav-link").nth(2).click();
  await page.waitForSelector(".ops-queue", { timeout: 5_000 });

  const queueDump = await page.locator(".queue-card").evaluateAll((els) =>
    els.map((el) => ({
      index: el.querySelector(".queue-card-index")?.textContent ?? null,
      title: el.querySelector(".queue-card-title")?.textContent ?? null,
      body: el.querySelector(".queue-card-text")?.textContent ?? null,
      countText: el.querySelector(".queue-card-count")?.textContent ?? null,
      countHtml: el.querySelector(".queue-card-count")?.innerHTML?.slice(0, 200) ?? null,
      classList: el.className
    }))
  );
  console.log("\n=== OPERATIONS QUEUE ===");
  for (const c of queueDump) {
    console.log(JSON.stringify(c, null, 2));
  }

  const chipDump = await page.locator(".night-chip").evaluateAll((els) =>
    els.map((el) => ({
      month: el.querySelector(".night-chip-month")?.textContent ?? null,
      num: el.querySelector(".night-chip-num")?.textContent ?? null,
      day: el.querySelector(".night-chip-day")?.textContent ?? null,
      status: el.querySelector(".night-chip-status")?.textContent ?? null,
      classList: el.className
    }))
  );
  console.log("\n=== NIGHT CHIPS ===");
  for (const c of chipDump) {
    console.log(JSON.stringify(c));
  }

  // Also dump the raw service plan window from the API for cross-check.
  const apiSnapshot = await page.evaluate(async () => {
    const res = await fetch("/api/operators/balthazar_nyc/workspace");
    if (!res.ok) return { error: res.status };
    const data = await res.json();
    return {
      windowLabel: data.dashboard?.servicePlanWindow?.windowLabel ?? null,
      dueCount: data.dashboard?.servicePlanWindow?.dueCount ?? null,
      pendingDates: data.dashboard?.servicePlanWindow?.pendingDates ?? null,
      entries: (data.dashboard?.servicePlanWindow?.entries ?? []).map((e: { serviceDate: string; serviceState: string; reviewed: boolean }) => ({
        serviceDate: e.serviceDate,
        serviceState: e.serviceState,
        reviewed: e.reviewed
      })),
      missingActuals: data.dashboard?.missingActuals?.length ?? 0,
      pendingSuggestions: data.dashboard?.openServiceStateSuggestions?.length ?? 0
    };
  });
  console.log("\n=== API SNAPSHOT ===");
  console.log(JSON.stringify(apiSnapshot, null, 2));
});
