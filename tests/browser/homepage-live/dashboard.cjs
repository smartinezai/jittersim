"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const readline = require("node:readline");
const { spawn } = require("node:child_process");
const modulePath = process.env.JITTERSIM_PLAYWRIGHT_MODULE || "playwright";
const { chromium } = require(modulePath);
const { expect } = require(modulePath + "/test");

const CODE = "a".repeat(32);
const PAGES = ["https://example.org/article", "https://example.org/another"];
const MAC = "02:00:00:00:00:01";
const root = path.resolve(__dirname, "../../..");
const cases = [
  ...["token", "access"].map(loss => ({ loss, kind: "existing" })),
  ...["token", "access"].flatMap(loss =>
    ["pairing-code", "revoke"].flatMap(action =>
      [false, true].flatMap(restored =>
        [200, 500].map(status => ({ loss, action, restored, status, kind: "delayed" }))
      )
    )
  )
];
const selected = process.env.HOMEPAGE_DASHBOARD_CASE;
let fixtureErrors = "";
const evidence = { transport: "real dashboard HTTP with explicitly substituted homepage responses",
  identity: "simulated fixture Wi-Fi commands", checks: [], failures: [] };

function label(item, width) {
  return width + "px " + item.kind + " " + item.loss +
    (item.action ? " " + item.action + " " + item.status + " restored=" + item.restored : "");
}

async function runCase(browser, base, item, width) {
  const context = await browser.newContext({ viewport: { width, height: width < 768 ? 844 : 1000 } });
  context.setDefaultTimeout(12000);
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", error => pageErrors.push(String(error)));
  await page.addInitScript(() => {
    globalThis.fixtureCodeTimers = [];
    const original = globalThis.setTimeout;
    globalThis.setTimeout = (callback, delay, ...args) => {
      if (delay > 110000 && delay <= 120000) {
        fixtureCodeTimers.push(() => callback(...args));
        return 0;
      }
      return original(callback, delay, ...args);
    };
  });
  async function authenticate() {
    if (await page.locator("#credentials-panel").isHidden()) {
      // The narrow sidebar has a separate opener.
      if (!await page.locator("#open-admin-credentials").isVisible()) {
        await page.locator("#controls-toggle").click();
      }
      await page.locator("#open-admin-credentials").click();
    }
    await page.locator("#admin-token").fill("browser-test");
    await page.locator("#check-admin-token").click();
    await expect(page.locator("#token-check-status")).toContainText("Token accepted", { timeout: 20000 });
    if (await page.locator("#private-dashboard").isHidden()) {
      // Phone admission already closes credentials to focus Allow this device.
      if (await page.locator("#credentials-panel").isVisible()) {
        await page.locator("#close-credentials").click();
      }
      await page.locator("#allow-dashboard-device").click();
    }
    await expect(page.locator("#private-dashboard")).toBeVisible({ timeout: 20000 });
    if (await page.locator("#credentials-panel").isVisible()) {
      await page.locator("#close-credentials").click();
    }
    await page.waitForFunction(() => !dashboardRefresh && !adminTokenChecking);
    // Use the page's real navigation rather than direct function invocation.
    const controls = page.locator("#controls-toggle");
    if (await controls.isVisible() && await controls.getAttribute("aria-expanded") === "true") {
      await controls.click();
    }
    const profiles = page.locator('[data-page-link="profiles"]');
    if (!await profiles.isVisible()) await page.locator("#controls-toggle").click();
    await profiles.click();
    const summary = page.locator("summary").filter({ hasText: "Pair homepage test extension" });
    if (!await summary.evaluate(node => node.parentElement.open)) await summary.click();
  }
  async function enroll() {
    await page.locator("#homepage-extension-id").fill("a".repeat(32));
    await page.locator("#homepage-pages").fill(PAGES.join("\n"));
  }
  async function lose() {
    if (item.loss === "token") {
      if (await page.locator("#credentials-panel").isHidden()) {
        if (!await page.locator("#open-admin-credentials").isVisible()) {
          await page.locator("#controls-toggle").click();
        }
        await page.locator("#open-admin-credentials").click();
      }
      await page.locator("#admin-token").fill("replacement-token");
    } else {
      const response = await context.request.post(base + "/api/dashboard-access/remove", {
        headers: { Authorization: "Bearer browser-test" }, data: { mac_address: MAC }
      });
      assert.equal(response.status(), 200, await response.text());
      // A real failed private read drives the normal registration-required path.
      const result = await page.evaluate(async () => {
        try { await request("/api/profiles", "GET"); return "unexpected success"; }
        catch (error) { return error.message; }
      });
      assert.notEqual(result, "unexpected success");
    }
    await expect(page.locator("#private-dashboard")).toBeHidden();
  }
  let held = null;
  let newer = null;
  try {
    await page.goto(base + "/#profiles");
    await authenticate();
    await enroll();
    if (item.kind === "existing") {
      let receivedPages;
      await page.route("**/api/homepage/pairing-code", route => {
        receivedPages = route.request().postDataJSON().pages;
        return route.fulfill({
          contentType: "application/json", body: JSON.stringify({ code: CODE, expires_in: 120 })
        });
      });
      await page.locator("#homepage-create-code").click();
      await expect(page.locator("#homepage-code")).toContainText(CODE);
      assert.deepEqual(receivedPages, PAGES);
      await lose();
      assert.equal(await page.locator("#homepage-code").textContent(), "",
        "The old pairing code is still present in the locked DOM");
      assert.equal(await page.locator("#homepage-extension-id").inputValue(), "");
      assert.equal(await page.locator("#homepage-pages").inputValue(), "");
      await page.evaluate(() => fixtureCodeTimers.forEach(callback => callback()));
      assert.equal(await page.locator("#homepage-code").textContent(), "",
        "The old expiry callback changed the locked output");
    } else {
      const endpoint = "/api/homepage/" + item.action;
      await page.route("**" + endpoint, route => { held = route; });
      await page.locator(item.action === "pairing-code" ? "#homepage-create-code" : "#homepage-revoke").click();
      // Revoking asks first. The request starts when the question is answered.
      if (item.action === "revoke") {
        await page.getByRole("dialog").getByRole("button", { name: "Revoke all pairings", exact: true }).click();
      }
      await expect.poll(() => Boolean(held)).toBe(true);
      if (item.action === "pairing-code") assert.deepEqual(held.request().postDataJSON().pages, PAGES);
      await lose();
      if (item.restored) await authenticate();
      if (item.restored && item.status === 200) {
        await enroll();
        await page.route("**/api/homepage/pairing-code", route => { newer = route; });
        await page.locator("#homepage-create-code").click();
        await expect.poll(() => Boolean(newer)).toBe(true);
        assert.deepEqual(newer.request().postDataJSON().pages, PAGES);
        await expect(page.locator("#homepage-create-code")).toBeDisabled();
        await expect(page.locator("#homepage-revoke")).toBeDisabled();
      }
      const before = await page.locator("#homepage-code").textContent();
      const messageBefore = await page.locator("#message").textContent();
      await held.fulfill({
        status: item.status, contentType: "application/json",
        body: JSON.stringify(item.status === 500 ? { error: "Earlier private session failed" } :
          item.action === "pairing-code" ? { code: CODE, expires_in: 120 } : { revoked: true })
      });
      held = null;
      // Observe the real completed handler rather than only response arrival.
      await page.waitForTimeout(150);
      assert.equal(await page.locator("#homepage-code").textContent(), before,
        "An earlier request changed the private output after access changed");
      assert.equal(await page.locator("#message").textContent(), messageBefore,
        "An earlier request displayed an error in a changed private session");
      assert.ok(!(await page.locator("#homepage-code").textContent()).includes(CODE));
      if (newer) {
        await expect(page.locator("#homepage-create-code")).toBeDisabled();
        await expect(page.locator("#homepage-revoke")).toBeDisabled();
        await newer.fulfill({ contentType: "application/json",
          body: JSON.stringify({ code: "b".repeat(32), expires_in: 120 }) });
        newer = null;
        await expect(page.locator("#homepage-code")).toContainText("b".repeat(32));
        await expect(page.locator("#homepage-create-code")).toBeEnabled();
        await expect(page.locator("#homepage-revoke")).toBeEnabled();
      }
    }
    assert.deepEqual(pageErrors, []);
  } finally {
    if (held) await held.abort().catch(() => {});
    if (newer) await newer.abort().catch(() => {});
    await context.close();
  }
}

