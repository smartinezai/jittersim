"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const playwrightModule = process.env.JITTERSIM_PLAYWRIGHT_MODULE || "playwright";
const { chromium } = require(playwrightModule);
const { expect } = require(playwrightModule + "/test");

async function run() {
  const origin = process.env.HOMEPAGE_REFERENCE_ORIGIN;
  const controlOrigin = process.env.HOMEPAGE_REFERENCE_CONTROL_ORIGIN;
  assert.equal(new URL(controlOrigin).hostname, "127.0.0.1");
  const extension = process.env.HOMEPAGE_REFERENCE_EXTENSION;
  const profile = process.env.HOMEPAGE_REFERENCE_PROFILE;
  assert.equal(new URL(origin).hostname, "127.0.0.1");
  const launchBrowser = () => chromium.launchPersistentContext(profile, {
    channel: "chromium",
    executablePath: process.env.JITTERSIM_BROWSER_EXECUTABLE,
    headless: true,
    args: ["--disable-extensions-except=" + extension, "--load-extension=" + extension],
    timeout: 15000,
    viewport: { width: 1000, height: 850 }
  });
  let context = await launchBrowser();
  const evidence = [];
  try {
    context.setDefaultTimeout(4000);
    context.setDefaultNavigationTimeout(5000);
    const worker = context.serviceWorkers()[0] ||
      await context.waitForEvent("serviceworker", { timeout: 5000 });
    const extensionId = new URL(worker.url()).hostname;
    assert.match(extensionId, /^[a-p]{32}$/);
    let ui = await context.newPage();
    const pageErrors = [];
    ui.on("pageerror", error => pageErrors.push(String(error)));
    await ui.goto(`chrome-extension://${extensionId}/index.html`);
    await expect(ui.getByRole("heading", { name: "Homepage bounce reference" })).toBeVisible();
    await expect(ui.locator("#status")).toHaveText("Off by default.");
    const control = async body => {
      const response = await fetch(controlOrigin + "/__reference/control", {
        method: "POST", headers: {
          "Content-Type": "application/json",
          "Authorization": "Harness " + process.env.HOMEPAGE_REFERENCE_HARNESS
        }, body: JSON.stringify(body), signal: AbortSignal.timeout(2000)
      }).catch(error => {
        throw new Error("Fixture control failed for " + JSON.stringify(body) + ": " +
          String(error.cause || error));
      });
      assert.equal(response.status, 200);
      return response.json();
    };
    const counts = async () => (await control({ read_counts: true })).counts;
    const state = () => ui.evaluate(() => chrome.runtime.sendMessage({ command: "status" }));
    const enable = async () => {
      await ui.getByRole("button", { name: "Enable reference effect", exact: true }).click();
      await expect(ui.locator("#status")).toHaveText("Enabled for reference actions");
    };
    const pair = async () => {
      const { code } = await control({ event: "pairing-code" });
      await ui.locator("#pair-code").fill(code);
      await ui.getByRole("button", { name: "Pair simulated device", exact: true }).click();
      await expect(ui.locator("#status")).toHaveText("Simulated device paired. Effect is off.");
      assert.deepEqual(await state(), { ok: true, enabled: false, paired: true });
      return code;
    };
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
      ["Transit consumes a short lease", { mode: "short-lease", delay: 0.2 }, "/pages/article"],
      ["Malformed chance is rejected", { mode: "malformed" }, "/pages/article"],
      ["A policy naming a private origin is rejected", { mode: "wrong-origin" }, "/pages/article"],
      ["A reply for another request is rejected", { mode: "wrong-request" }, "/pages/article"],
      ["A regressed policy revision is rejected", { mode: "old-revision" }, "/pages/article"],
      ["An enabled policy for protected identity is rejected", { mode: "invalid-identity" }, "/pages/article"],
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
    let home = await launch("/");
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

    await control({ delay: 0.4, general: 100 });
    await enable();
    const beforeConcurrent = (await counts()).policy;
    await ui.evaluate(() => {
      globalThis.firstAction = chrome.runtime.sendMessage({ command: "launch", action: "notes" });
    });
    await expect.poll(async () => (await counts()).policy).toBe(beforeConcurrent + 1);
    const refused = await ui.evaluate(() => chrome.runtime.sendMessage({ command: "launch", action: "article" }));
    assert.equal(refused.ok, false);
    assert.equal(refused.status, "An action is already pending");
    await ui.getByRole("button", { name: "Off", exact: true }).click();
    await enable();
    assert.equal((await ui.evaluate(() => globalThis.firstAction)).ok, false);
    assert.equal(home.url(), oldURL);
    assert.equal((await counts()).policy, beforeConcurrent + 1);
    evidence.push("One request is allowed at a time and Off followed by Enable cannot revive it");
    // Let the artificial server delay finish before the next independent case.
    await new Promise(resolve => setTimeout(resolve, 450));

    await control({ general: 100 });
    await ui.locator("#pair-code").fill(process.env.HOMEPAGE_REFERENCE_CODE);
    await ui.getByRole("button", { name: "Pair simulated device", exact: true }).click();
    await expect(ui.locator("#status")).toHaveText("Pairing unavailable");
    assert.deepEqual(await state(), { ok: true, enabled: false, paired: false });
    const beforeUnpaired = (await counts()).policy_attempts;
    await launch("/pages/notes", "Open notes");
    assert.equal((await counts()).policy_attempts, beforeUnpaired);
    evidence.push("Replayed pairing is refused and failed re-pairing removes the old local session");

    await pair();
    await enable();
    await control({ pair_delay: 0.4 });
    const { code: delayedCode } = await control({ event: "pairing-code" });
    const beforePair = (await counts()).pair;
    await ui.locator("#pair-code").fill(delayedCode);
    await ui.getByRole("button", { name: "Pair simulated device", exact: true }).click();
    await expect.poll(async () => (await counts()).pair).toBe(beforePair + 1);
    await expect(ui.getByRole("button", { name: "Enable reference effect", exact: true })).toBeDisabled();
    const duringPair = await ui.evaluate(() => chrome.runtime.sendMessage({ command: "enable" }));
    assert.equal(duringPair.ok, false);
    assert.deepEqual(await state(), { ok: true, enabled: false, paired: false });
    await expect(ui.locator("#status")).toHaveText("Simulated device paired. Effect is off.");
    assert.deepEqual(await state(), { ok: true, enabled: false, paired: true });
    await enable();
    await control({ general: 100 });
    home = await launch("/");
    evidence.push("Enable during an in-progress pair is refused and successful pairing stays off");

    for (const mode of ["pair-malformed", "pair-invalid-token"]) {
      await control({ mode });
      const { code: malformedCode } = await control({ event: "pairing-code" });
      await ui.locator("#pair-code").fill(malformedCode);
      await ui.getByRole("button", { name: "Pair simulated device", exact: true }).click();
      await expect(ui.locator("#status")).toHaveText("Pairing unavailable");
      assert.deepEqual(await state(), { ok: true, enabled: false, paired: false });
    }
    evidence.push("Unexpected pairing fields and non-string credentials cannot restore a local session");

    await control({ pair_delay: 0.4 });
    const { code: cancelledCode } = await control({ event: "pairing-code" });
    const beforeCancelledPair = (await counts()).pair;
    await ui.locator("#pair-code").fill(cancelledCode);
    await ui.getByRole("button", { name: "Pair simulated device", exact: true }).click();
    await expect.poll(async () => (await counts()).pair).toBe(beforeCancelledPair + 1);
    await ui.getByRole("button", { name: "Off", exact: true }).click();
    await expect(ui.locator("#status")).toHaveText("Off. Pending action cancelled.");
    await expect(ui.getByRole("button", { name: "Pair simulated device", exact: true })).toBeEnabled();
    await new Promise(resolve => setTimeout(resolve, 450));
    await expect(ui.locator("#status")).toHaveText("Off. Pending action cancelled.");
    assert.deepEqual(await state(), { ok: true, enabled: false, paired: false });
    evidence.push("Off during pairing prevents session restoration and an older interface reply");

    for (const event of ["expire-session", "restart"]) {
      await control({ general: 100 });
      await pair();
      await enable();
      await control({ event });
      await launch("/pages/article");
      await expect(ui.locator("#status")).toHaveText("Pairing lost. Effect off. Opened the test page.");
      assert.deepEqual(await state(), { ok: true, enabled: false, paired: false });
      const beforeNext = (await counts()).policy_attempts;
      await launch("/pages/notes", "Open notes");
      assert.equal((await counts()).policy_attempts, beforeNext);
      evidence.push(`Simulated ${event} loses pairing and cannot silently re-enable`);
    }

    await control({ general: 100 });
    await pair();
    await enable();
    home = await launch("/");
    await control({ delay: 0.4, general: 100 });
    const beforeClose = (await counts()).policy;
    await ui.getByRole("button", { name: "Open notes", exact: true }).click();
    await expect.poll(async () => (await counts()).policy).toBe(beforeClose + 1);
    await home.close();
    await expect(ui.locator("#status")).toHaveText("Test tab closed. Pending action cancelled.");
    await new Promise(resolve => setTimeout(resolve, 450));
    assert.equal(context.pages().filter(page => page.url().startsWith(origin + "/")).length, 0);
    await control({ general: 0 });
    home = await launch("/pages/notes", "Open notes");
    assert.equal(context.pages().filter(page => page.url().startsWith(origin + "/")).length, 1);
    evidence.push("Closing the test tab cancels pending navigation and a fresh action creates one tab");

    const debug = await context.newCDPSession(ui);
    const versions = new Map();
    debug.on("ServiceWorker.workerVersionUpdated", ({ versions: updates }) => {
      for (const version of updates) versions.set(version.versionId, version);
    });
    await debug.send("ServiceWorker.enable");
    await expect.poll(() => [...versions.values()].some(version =>
      version.scriptURL === worker.url() && version.runningStatus === "running")).toBe(true);
    const running = [...versions.values()].find(version =>
      version.scriptURL === worker.url() && version.runningStatus === "running");
    await debug.send("ServiceWorker.stopWorker", { versionId: running.versionId });
    await expect.poll(() => versions.get(running.versionId).runningStatus).toBe("stopped");
    assert.deepEqual(await state(), { ok: true, enabled: false, paired: true });
    await expect.poll(() => versions.get(running.versionId).runningStatus).toBe("running");
    const beforeWorkerAction = (await counts()).policy_attempts;
    await launch("/pages/article");
    assert.equal((await counts()).policy_attempts, beforeWorkerAction);
    await control({ general: 100 });
    await enable();
    home = await launch("/");
    assert.equal(context.pages().filter(page => page.url().startsWith(origin + "/")).length, 1);
    await debug.detach();
    evidence.push("A real worker restart preserves pairing and the dedicated tab while resetting Enable");

    const manifest = JSON.parse(await fs.readFile(path.join(extension, "manifest.json"), "utf8"));
    assert.deepEqual(manifest.permissions, ["storage"]);
    assert.deepEqual(manifest.host_permissions, ["http://127.0.0.1/*"]);
    assert.equal(manifest.content_scripts, undefined);
    const storage = await ui.evaluate(() => chrome.storage.session.get(null));
    assert.deepEqual(Object.keys(storage).sort(), ["referenceSession", "referenceTab"]);
    assert.equal(storage.referenceSession.capability.length, 64);
    assert.equal(JSON.stringify(storage).includes(process.env.HOMEPAGE_REFERENCE_HARNESS), false);
    assert.equal(pageErrors.length, 0, pageErrors.join("\n"));
    evidence.push("Installed extension has no history or browsing-monitor permissions");
    await context.close();
    context = await launchBrowser();
    context.setDefaultTimeout(4000);
    ui = await context.newPage();
    await ui.goto(`chrome-extension://${extensionId}/index.html`);
    assert.deepEqual(await state(), { ok: true, enabled: false, paired: false });
    const beforeBrowserAction = (await counts()).policy_attempts;
    await launch("/pages/article");
    assert.equal((await counts()).policy_attempts, beforeBrowserAction);
    evidence.push("A real browser restart clears the session credential and keeps the effect off");
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
