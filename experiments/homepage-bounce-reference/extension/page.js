"use strict";
const status = document.getElementById("status");
async function send(message) {
  try {
    const result = await chrome.runtime.sendMessage(message);
    status.textContent = result.status || (result.enabled ? "Enabled" : "Off by default.");
  } catch { status.textContent = "Reference worker unavailable. Effect off."; }
}
document.getElementById("pair").addEventListener("click", () => {
  const input = document.getElementById("pair-code");
  const code = input.value;
  input.value = "";
  send({ command: "pair", code });
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
