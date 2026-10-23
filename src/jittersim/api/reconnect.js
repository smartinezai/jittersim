// Pi Wi-Fi restarts. The Wi-Fi goes down for a few seconds and takes this browser with it,
// so the page waits for the Pi to answer again and then reads how the change went.
// This file is sent before anyone has signed in. Keep the product name out of every text here.
const WIFI_RESTART_NOTICE = "The Pi's Wi-Fi will restart now. Every device on it, including this one, will lose its connection for up to a minute. If the Wi-Fi name or password changes, this device cannot rejoin by itself: choose the network again in the Wi-Fi menu. This page waits for the Pi and then shows how the change went.";
const RECONNECT_STEPS_MS = [1000, 2000, 3000];
const RECONNECT_LIMIT_MS = 90000;
const WIFI_RUN_LIMIT_MS = 240000;
const LOST_CONNECTION_TEXT = "Lost the connection to the Pi. If its Wi-Fi is restarting, this device rejoins on its own.";
const WIFI_DISMISSED_KEY = "wifi-change-dismissed";
let reconnectGrace = 0;
let reconnectHappened = false;
// The network a person has to join again after a change. Only the name, never a password.
let wifiJoinHint = null;
let lastWifiBannerAnnouncement = "";

// All automatic reads share one elapsed deadline and one credential generation.
const wifiReaders = new Set();
const wifiFollowers = new Map();
let wifiRecoveryGeneration = 0;
const recoveryNow = () => performance.now();

function cancelWifiRecovery() {
  wifiRecoveryGeneration += 1;
  wifiReaders.forEach(controller => controller.abort());
  hideWifiBanner();
}

function wifiRecoveryContext(limitMs = RECONNECT_LIMIT_MS) {
  const generation = wifiRecoveryGeneration;
  const revision = typeof adminTokenRevision === "undefined" ? null : adminTokenRevision;
  return {now: recoveryNow, deadline: recoveryNow() + limitMs,
    valid: () => generation === wifiRecoveryGeneration &&
      (revision === null || revision === adminTokenRevision)};
}

function recoveryStopped() {
  const error = new Error("Recovery stopped. Check current Wi-Fi settings before another change.");
  error.recoveryStopped = true;
  return error;
}

async function boundedRecoveryRead(send, context) {
  const now = context.now || recoveryNow;
  if ((context.valid && !context.valid()) || now() >= context.deadline) throw recoveryStopped();
  const controller = new AbortController();
  wifiReaders.add(controller);
  let timer;
  try {
    const result = await Promise.race([
      send({signal: controller.signal, timeoutMs: Math.min(10000, context.deadline - now())}),
      new Promise((resolve, reject) => {
        timer = setTimeout(() => {
          reject((!context.valid || context.valid()) && now() < context.deadline
            ? lostConnectionError(new Error("Request timed out")) : recoveryStopped());
          controller.abort();
        },
          Math.max(1, Math.min(10000, context.deadline - now())));
        controller.signal.addEventListener("abort", () => reject(recoveryStopped()), {once: true});
      })
    ]);
    if ((context.valid && !context.valid()) || now() >= context.deadline) throw recoveryStopped();
    return result;
  } finally {
    clearTimeout(timer);
    wifiReaders.delete(controller);
  }
}

async function recoveryPause(ms, context, sleep = wifiSleep) {
  const now = context.now || recoveryNow;
  if ((context.valid && !context.valid()) || now() >= context.deadline) throw recoveryStopped();
  await sleep(Math.min(ms, context.deadline - now()));
  if (context.valid && !context.valid()) throw recoveryStopped();
}

async function newWifiRequestId(options) {
  // The Pi supplies its own time so a phone clock cannot expire a new request.
  const result = await request("/api/wifi-operation/request-id", "GET", undefined, options);
  return result.request_id;
}

// A browser reports a dropped connection as "Load failed" or "Failed to fetch". Those words
// tell nobody what to do, so they are replaced and the error is marked.
function lostConnectionError(error) {
  if (!error || error.status) return error;
  const lost = new Error(LOST_CONNECTION_TEXT);
  lost.connectionLost = true;
  return lost;
}

function isConnectionLost(error) {
  return Boolean(error && error.connectionLost === true);
}

