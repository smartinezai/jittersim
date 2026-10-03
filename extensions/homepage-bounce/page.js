"use strict";
const status = document.getElementById("status");
let latestRequest = 0;
async function send(message) {
  const request = ++latestRequest;
  try {
    const result = await chrome.runtime.sendMessage(message);
    if (request !== latestRequest) return;
    if (result.extensionId) document.getElementById("extension-id").textContent = result.extensionId;
    if (result.pages) {
      const pages = document.getElementById("test-pages");
      pages.replaceChildren();
      for (const [index, page] of result.pages.entries()) {
        const button = document.createElement("button");
        button.textContent = "Open " + page.origin + page.path;
        button.addEventListener("click", () => send({ command: "launch", action: String(index) }));
        pages.append(button);
      }
    }
    status.textContent = result.status || (result.enabled ? "Enabled" : "Off by default.");
  } catch {
    if (request === latestRequest) status.textContent = "Test worker unavailable. Effect off.";
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
  try { await send({ command: "pair", code }); await send({ command: "status" }); }
  finally { pair.disabled = false; enable.disabled = false; }
});
document.getElementById("enable").addEventListener("click", () => send({ command: "enable" }));
document.getElementById("off").addEventListener("click", () => send({ command: "off" }));
send({ command: "status" });
