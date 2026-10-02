"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const playwrightModule = process.env.JITTERSIM_PLAYWRIGHT_MODULE || "playwright";
const { chromium } = require(playwrightModule);
const { expect } = require(playwrightModule + "/test");

async function run() {
  const origin = process.env.HOMEPAGE_REFERENCE_ORIGIN;
  const extension = process.env.HOMEPAGE_REFERENCE_EXTENSION;
  const profile = process.env.HOMEPAGE_REFERENCE_PROFILE;
  assert.equal(new URL(origin).hostname, "127.0.0.1");
  const context = await chromium.launchPersistentContext(profile, {
    executablePath: process.env.JITTERSIM_BROWSER_EXECUTABLE,
    headless: true,
    args: ["--disable-extensions-except=" + extension, "--load-extension=" + extension],
    timeout: 15000,
    viewport: { width: 1000, height: 850 }
  });
  const evidence = [];
  try {
    context.setDefaultTimeout(4000);
    context.setDefaultNavigationTimeout(5000);
    const worker = context.serviceWorkers()[0] ||
      await context.waitForEvent("serviceworker", { timeout: 5000 });
    const extensionId = new URL(worker.url()).hostname;
    assert.match(extensionId, /^[a-p]{32}$/);
    const ui = await context.newPage();
    const pageErrors = [];
    ui.on("pageerror", error => pageErrors.push(String(error)));
    await ui.goto(`chrome-extension://${extensionId}/index.html`);
    await expect(ui.getByRole("heading", { name: "Homepage bounce reference" })).toBeVisible();
    await expect(ui.locator("#status")).toHaveText("Off by default.");
    const control = async body => {
      const response = await fetch(origin + "/__reference/control", {
        method: "POST", headers: {
          "Content-Type": "application/json",
          "Authorization": "Harness " + process.env.HOMEPAGE_REFERENCE_HARNESS
        }, body: JSON.stringify(body), signal: AbortSignal.timeout(2000)
      });
      assert.equal(response.status, 200);
      return response.json();
    };
    const counts = async () => (await control({ read_counts: true })).counts;
    const testPage = async expected => {
      const servedPage = () => context.pages().find(
        candidate => candidate.url().startsWith(origin + "/")
      );
      await expect.poll(() => !!servedPage(), { timeout: 4000 }).toBe(true);
      const page = servedPage();
      assert.ok(page);
      await page.waitForURL(origin + expected, { timeout: 4000 });
      await expect(page.getByRole("heading")).toHaveText(
        expected === "/" ? "Reference home" :
          expected === "/pages/article" ? "Reference article" : "Reference notes"
      );
      return page;
    };
    const launch = async (expected, action = "Open article") => {
      await ui.getByRole("button", { name: action, exact: true }).click();
      await expect(ui.locator("#status")).toContainText(/Opened the test page|home page/);
      return testPage(expected);
    };

    await launch("/pages/article");
    assert.equal((await counts()).policy || 0, 0);
    evidence.push("Off default preserves the named page without a policy request");

    await ui.locator("#pair-code").fill(process.env.HOMEPAGE_REFERENCE_CODE);
    await ui.getByRole("button", { name: "Pair simulated device", exact: true }).click();
    await expect(ui.locator("#status")).toHaveText("Simulated device paired. Effect is off.");
    assert.equal(await ui.locator("#pair-code").inputValue(), "");
    await ui.getByRole("button", { name: "Enable reference effect", exact: true }).click();
    await expect(ui.locator("#status")).toHaveText("Enabled for reference actions");
    evidence.push("Disposable simulated pairing uses a separate credential and clears the input");

    const cases = [
      ["0% keeps the deep page", { general: 0 }, "/pages/article"],
      ["100% chooses the exact origin root", { general: 100 }, "/"],
      ["Service-list zero wins before general 100", { service: 0, general: 100 }, "/pages/article"],
      ["General 100 wins before device zero", { general: 100, device: 0 }, "/"],
      ["Protected identity disables bounce", { protected: true }, "/pages/article"],
      ["Unknown identity disables bounce", { identity: "unknown" }, "/pages/article"],
      ["Unavailable policy leaves the named page usable", { mode: "unavailable" }, "/pages/article"],
      ["Expired lease disables bounce", { mode: "stale" }, "/pages/article"],
      ["Malformed chance is rejected", { mode: "malformed" }, "/pages/article"],
      ["A policy naming a private origin is rejected", { mode: "wrong-origin" }, "/pages/article"],
      ["Oversized policy is rejected", { mode: "oversized" }, "/pages/article"],
      ["Lookup timeout leaves the named page usable", { delay: 1 }, "/pages/article"]
    ];
    for (const [description, config, expected] of cases) {
      await control(config);
      const before = (await counts()).policy || 0;
      await launch(expected);
      assert.equal((await counts()).policy, before + 1);
      evidence.push(description);
    }

    await control({ general: 100 });
    const home = await launch("/");
    const beforeReload = await counts();
    await home.reload();
    await expect(home.getByRole("heading")).toHaveText("Reference home");
    const afterReload = await counts();
    assert.equal(afterReload.policy, beforeReload.policy);
    assert.equal(afterReload["/"], beforeReload["/"] + 1);
    evidence.push("Reloading the home page makes no repeated chance decision");

    await control({ delay: 1, general: 100 });
    const beforeLate = (await counts()).policy;
    const oldURL = home.url();
    await ui.getByRole("button", { name: "Open notes", exact: true }).click();
    await expect.poll(async () => (await counts()).policy, { timeout: 1500 }).toBe(beforeLate + 1);
    await ui.getByRole("button", { name: "Off", exact: true }).click();
    await expect(ui.locator("#status")).toContainText("Pending action cancelled");
    await new Promise(resolve => setTimeout(resolve, 1100));
    assert.equal(home.url(), oldURL);
    await expect(ui.locator("#status")).toContainText("Pending action cancelled");
    evidence.push("Off cancels a real outstanding lookup and prevents late navigation");

    for (const action of ["http://192.168.50.1:8080/", "callback", "download", "__proto__"]) {
      const result = await ui.evaluate(action => chrome.runtime.sendMessage({
        command: "launch", action
      }), action);
      assert.equal(result.ok, false);
      assert.equal(home.url(), oldURL);
    }
    evidence.push("Private URLs and unenrolled callback/download actions cannot navigate");

    const manifest = JSON.parse(await fs.readFile(path.join(extension, "manifest.json"), "utf8"));
    assert.deepEqual(manifest.permissions, ["storage"]);
    assert.deepEqual(manifest.host_permissions, ["http://127.0.0.1/*"]);
    assert.equal(manifest.content_scripts, undefined);
    const storage = await worker.evaluate(() => chrome.storage.session.get(null));
    assert.deepEqual(Object.keys(storage).sort(), ["referenceSession", "referenceTab"]);
    assert.equal(storage.referenceSession.capability.length, 64);
    assert.equal(JSON.stringify(storage).includes(process.env.HOMEPAGE_REFERENCE_HARNESS), false);
    assert.equal(pageErrors.length, 0, pageErrors.join("\n"));
    evidence.push("Installed extension has no history or browsing-monitor permissions");
    console.log(JSON.stringify({ result: "passed", checks: evidence.length, evidence }, null, 2));
  } finally {
    let cleanupTimer;
    try {
      await Promise.race([
        context.close(),
        new Promise((_, reject) => {
          cleanupTimer = setTimeout(() => reject(new Error("Browser cleanup timed out")), 5000);
        })
      ]);
    } finally { clearTimeout(cleanupTimer); }
  }
}

run().catch(error => { console.error(error.stack || String(error)); process.exitCode = 1; });