(async () => {
  const fixture = spawn(process.env.JITTERSIM_PYTHON_EXECUTABLE || "python",
    ["-m", "tests.browser.fixture"], {
      cwd: root, env: { ...process.env, JITTERSIM_BROWSER_TEST_WIFI_PEER: "1" },
      stdio: ["ignore", "pipe", "pipe"]
    });
  fixture.stderr.on("data", data => { fixtureErrors = (fixtureErrors + data).slice(-32768); });
  const lines = readline.createInterface({ input: fixture.stdout });
  let browser;
  try {
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Fixture did not start: " + fixtureErrors)), 15000);
      lines.once("line", line => { clearTimeout(timer); resolve(Number(line)); });
      fixture.once("exit", code => { clearTimeout(timer); reject(new Error("Fixture exited " + code + ": " + fixtureErrors)); });
      fixture.once("error", reject);
    });
    assert.ok(Number.isInteger(port) && port > 0);
    browser = await chromium.launch({ executablePath: process.env.JITTERSIM_BROWSER_EXECUTABLE, headless: true });
    evidence.browser = browser.version();
    for (const width of [1440, 390]) {
      for (const item of cases) {
        const name = label(item, width);
        if (selected && !name.includes(selected)) continue;
        try {
          await runCase(browser, "http://127.0.0.1:" + port, item, width);
          evidence.checks.push(name);
        } catch (error) {
          evidence.failures.push({ check: name, error: String(error) });
        }
      }
    }
    if (process.env.HOMEPAGE_RESULT_PATH) {
      await fs.writeFile(process.env.HOMEPAGE_RESULT_PATH, JSON.stringify(evidence, null, 2) + "\n");
    }
    console.log(JSON.stringify(evidence, null, 2));
    assert.equal(evidence.failures.length, 0, "Dashboard privacy regressions failed");
    assert.ok(evidence.checks.length);
  } finally {
    if (browser) await browser.close();
    lines.close();
    fixture.kill("SIGTERM");
    await new Promise(resolve => { fixture.once("exit", resolve); setTimeout(resolve, 5000); });
    if (fixture.exitCode === null) fixture.kill("SIGKILL");
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
