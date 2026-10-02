// Pi Wi-Fi restarts. The Wi-Fi goes down for a few seconds and takes this browser with it,
// so the page waits for the Pi to answer again and then reads how the change went.
// This file is sent before anyone has signed in. Keep the product name out of every text here.
const WIFI_RESTART_NOTICE = "The Pi's Wi-Fi will restart now. This Mac will lose its connection for up to a minute and should rejoin on its own. This page waits for the Pi and then shows how the change went.";
const RECONNECT_STEPS_MS = [1000, 2000, 3000];
const RECONNECT_LIMIT_MS = 90000;
const WIFI_RUN_LIMIT_MS = 240000;
const LOST_CONNECTION_TEXT = "Lost the connection to the Pi. If its Wi-Fi is restarting, this Mac rejoins on its own.";
const WIFI_DISMISSED_KEY = "wifi-change-dismissed";
let reconnectGrace = 0;
let reconnectHappened = false;

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

function reconnectDelay(attempt) {
  return RECONNECT_STEPS_MS[Math.min(attempt, RECONNECT_STEPS_MS.length - 1)];
}

// Ask again after 1 s, 2 s, 3 s, then every 3 s until the Pi answers or the limit passes.
async function waitForPi({probe, sleep, now, limitMs = RECONNECT_LIMIT_MS, onWaiting}) {
  const started = now();
  let attempt = 0;
  while (now() - started < limitMs) {
    await sleep(reconnectDelay(attempt));
    attempt += 1;
    try {
      await probe();
      return true;
    } catch (error) {
      if (onWaiting) onWaiting(attempt);
    }
  }
  return false;
}

// Read the stored outcome of one Wi-Fi change until it is final, across a dropped connection.
// Returns {operation}, {operation: null, expired: true} or {gaveUp: true}.
async function followWifiChange(identifier, {fetchOperation, sleep, now, onRunning, onReconnecting,
                                              onGivenUp, limitMs = RECONNECT_LIMIT_MS,
                                              runLimitMs = WIFI_RUN_LIMIT_MS}) {
  const started = now();
  let lostSince = null;
  let attempt = 0;
  for (;;) {
    try {
      const data = await fetchOperation(identifier);
      lostSince = null;
      attempt = 0;
      const operation = data && data.operation;
      if (!operation) return {operation: null, expired: true};
      if (operation.state !== "running") return {operation};
      if (now() - started >= runLimitMs) {
        if (onGivenUp) onGivenUp();
        return {gaveUp: true};
      }
      if (onRunning) onRunning(operation);
      await sleep(1000);
    } catch (error) {
      if (error && error.status === 410) return {operation: null, expired: true};
      // A device check that lags behind the new connection is waited out, not treated as a refusal.
      if (!isConnectionLost(error) && !(error && error.transient)) throw error;
      if (lostSince === null) {
        lostSince = now();
        if (onReconnecting) onReconnecting();
      }
      if (now() - lostSince >= limitMs) {
        if (onGivenUp) onGivenUp();
        return {gaveUp: true};
      }
      await sleep(reconnectDelay(attempt));
      attempt += 1;
    }
  }
}

// What to tell the person about the network to join. The password is never repeated here.
function rejoinLines(operation) {
  if (!operation || operation.state !== "succeeded" || !operation.ssid) return [];
  const lines = ["Join the Wi-Fi network “" + operation.ssid + "” on this Mac."];
  if (operation.kind === "router-copy") lines.push("Use the router\u2019s Wi-Fi password.");
  else if (operation.password_changed) lines.push("Use the new password you set. It is not shown here.");
  else lines.push("The password did not change.");
  return lines;
}

function wifiSleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function probePi() {
  return fetch("/api/health", {cache: "no-store"}).then((response) => {
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
    banner.setAttribute("role", "status");
    banner.setAttribute("aria-live", "polite");
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
}

function showReconnectingBanner() {
  reconnectHappened = true;
  showWifiBanner("working", "Reconnecting to the Pi…", [
    "The Pi’s Wi-Fi is restarting. This Mac should rejoin on its own, usually within a minute.",
    "This page keeps trying and shows the result when the Pi answers. If the Mac does not rejoin, choose the Pi’s test Wi-Fi network in the Wi-Fi menu.",
  ]);
}

function showWifiResult(operation) {
  const succeeded = operation.state === "succeeded";
  showWifiBanner(succeeded ? "success" : "error",
    succeeded ? "Wi-Fi change finished" : "Wi-Fi change did not finish",
    [operation.message || ""].concat(rejoinLines(operation)).filter(Boolean),
    [{label: "Dismiss", run: () => { rememberDismissedWifiChange(operation.id); hideWifiBanner(); }}]);
}

function wifiPollDependencies(retry) {
  return {
    fetchOperation: (identifier) =>
      request("/api/wifi-operation?id=" + encodeURIComponent(identifier), "GET"),
    sleep: wifiSleep,
    now: () => Date.now(),
    onRunning: () => showWifiBanner("working", "Restarting the Pi’s Wi-Fi", [
      "This Mac may disconnect for up to a minute and should rejoin on its own.",
    ]),
    onReconnecting: showReconnectingBanner,
    onGivenUp: () => showWifiBanner("warning", "The Pi has not answered yet", [
      "Check that this Mac is on the Pi’s test Wi-Fi network, then check again. Nothing was lost: your sign-in is kept in this browser.",
    ], [{label: "Check again", run: retry}]),
  };
}

// Follow one change to its end and show the result. onFinished gets the final operation.
async function followStoredWifiChange(identifier, onFinished) {
  const outcome = await withReconnectGrace(() => followWifiChange(
    identifier, wifiPollDependencies(() => followStoredWifiChange(identifier, onFinished))));
  if (outcome.gaveUp) return outcome;
  if (outcome.operation) {
    showWifiResult(outcome.operation);
  } else {
    showWifiBanner("warning", "The result is no longer remembered", [
      "Check the Pi Wi-Fi settings before making another change.",
    ], [{label: "Dismiss", run: hideWifiBanner}]);
  }
  if (onFinished) onFinished(outcome.operation);
  return outcome;
}

// Send a change that restarts the Pi's Wi-Fi and follow it. The Pi answers before it restarts.
async function runWifiChange(path, body, onFinished) {
  const sentAt = Date.now() / 1000;
  showWifiBanner("working", "Sending the Wi-Fi change", [
    "The Pi’s Wi-Fi restarts a moment after it confirms. This Mac should rejoin on its own.",
  ]);
  let identifier;
  try {
    identifier = (await request(path, "POST", body)).operation_id;
  } catch (error) {
    hideWifiBanner();
    if (!isConnectionLost(error)) throw error;
    // The answer was lost. Find out whether the Pi took the change before saying anything.
    showReconnectingBanner();
    const back = await withReconnectGrace(() => waitForPi({probe: probePi, sleep: wifiSleep,
                                                          now: () => Date.now()}));
    const last = back ? await request("/api/wifi-operation", "GET") : null;
    const operation = last && last.operation;
    if (!operation || operation.started_at < sentAt - 5) {
      hideWifiBanner();
      throw new Error(back
        ? "The Pi did not confirm the change. Check the Pi Wi-Fi settings before trying again."
        : "The Pi has not answered yet. Check that this Mac is on the Pi’s test Wi-Fi network.");
    }
    identifier = operation.id;
  }
  return followStoredWifiChange(identifier, onFinished);
}

// Run one request of a longer job (a router change) and wait out a restart in between.
async function requestAcrossWifiRestart(send) {
  for (;;) {
    try {
      const response = await send();
      if (reconnectHappened) hideWifiBanner();
      return response;
    } catch (error) {
      if (!isConnectionLost(error)) throw error;
      showReconnectingBanner();
      const back = await withReconnectGrace(() => waitForPi({probe: probePi, sleep: wifiSleep,
                                                            now: () => Date.now()}));
      if (!back) {
        hideWifiBanner();
        throw new Error("The Pi has not answered yet. Check that this Mac is on the Pi’s test Wi-Fi network, then check the router state before another change.");
      }
    }
  }
}

// After a reload or a new connection, show a recent change nobody has dismissed yet.
async function resumeWifiChangeNotice() {
  try {
    const last = (await request("/api/wifi-operation", "GET")).operation;
    if (!last || last.id === dismissedWifiChange()) return;
    if (last.state === "running") {
      followStoredWifiChange(last.id).catch(() => {});
    } else if (Date.now() / 1000 - last.finished_at < 600) {
      showWifiResult(last);
    }
  } catch (error) {
    // Nothing to resume, or the Pi cannot be reached right now.
  }
}

// A token check that met a dropped connection tries again once the Pi answers. The token stays.
function recheckTokenWhenPiAnswers() {
  waitForPi({probe: probePi, sleep: wifiSleep, now: () => Date.now()}).then((back) => {
    if (back) checkAdminToken();
  });
}
