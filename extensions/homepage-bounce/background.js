"use strict";
importScripts("runtime-config.js");

const origin = new URL(HOMEPAGE_CONFIG.origin);
if (origin.protocol !== "https:" || origin.hostname !== "netlab.local" ||
    origin.port !== "8443" || origin.pathname !== "/" || origin.search || origin.hash ||
    origin.username || origin.password) throw new Error("Invalid policy origin");
const base = origin.origin;
let actions = {};
function safePages(pages) {
  if (!Array.isArray(pages) || pages.length < 1 || pages.length > 8) return null;
  const result = {};
  for (const [index, page] of pages.entries()) {
    if (!page || Object.keys(page).sort().join(",") !== "origin,path" ||
        typeof page.origin !== "string" || typeof page.path !== "string") return null;
    let url;
    try { url = new URL(page.origin); } catch { return null; }
    if (url.protocol !== "https:" || url.port || url.username || url.password ||
        url.pathname !== "/" || url.search || url.hash || url.origin !== page.origin ||
        !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(url.hostname) ||
        /\.(local|localhost|home\.arpa|internal|test|invalid)$/.test(url.hostname) ||
        !/^\/[A-Za-z0-9/_-]{1,255}$/.test(page.path) || page.path === "/" || page.path.startsWith("//")) return null;
    result[String(index)] = Object.freeze({ origin: page.origin, path: page.path });
  }
  return Object.freeze(result);
}
let enabled = false;
let capability = null;
let lastRevision = 0;
let epoch = 0;
let pending = null;
let pairing = false;
let cancellation = "Pending action cancelled.";
let testTab = null;
const ready = chrome.storage.session.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" }).then(() => chrome.storage.session.get(["homepageSession", "homepageTab"])).then(
  ({ homepageSession, homepageTab }) => {
  if (homepageSession && typeof homepageSession.capability === "string" &&
      /^[a-f0-9]{64}$/.test(homepageSession.capability) &&
      Number.isSafeInteger(homepageSession.revision) && homepageSession.revision >= 1 && safePages(homepageSession.pages)) {
    actions = safePages(homepageSession.pages);
    capability = homepageSession.capability;
    lastRevision = homepageSession.revision;
  }
  if (Number.isSafeInteger(homepageTab) && homepageTab >= 0) testTab = homepageTab;
});

class SessionRejected extends Error {}

function cancelPending(status) {
  cancellation = status;
  epoch += 1;
  pending?.abort();
}

