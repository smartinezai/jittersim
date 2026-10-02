"use strict";
importScripts("runtime-config.js");

const origin = new URL(REFERENCE_CONFIG.origin);
if (origin.protocol !== "http:" || origin.hostname !== "127.0.0.1" ||
    !origin.port || origin.pathname !== "/" || origin.search || origin.hash ||
    origin.username || origin.password) throw new Error("Invalid reference origin");
const base = origin.origin;
const actions = Object.freeze({ article: "/pages/article", notes: "/pages/notes" });
let enabled = false;
let capability = null;
let lastRevision = 0;
let epoch = 0;
let pending = null;
let testTab = null;
const ready = chrome.storage.session.get(["referenceSession", "referenceTab"]).then(
  ({ referenceSession, referenceTab }) => {
  if (referenceSession && /^[a-f0-9]{64}$/.test(referenceSession.capability || "")) {
    capability = referenceSession.capability;
    lastRevision = referenceSession.revision;
  }
  if (Number.isSafeInteger(referenceTab) && referenceTab >= 0) testTab = referenceTab;
});

async function boundedJSON(path, body, credential, controller) {
  const timer = setTimeout(() => controller.abort(), 750);
  try {
    const response = await fetch(base + path, {
      method: "POST", redirect: "error", cache: "no-store", credentials: "omit",
      headers: { "Content-Type": "application/json", ...(credential ? {
        "Authorization": "Reference " + credential
      } : {}) },
      body: JSON.stringify(body), signal: controller.signal
    });
    if (!response.ok || response.url !== base + path ||
        !/^application\/json(?:;|$)/i.test(response.headers.get("Content-Type") || "")) {
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

function validPolicy(policy, requestId, started) {
  return policy && typeof policy === "object" && !Array.isArray(policy) &&
    Object.keys(policy).sort().join(",") ===
      "chance,enabled,kind,lease_ms,origin,reference_only,request_id,revision,simulated_identity" &&
    policy.kind === "homepage-reference" && policy.reference_only === true &&
    policy.origin === base && policy.request_id === requestId &&
    Number.isInteger(policy.chance) && policy.chance >= 0 && policy.chance <= 100 &&
    typeof policy.enabled === "boolean" && Number.isSafeInteger(policy.revision) &&
    policy.revision >= lastRevision && Number.isInteger(policy.lease_ms) &&
    policy.lease_ms > 0 && policy.lease_ms <= 1000 &&
    performance.now() - started < policy.lease_ms &&
    ["verified", "protected", "unknown"].includes(policy.simulated_identity) &&
    (!policy.enabled || policy.simulated_identity === "verified");
}

async function navigate(path) {
  const url = new URL(path, base);
  if (url.origin !== base || !["/", ...Object.values(actions)].includes(url.pathname) ||
      url.search || url.hash || url.username || url.password) {
    throw new Error("Navigation refused");
  }
  if (testTab !== null) {
    try { await chrome.tabs.update(testTab, { url: url.href, active: false }); return; }
    catch { testTab = null; }
  }
  const tab = await chrome.tabs.create({ url: url.href, active: false });
  testTab = tab.id;
  await chrome.storage.session.set({ referenceTab: testTab });
}

chrome.tabs.onRemoved.addListener(id => { if (id === testTab) testTab = null; });

async function handle(message) {
  await ready;
  if (!message || typeof message !== "object") return { ok: false, status: "Action refused" };
  if (message.command === "status") return { ok: true, enabled, paired: !!capability };
  if (message.command === "off") {
    enabled = false;
    epoch += 1;
    pending?.abort();
    return { ok: true, status: "Off. Pending action cancelled." };
  }
  if (message.command === "enable") {
    enabled = !!capability;
    return { ok: enabled, status: enabled ? "Enabled for reference actions" : "Pair first" };
  }
  if (message.command === "pair") {
    if (pending || typeof message.code !== "string" || !/^[a-f0-9]{32}$/.test(message.code)) {
      return { ok: false, status: "Pairing refused" };
    }
    const controller = new AbortController();
    pending = controller;
    const currentEpoch = ++epoch;
    enabled = false;
    try {
      const result = await boundedJSON("/reference/pair", { code: message.code }, null, controller);
      if (currentEpoch !== epoch || result.reference_only !== true ||
          result.kind !== "reference-pair" || !/^[a-f0-9]{64}$/.test(result.capability || "") ||
          !Number.isSafeInteger(result.revision) || result.revision < 1) {
        throw new Error("Invalid pairing response");
      }
      capability = result.capability;
      lastRevision = result.revision;
      await chrome.storage.session.set({ referenceSession: {
        capability, revision: lastRevision
      } });
      return { ok: true, status: "Simulated device paired. Effect is off." };
    } catch { return { ok: false, status: "Pairing unavailable" }; }
    finally { if (pending === controller) pending = null; }
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
      const requestId = crypto.randomUUID();
      try {
        const policy = await boundedJSON("/reference/policy", {
          origin: base, request_id: requestId
        }, capability, controller);
        if (validPolicy(policy, requestId, started)) {
          lastRevision = policy.revision;
          await chrome.storage.session.set({ referenceSession: {
            capability, revision: lastRevision
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
      } catch { /* An unavailable policy leaves ordinary test navigation usable. */ }
    }
    if (epoch !== currentEpoch || controller.signal.aborted && !enabled) {
      return { ok: false, status: "Off. Pending action cancelled." };
    }
    await navigate(selected ? "/" : actions[message.action]);
    return { ok: true, status };
  } finally { if (pending === controller) pending = null; }
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("index.html")) {
    reply({ ok: false, status: "Sender refused" });
    return false;
  }
  handle(message).then(reply, () => reply({ ok: false, status: "Reference action failed" }));
  return true;
});