function clearReconnectFlag() {
  reconnectHappened = false;
}

function wifiReconnectActive() {
  return reconnectGrace > 0;
}

async function withReconnectGrace(work) {
  reconnectGrace += 1;
  try { return await work(); } finally { reconnectGrace -= 1; }
}

// Run one request after the Pi is back. The Pi may not have matched this device to its connection
// yet and a dropped connection can follow, so both are waited out for a while.
async function retryAfterRejoin(send, context = wifiRecoveryContext()) {
  return withReconnectGrace(async () => {
    for (let attempt = 0;; attempt += 1) {
      try { return await boundedRecoveryRead(send, context); }
      catch (error) {
        if (!isConnectionLost(error) && !error.transient) throw error;
        await recoveryPause(reconnectDelay(attempt), context);
      }
    }
  });
}

function reconnectDelay(attempt) {
  return RECONNECT_STEPS_MS[Math.min(attempt, RECONNECT_STEPS_MS.length - 1)];
}

// Ask again after 1 s, 2 s, 3 s, then every 3 s until the Pi answers or the limit passes.
async function waitForPi({probe, sleep, now, limitMs = RECONNECT_LIMIT_MS, onWaiting, valid}) {
  const context = {now, valid, deadline: now() + limitMs};
  for (let attempt = 0; now() < context.deadline; attempt += 1) {
    try {
      await recoveryPause(reconnectDelay(attempt), context, sleep);
      await boundedRecoveryRead(probe, context);
      return true;
    } catch (error) {
      if (error.recoveryStopped) return false;
      if (onWaiting) onWaiting(attempt + 1);
    }
  }
  return false;
}

// Read the stored outcome of one Wi-Fi change until it is final, across a dropped connection.
// Returns {operation}, {operation: null, expired: true} or {gaveUp: true}.
async function followWifiChange(identifier, {fetchOperation, sleep, now, onRunning, onReconnecting,
                                              onGivenUp, valid, deadline,
                                              limitMs = RECONNECT_LIMIT_MS, runLimitMs = WIFI_RUN_LIMIT_MS}) {
  const context = {now, valid, deadline: deadline === undefined ? now() + runLimitMs : deadline};
  let lostSince = null;
  let attempt = 0;
  try {
    for (;;) {
      try {
        const data = await boundedRecoveryRead(options => fetchOperation(identifier, options), context);
        lostSince = null;
        attempt = 0;
        const operation = data && data.operation;
        if (!operation) return {operation: null, expired: true};
        if (operation.id && operation.id !== identifier) throw recoveryStopped();
        if (operation.state !== "running") return {operation};
        if (onRunning) onRunning(operation);
        await recoveryPause(1000, context, sleep);
      } catch (error) {
        if (error && error.status === 410) return {operation: null, expired: true};
        if (!isConnectionLost(error) && !error.transient) throw error;
        if (lostSince === null) {
          lostSince = now();
          if (onReconnecting) onReconnecting();
        }
        if (now() - lostSince >= limitMs) throw recoveryStopped();
        await recoveryPause(reconnectDelay(attempt++), context, sleep);
      }
    }
  } catch (error) {
    if (!error.recoveryStopped) throw error;
    if ((!valid || valid()) && onGivenUp) onGivenUp();
    return {gaveUp: true};
  }
}

// What to tell the person about the network to join. The password is never repeated here.
function rejoinLines(operation) {
  if (!operation || operation.state !== "succeeded" || !operation.ssid) return [];
  const lines = ["Join the Wi-Fi network “" + operation.ssid + "” on this device."];
  if (operation.kind === "router-copy") lines.push("Use the router\u2019s Wi-Fi password.");
  else if (operation.password_changed) lines.push("Use the new password you set. It is not shown here.");
  else lines.push("The password did not change.");
  return lines;
}

function wifiSleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function probePi(options = {}) {
  return fetch("/api/health", {cache: "no-store", signal: options.signal}).then((response) => {
    if (!response.ok) throw new Error("not ready");
  }).catch((error) => { throw lostConnectionError(error); });
}

function dismissedWifiChange() {
  try { return window.localStorage.getItem(WIFI_DISMISSED_KEY); } catch (error) { return null; }
}