async function boundedJSON(path, body, credential, controller) {
  const timer = setTimeout(() => controller.abort(), 750);
  try {
    const response = await fetch(base + path, {
      method: "POST", redirect: "error", cache: "no-store", credentials: "omit",
      headers: { "Content-Type": "application/json", ...(credential ? {
        "Authorization": "Homepage " + credential
      } : {}) },
      body: JSON.stringify(body), signal: controller.signal
    });
    if (credential && [401, 403].includes(response.status)) {
      controller.abort();
      throw new SessionRejected();
    }
    if (!response.ok || response.url !== base + path ||
        !/^application\/json(?:;|$)/i.test(response.headers.get("Content-Type") || "")) {
      controller.abort();
      throw new Error("Policy unavailable");
    }
    const reader = response.body.getReader();
    const chunks = [];
    let length = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.length;
      if (length > 8192) {
        await reader.cancel();
        throw new Error("Policy too large");
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } finally { clearTimeout(timer); }
}

function validPolicy(policy, requestId, started, target) {
  return policy && typeof policy === "object" && !Array.isArray(policy) &&
    Object.keys(policy).sort().join(",") ===
      "chance,enabled,kind,lease_ms,origin,request_id,revision" &&
    policy.kind === "homepage-policy" && policy.origin === target && policy.request_id === requestId &&
    Number.isInteger(policy.chance) && policy.chance >= 0 && policy.chance <= 100 &&
    typeof policy.enabled === "boolean" && Number.isSafeInteger(policy.revision) &&
    policy.revision === lastRevision && Number.isInteger(policy.lease_ms) &&
    policy.lease_ms > 0 && policy.lease_ms <= 1000 &&
    performance.now() - started < policy.lease_ms;
}

async function navigate(page, path, currentEpoch) {
  const url = new URL(path, page.origin);
  if (url.origin !== page.origin || !["/", page.path].includes(url.pathname) ||
      url.search || url.hash || url.username || url.password) {
    throw new Error("Navigation refused");
  }
  if (testTab !== null) {
    try { await chrome.tabs.update(testTab, { url: url.href, active: false }); return; }
    catch { testTab = null; }
  }
  if (epoch !== currentEpoch) throw new Error("Action cancelled");
  const tab = await chrome.tabs.create({ url: url.href, active: false });
  testTab = tab.id;
  await chrome.storage.session.set({ homepageTab: testTab });
}

chrome.tabs.onRemoved.addListener(id => {
  if (id !== testTab) return;
  testTab = null;
  cancelPending("Test tab closed. Pending action cancelled.");
  chrome.storage.session.remove("homepageTab").catch(() => {});
});

async function handle(message) {
  await ready;
  if (!message || typeof message !== "object") return { ok: false, status: "Action refused" };
  if (message.command === "status") return { ok: true, enabled, paired: !!capability, pages: Object.values(actions), extensionId: chrome.runtime.id };
  if (message.command === "off") {
    enabled = false;
    cancelPending("Off. Pending action cancelled.");
    return { ok: true, status: "Off. Pending action cancelled." };
  }
  if (message.command === "enable") {
    if (pairing) return { ok: false, status: "Pairing in progress. Effect is off." };
    enabled = !!capability;
    return { ok: enabled, status: enabled ? "Enabled for enrolled test pages" : "Pair first" };
  }
  if (message.command === "pair") {
    if (pending || typeof message.code !== "string" || !/^[a-f0-9]{32}$/.test(message.code)) {
      return { ok: false, status: "Pairing refused" };
    }
    const controller = new AbortController();
    pending = controller;
    const currentEpoch = ++epoch;
    enabled = false;
    pairing = true;
    capability = null;
    lastRevision = 0;
    actions = {};
    try {
      await chrome.storage.session.remove("homepageSession");
      if (currentEpoch !== epoch) throw new Error("Pairing cancelled");
      const result = await boundedJSON("/homepage/pair", { code: message.code }, null, controller);
      if (currentEpoch !== epoch || !result || typeof result !== "object" ||
          Array.isArray(result) || Object.keys(result).sort().join(",") !==
            "capability,kind,pages,revision" || !safePages(result.pages) ||
          result.kind !== "homepage-pair" || typeof result.capability !== "string" ||
          !/^[a-f0-9]{64}$/.test(result.capability) ||
          !Number.isSafeInteger(result.revision) || result.revision < 1) {
        throw new Error("Invalid pairing response");
      }
      await chrome.storage.session.set({ homepageSession: {
        capability: result.capability, revision: result.revision, pages: result.pages
      } });
      if (currentEpoch !== epoch) {
        await chrome.storage.session.remove("homepageSession");
        return { ok: false, status: cancellation };
      }
      actions = safePages(result.pages);
      capability = result.capability;
      lastRevision = result.revision;
      return { ok: true, status: "Device paired. Effect is off." };
    } catch {
      return { ok: false, status: currentEpoch !== epoch ? cancellation : "Pairing unavailable" };
    } finally {
      pairing = false;
      if (pending === controller) pending = null;
    }
  }
  if (message.command !== "launch" || !Object.hasOwn(actions, message.action)) {
    return { ok: false, status: "Action refused" };
  }
  if (pending) return { ok: false, status: "An action is already pending" };
  const currentEpoch = epoch;
  const started = performance.now();
  const controller = new AbortController();
  pending = controller;
  let selected = false;
  let status = "Effect off. Opened the test page.";
  try {
    if (enabled && capability) {
      status = "Policy unavailable. Opened the test page.";
      const requestId = crypto.randomUUID().replaceAll("-", "");
      try {
        const policy = await boundedJSON("/homepage/policy", {
          origin: actions[message.action].origin, request_id: requestId
        }, capability, controller);
        if (validPolicy(policy, requestId, started, actions[message.action].origin)) {
          lastRevision = policy.revision;
          await chrome.storage.session.set({ homepageSession: {
            capability, revision: lastRevision, pages: Object.values(actions)
          } });
          const random = crypto.getRandomValues(new Uint32Array(1))[0] / 4294967296;
          selected = policy.enabled && random * 100 < policy.chance;
          status = selected ? "Selected the exact-origin home page." :
            "No bounce. Opened the test page.";
          if (performance.now() - started >= policy.lease_ms) {
            selected = false;
            status = "Policy expired. Opened the test page.";
          }
        }
      } catch (error) {
        if (error instanceof SessionRejected) {
          enabled = false;
          capability = null;
          lastRevision = 0;
          await chrome.storage.session.remove("homepageSession");
          status = "Pairing lost. Effect off. Opened the test page.";
        }
        /* An unavailable policy leaves ordinary test navigation usable. */
      }
    }
    if (epoch !== currentEpoch) return { ok: false, status: cancellation };
    await navigate(actions[message.action], selected ? "/" : actions[message.action].path, currentEpoch);
    return { ok: true, status };
  } finally { if (pending === controller) pending = null; }
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("index.html")) {
    reply({ ok: false, status: "Sender refused" });
    return false;
  }
  handle(message).then(reply, () => reply({ ok: false, status: "Test action failed" }));
  return true;
});
