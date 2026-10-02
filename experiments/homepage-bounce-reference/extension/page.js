"use strict";
const status = document.getElementById("status");
let latestRequest = 0;
async function send(message) {
  const request = ++latestRequest;
  try {
    const result = await chrome.runtime.sendMessage(message);
    if (request !== latestRequest) return;
    status.textContent = result.status || (result.enabled ? "Enabled" : "Off by default.");
  } catch {
    if (request === latestRequest) status.textContent = "Reference worker unavailable. Effect off.";
  }
}
document.getElementById("pair").addEventListener("click", async () => {
  const input = document.getElementById("pair-code");
  const code = input.value;
  input.value = "";
  const pair = document.getElementById("pair");
  const enable = document.getElementById("enable");
  pair.disabled = true;
  enable.disabled = true;
  try { await send({ command: "pair", code }); }
  finally { pair.disabled = false; enable.disabled = false; }
});
document.getElementById("enable").addEventListener("click", () => send({ command: "enable" }));
document.getElementById("off").addEventListener("click", () => send({ command: "off" }));
for (const button of document.querySelectorAll("[data-action]")) {
  button.addEventListener("click", () => {
    status.textContent = "Checking fresh reference policy…";
    send({ command: "launch", action: button.dataset.action });
  });
}
send({ command: "status" });