function rememberDismissedWifiChange(identifier) {
  try { window.localStorage.setItem(WIFI_DISMISSED_KEY, identifier); } catch (error) { /* shown again next time */ }
}

function hideWifiBanner() {
  const banner = document.getElementById("wifi-change-banner");
  if (banner) banner.hidden = true;
}

// One panel for the whole change. It stays until the person dismisses a finished result.
function showWifiBanner(kind, title, lines, buttons) {
  let banner = document.getElementById("wifi-change-banner");
  if (!banner) {
    banner = document.createElement("aside");
    banner.id = "wifi-change-banner";
    // At the top of the page content, above the controls, so it never covers them.
    const content = document.getElementById("content") || document.body;
    content.prepend(banner);
  }
  banner.hidden = false;
  banner.className = "wifi-change-banner " + kind;
  const heading = document.createElement("strong");
  heading.textContent = title;
  const children = [heading];
  (lines || []).forEach((line) => {
    const paragraph = document.createElement("p");
    paragraph.textContent = line;
    children.push(paragraph);
  });
  const actions = document.createElement("div");
  actions.className = "wifi-change-actions";
  (buttons || []).forEach((entry) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = entry.label;
    button.addEventListener("click", entry.run);
    actions.append(button);
  });
  if (buttons && buttons.length) children.push(actions);
  banner.replaceChildren(...children);
  // The banner is not a live region. The page's one announcer says it once per change.
  const said = [title].concat(lines || []).join(" ");
  if (said !== lastWifiBannerAnnouncement) {
    lastWifiBannerAnnouncement = said;
    if (typeof announce === "function") announce(said, kind === "error");
  }
}

function showReconnectingBanner() {
  reconnectHappened = true;
  showWifiBanner("working", "Reconnecting to the Pi…", [
    "The Pi’s Wi-Fi is restarting. If its name or password changed, this device does not rejoin by itself: choose the Pi’s test Wi-Fi network in the Wi-Fi menu.",
  ].concat(wifiJoinHint ? [wifiJoinHint] : [], [
    "This page keeps trying and shows the result as soon as the Pi answers.",
  ]));
}

function showWifiResult(operation) {
  const succeeded = operation.state === "succeeded";
  showWifiBanner(succeeded ? "success" : "error",
    succeeded ? "Wi-Fi change finished" : "Wi-Fi change did not finish",
    [operation.message || ""].concat(rejoinLines(operation)).filter(Boolean),
    [{label: "Dismiss", run: () => { rememberDismissedWifiChange(operation.id); hideWifiBanner(); }}]);
}

function wifiPollDependencies(retry, context = wifiRecoveryContext(WIFI_RUN_LIMIT_MS)) {
  return {
    fetchOperation: (identifier, options) =>
      request("/api/wifi-operation?id=" + encodeURIComponent(identifier), "GET", undefined, options),
    sleep: wifiSleep,
    ...context,
    onRunning: () => showWifiBanner("working", "Restarting the Pi’s Wi-Fi", [
      "This device may disconnect for up to a minute. If the name or password changed, choose the network again in the Wi-Fi menu.",
    ].concat(wifiJoinHint ? [wifiJoinHint] : [])),
    onReconnecting: showReconnectingBanner,
    onGivenUp: () => showWifiBanner("warning", "The Pi has not answered yet", [
      "Check that this device is on the Pi’s test Wi-Fi network, then check again. If the name or password changed, join it again in the Wi-Fi menu. If the Pi answers but says this device is not allowed, press Allow this device again: a new Wi-Fi name gives this device a new private address. Nothing was lost: your sign-in is kept in this browser.",
    ].concat(wifiJoinHint ? [wifiJoinHint] : []), [{label: "Check again", run: retry}]),
  };
}

