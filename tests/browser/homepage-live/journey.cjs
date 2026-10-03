"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { startDocumentTransport, withDeadline } = require("./transport.cjs");
const modulePath = process.env.JITTERSIM_PLAYWRIGHT_MODULE || "playwright";
const { chromium } = require(modulePath);
const { expect } = require(modulePath + "/test");

(async () => {
  const extension = path.resolve(__dirname, "../../../extensions/homepage-bounce");
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), "homepage-live-browser-"));
  let transport;
  let context;
  try {
    transport = await startDocumentTransport();
    context = await chromium.launchPersistentContext(profile, {
      executablePath: process.env.JITTERSIM_BROWSER_EXECUTABLE,
      headless: true,
      channel: "chromium",
      args: ["--disable-extensions-except=" + extension, "--load-extension=" + extension, ...transport.args],
      timeout: 15000
    });
    const evidence = [];
    let worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
    const extensionId = new URL(worker.url()).host;
    // This journey verifies the real extension with a substituted transport.
    // Normal certificate verification is tested separately by the HTTPS suite.
    await worker.evaluate(() => {
      globalThis.fixture = { chance: 100, delay: 0, rejected: false, requests: 0 };
      globalThis.fetch = async (url, options) => {
        const f = globalThis.fixture;
        const input = JSON.parse(options.body);
        f.requests += 1;
        await new Promise((resolve, reject) => {
          const timer = setTimeout(resolve, f.delay);
          options.signal.addEventListener("abort", () => {
            clearTimeout(timer);
            reject(new DOMException("Cancelled", "AbortError"));
          }, { once: true });
        });
        const pair = url.endsWith("/homepage/pair");
        const body = pair ? { kind: "homepage-pair", capability: "a".repeat(64), revision: 1,
          pages: [{ origin: "https://example.org", path: "/article" }] } : {
          kind: "homepage-policy", enabled: true, chance: f.chance, revision: 1,
          lease_ms: 1000, origin: input.origin, request_id: input.request_id
        };
        const response = new Response(JSON.stringify(body), {
          status: f.rejected ? 403 : 200, headers: { "Content-Type": "application/json" }
        });
        Object.defineProperty(response, "url", { value: url });
        return response;
      };
    });
    const ui = await context.newPage();
    await ui.goto("chrome-extension://" + extensionId + "/index.html");
    const state = () => ui.evaluate(() => chrome.runtime.sendMessage({ command: "status" }));
    assert.equal((await state()).enabled, false);
    assert.equal((await state()).paired, false);
    await ui.locator("#pair-code").fill("a".repeat(32));
    await ui.getByRole("button", { name: "Pair device", exact: true }).click();
    await expect.poll(async () => (await state()).paired).toBe(true);
    assert.equal((await state()).enabled, false);
    assert.equal(await ui.locator("#pair-code").inputValue(), "");
    evidence.push("Pairing clears the code and stays off");
    const launch = async expected => {
      const beforeReads = transport.reads.length;
      await ui.getByRole("button", { name: "Open https://example.org/article", exact: true }).click();
      await expect.poll(() => context.pages().some(page => page.url() === expected)).toBe(true);
      const page = context.pages().find(page => page.url() === expected);
      await expect(page.locator("h1")).toHaveText("Controlled HTTPS test page");
      await expect.poll(() => transport.reads.slice(beforeReads).includes(new URL(expected).pathname)).toBe(true);
      return page;
    };
    let test = await launch("https://example.org/article");
    assert.equal(await worker.evaluate(() => fixture.requests), 1);
    evidence.push("Off opens only the named page without policy traffic");
    await ui.getByRole("button", { name: "Enable test effect", exact: true }).click();
    test = await launch("https://example.org/");
    evidence.push("A fresh 100 percent approval chooses only the same origin home page");
    await worker.evaluate(() => { fixture.chance = 0; });
    test = await launch("https://example.org/article");
    evidence.push("A fresh zero percent approval preserves the named page");
    await worker.evaluate(() => { fixture.delay = 500; fixture.chance = 100; });
    const before = await worker.evaluate(() => fixture.requests);
    await ui.getByRole("button", { name: "Open https://example.org/article", exact: true }).click();
    await expect.poll(() => worker.evaluate(() => fixture.requests)).toBe(before + 1);
    await ui.getByRole("button", { name: "Off", exact: true }).click();
    await expect(ui.locator("#status")).toContainText("Pending action cancelled");
    await new Promise(resolve => setTimeout(resolve, 600));
    assert.equal(test.url(), "https://example.org/article");
    evidence.push("Off cancels a pending lookup and prevents delayed navigation");
    await ui.getByRole("button", { name: "Enable test effect", exact: true }).click();
    const beforeClose = await worker.evaluate(() => fixture.requests);
    await ui.getByRole("button", { name: "Open https://example.org/article", exact: true }).click();
    await expect.poll(() => worker.evaluate(() => fixture.requests)).toBe(beforeClose + 1);
    await test.close();
    await new Promise(resolve => setTimeout(resolve, 600));
    assert.equal(context.pages().filter(page => page.url().startsWith("https://example.org/")).length, 0);
    evidence.push("Closing the test tab cancels its request without opening a replacement");
    await worker.evaluate(() => { fixture.delay = 0; fixture.rejected = true; });
    await launch("https://example.org/article");
    assert.equal((await state()).paired, false);
    assert.equal((await state()).enabled, false);
    evidence.push("Credential rejection clears pairing and turns the effect off");
    assert.deepEqual(await ui.evaluate(() => chrome.runtime.getManifest().host_permissions),
      ["https://netlab.local:8443/*"]);
    assert.deepEqual(await ui.evaluate(() => chrome.runtime.getManifest().permissions), ["storage"]);
    evidence.push("The real manifest has only storage and the exact HTTPS policy origin");
    assert(transport.reads.includes("/article") && transport.reads.includes("/"));
    console.log(JSON.stringify({ documentRequests:transport.reads, transport: "substituted-policy-and-loopback-HTTPS-documents", browser: context.browser().version(), checks: evidence }, null, 2));
  } finally {
    try { if (context) await withDeadline(context.close(), 15000); } finally {
      try { if (transport) await transport.close(); } finally { await fs.rm(profile, { recursive: true, force: true }); }
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