// Follow one change to its end and show the result. onFinished gets the final operation.
async function followStoredWifiChange(identifier, onFinished, context = wifiRecoveryContext(WIFI_RUN_LIMIT_MS)) {
  const key = wifiRecoveryGeneration + ":" + identifier;
  if (!wifiFollowers.has(key)) {
    const work = withReconnectGrace(() => followWifiChange(identifier,
      wifiPollDependencies(() => {
        if (context.valid()) followStoredWifiChange(identifier, onFinished).catch(() => {});
      }, context))).then(outcome => {
        if (!context.valid() || outcome.gaveUp) return outcome;
        if (outcome.operation) showWifiResult(outcome.operation);
        else showWifiBanner("warning", "The exact result is no longer remembered", [
          "Check the Pi Wi-Fi settings before making another change."
        ], [{label: "Dismiss", run: hideWifiBanner}]);
        return outcome;
      }).finally(() => wifiFollowers.delete(key));
    wifiFollowers.set(key, work);
  }
  const outcome = await wifiFollowers.get(key);
  if (context.valid() && !outcome.gaveUp && onFinished) onFinished(outcome.operation);
  return outcome;
}

// Send a change that restarts the Pi's Wi-Fi and follow it. The Pi answers before it restarts.
// restoredName is the name a reset brings back. It keeps the password.
async function runWifiChange(path, body, onFinished, restoredName) {
  const submitButton = typeof pendingFormSubmitButton === "undefined" ? null : pendingFormSubmitButton;
  wifiJoinHint = restoredName ? "Join “" + restoredName + "” in the Wi-Fi menu. The password did not change."
    : body && typeof body.ssid === "string" && body.ssid
    ? "Join “" + body.ssid + "” with the new password you set."
    : null;
  try {
    return await sendAndFollowWifiChange(path, body, onFinished);
  } finally {
    wifiJoinHint = null;
    if (submitButton && pendingFormSubmitButton === submitButton) {
      pendingFormSubmitButton = null;
      setButtonBusy(submitButton, false);
    }
  }
}

async function sendAndFollowWifiChange(path, body, onFinished) {
  const context = wifiRecoveryContext(WIFI_RUN_LIMIT_MS);
  const identifier = await boundedRecoveryRead(options => newWifiRequestId(options), context);
  showWifiBanner("working", "Sending the Wi-Fi change", [WIFI_RESTART_NOTICE]);
  try {
    const accepted = await boundedRecoveryRead(options =>
      request(path, "POST", {...body, request_id: identifier}, options), context);
    if (accepted.operation_id !== identifier) throw recoveryStopped();
  } catch (error) {
    if (!context.valid()) throw recoveryStopped();
    if (!isConnectionLost(error) && !error.recoveryStopped) throw error;
    // An uncertain write is never replayed. Only its exact outcome is read.
    showReconnectingBanner();
  }
  return followStoredWifiChange(identifier, onFinished, context);
}

// Run one request of a longer job (a router change) and wait out a restart in between.
async function requestAcrossWifiRestart(send, context = wifiRecoveryContext()) {
  return withReconnectGrace(async () => {
    const response = await retryAfterRejoin(options => send(options).catch(error => {
      if (isConnectionLost(error)) showReconnectingBanner();
      throw error;
    }), context);
    if (context.valid && !context.valid()) throw recoveryStopped();
    if (reconnectHappened) hideWifiBanner();
    return response;
  });
}

// After a reload or a new connection, show a recent change nobody has dismissed yet.
async function resumeWifiChangeNotice() {
  const context = wifiRecoveryContext(WIFI_RUN_LIMIT_MS);
  try {
    const last = (await boundedRecoveryRead(options =>
      request("/api/wifi-operation", "GET", undefined, options), context)).operation;
    if (!context.valid() || !last || last.id === dismissedWifiChange()) return;
    if (last.state === "running") followStoredWifiChange(last.id, null, context).catch(() => {});
    else if (Date.now() / 1000 - last.finished_at < 600) showWifiResult(last);
  } catch (error) { /* No confirmed result to resume. */ }
}

// A token check that met a dropped connection tries again once the Pi answers. The token stays.
let tokenRecovery = null;
function recheckTokenWhenPiAnswers() {
  const context = wifiRecoveryContext();
  if (tokenRecovery && tokenRecovery.valid()) return;
  tokenRecovery = context;
  waitForPi({probe: probePi, sleep: wifiSleep, now: context.now, valid: context.valid}).then(async back => {
    if (back && context.valid()) await checkAdminToken();
  }).catch(() => {}).finally(() => {
    if (tokenRecovery === context) tokenRecovery = null;
  });
}
