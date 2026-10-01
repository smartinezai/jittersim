"""Copy the router Wi-Fi name and password to the clipboard through the served dashboard.

The fixture's fake router accepts any router password and answers with the Wi-Fi name
"Browser home" and the Wi-Fi password "browser-wifi-password". Nothing here is a real credential.
"""

import json
import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ADMIN_TOKEN = "browser-test"
ROUTER_PASSWORD = "browser-router-password"
WIFI_NAME = "Browser home"
WIFI_PASSWORD = "browser-wifi-password"
CREDENTIALS_PATH = "/api/router/wifi-credentials"


def sign_in(page):
    expect(page.locator("#private-dashboard")).to_be_hidden()
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill(ADMIN_TOKEN)
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    page.locator("#close-credentials").click()


def enter_router_password(page):
    page.locator("#open-router-credentials").click()
    page.locator("#router-admin-password").fill(ROUTER_PASSWORD)
    page.locator("#check-router-password").click()
    expect(page.locator("#credential-router-status")).to_contain_text("Router password accepted")
    page.locator("#close-credentials").click()


SCAN_JS = """
(() => {
  const secret = __SECRET__;
  const hits = [];
  const seen = new Set();
  let budget = 400000;
  let scanned = 0;
  const scan = (value, path, depth) => {
    if (budget-- <= 0 || value === null || value === undefined) return;
    if (typeof value === "string") {
      scanned += 1;
      if (value.includes(secret)) hits.push(path);
      return;
    }
    if (typeof value === "function") {
      try { if (Function.prototype.toString.call(value).includes(secret)) hits.push(path + "()"); }
      catch (error) { /* a native function has no source to read */ }
      return;
    }
    if (typeof value !== "object" || depth > 5 || seen.has(value)) return;
    seen.add(value);
    if (value instanceof Node || ArrayBuffer.isView(value) || value instanceof ArrayBuffer) return;
    if (value instanceof Map || value instanceof Set) {
      value.forEach((entry, key) => { scan(key, path + ".key", depth + 1); scan(entry, path + ".value", depth + 1); });
      return;
    }
    let keys;
    try { keys = Reflect.ownKeys(value); } catch (error) { return; }
    for (const key of keys) {
      let entry;
      try { entry = value[key]; } catch (error) { continue; }
      scan(entry, path + "." + String(key), depth + 1);
    }
  };
  for (const name of Object.getOwnPropertyNames(window)) {
    let entry;
    try { entry = window[name]; } catch (error) { continue; }
    scan(entry, "window." + name, 0);
  }
  const lexical = __LEXICAL__;
  for (const [name, entry] of lexical) scan(entry, "let " + name, 0);
  // Every text node and attribute and field value in the page, hidden or not.
  if (document.documentElement.textContent.includes(secret)) hits.push("document text");
  for (const node of document.querySelectorAll("*")) {
    for (const attribute of node.attributes) {
      if (attribute.value.includes(secret)) hits.push("attribute " + attribute.name + " of " + node.tagName);
    }
    if (typeof node.value === "string" && node.value.includes(secret)) hits.push("value of " + node.tagName);
  }
  for (const store of [localStorage, sessionStorage]) {
    for (let index = 0; index < store.length; index += 1) {
      const key = store.key(index);
      if (key.includes(secret) || store.getItem(key).includes(secret)) hits.push("storage " + key);
    }
  }
  if (document.cookie.includes(secret)) hits.push("cookie");
  return {hits: hits, scanned: scanned, lexical: lexical.length};
})()
"""


def scan_for_secret(page, secret):
    """Look for a value in window properties and top-level let bindings and the whole DOM."""

    session = page.context.new_cdp_session(page)
    try:
        names = session.send("Runtime.globalLexicalScopeNames")["names"]
        reads = ",".join(
            "(() => { try { return [%s, %s]; } catch (error) { return [%s, undefined]; } })()"
            % (json.dumps(name), name, json.dumps(name))
            for name in names
        )
        expression = SCAN_JS.replace("__SECRET__", json.dumps(secret)).replace(
            "__LEXICAL__", "[" + reads + "]"
        )
        outcome = session.send(
            "Runtime.evaluate", {"expression": expression, "returnByValue": True}
        )
        assert "exceptionDetails" not in outcome, outcome
        return outcome["result"]["value"]
    finally:
        session.detach()


def prove_the_scan_can_find_a_value(context, base):
    """A scan that finds nothing means little unless it finds a planted value."""

    page = context.new_page()
    page.goto(base + "/")
    probe = "planted-value-for-the-scan"
    page.evaluate("(probe) => { window.planted = {deep: {value: probe}}; }", probe)
    session = context.new_cdp_session(page)
    session.send(
        "Runtime.evaluate",
        {"expression": "let plantedLexical = {inner: '%s'}; 0" % probe, "replMode": True},
    )
    session.detach()
    found = scan_for_secret(page, probe)
    page.close()
    assert any(hit.endswith("planted.deep.value") for hit in found["hits"]), found
    assert "let plantedLexical.inner" in found["hits"], found


def clipboard_text(page):
    return page.evaluate("navigator.clipboard.readText()")


def credential_requests(requests):
    return [item for item in requests if item["url"].endswith(CREDENTIALS_PATH)]


def run_width(browser, base, width, height):
    context = browser.new_context(
        viewport={"width": width, "height": height},
        permissions=["clipboard-read", "clipboard-write"],
        base_url=base,
    )
    page = context.new_page()
    errors = []
    requests = []
    responses = []

    def record_request(request):
        requests.append(
            {"url": request.url, "method": request.method, "body": request.post_data or ""}
        )

    def record_response(response):
        try:
            body = response.text()
        except Exception:
            body = None
        responses.append(
            {
                "url": response.url,
                "status": response.status,
                "headers": response.headers,
                "body": body,
            }
        )

    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", record_request)
    page.on("response", record_response)

    page.goto("/")
    # Locked: nothing can be copied before the admin token is checked.
    expect(page.locator("#copy-wifi-name")).to_be_disabled()
    expect(page.locator("#copy-wifi-password")).to_be_disabled()
    expect(page.locator("#copy-wifi-name")).to_have_attribute(
        "title", "Check the admin token first"
    )
    sign_in(page)

    # The token is accepted but there is no router password yet: a click opens the panel.
    expect(page.locator("#copy-wifi-name")).to_be_enabled()
    expect(page.locator("#copy-wifi-password")).to_have_attribute(
        "title", "Enter the router password first"
    )
    page.locator("#copy-wifi-password").click()
    expect(page.locator("#credentials-panel")).to_be_visible()
    expect(page.locator("#router-admin-password")).to_be_focused()
    assert credential_requests(requests) == []
    page.locator("#close-credentials").click()

    enter_router_password(page)
    expect(page.locator("#copy-wifi-name")).to_have_attribute(
        "title", "Copy the router Wi-Fi name"
    )
    page.locator(".router-wifi-control").screenshot(path=f"test-results/router-copy-{width}.png")

    # The name: the password is not even asked for.
    page.locator("#copy-wifi-name").click()
    expect(page.locator("#copy-wifi-name")).to_have_text("Copied")
    assert clipboard_text(page) == WIFI_NAME
    expect(page.locator("#copy-wifi-name")).to_have_text("Copy Wi-Fi name")

    # The password.
    page.locator("#copy-wifi-password").click()
    expect(page.locator("#copy-wifi-password")).to_have_text("Copied")
    assert clipboard_text(page) == WIFI_PASSWORD
    expect(page.locator("#message")).to_contain_text(
        "Router Wi-Fi password copied. It stays on your clipboard until you copy something else."
    )
    expect(page.locator("#copy-wifi-password")).to_have_text("Copy Wi-Fi password")

    asked = credential_requests(requests)
    assert len(asked) == 2, asked
    assert [item["method"] for item in asked] == ["POST", "POST"]
    assert json.loads(asked[0]["body"]) == {
        "admin_password": ROUTER_PASSWORD,
        "include_password": False,
    }
    assert json.loads(asked[1]["body"]) == {
        "admin_password": ROUTER_PASSWORD,
        "include_password": True,
    }
    answered = [item for item in responses if item["url"].endswith(CREDENTIALS_PATH)]
    assert len(answered) == 2, answered
    assert [item["status"] for item in answered] == [200, 200]
    assert json.loads(answered[0]["body"]) == {"result": {"ssid": WIFI_NAME}}
    assert WIFI_PASSWORD not in answered[0]["body"]
    assert ROUTER_PASSWORD not in answered[0]["body"]
    assert json.loads(answered[1]["body"]) == {
        "result": {"ssid": WIFI_NAME, "password": WIFI_PASSWORD}
    }
    for item in answered:
        assert item["headers"]["cache-control"] == "no-store"

    # The Wi-Fi password appears in no other response and nowhere in the page.
    for item in responses:
        if item is not answered[1] and item["body"] is not None:
            assert WIFI_PASSWORD not in item["body"], item["url"]
    assert WIFI_PASSWORD not in page.content()
    visible_text = page.evaluate("document.body.innerText")
    assert WIFI_PASSWORD not in visible_text
    fields = page.evaluate(
        "Array.from(document.querySelectorAll('input, textarea, select'))"
        ".map((field) => field.value).join('\\n')"
    )
    assert WIFI_PASSWORD not in fields
    stored = page.evaluate(
        "JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])"
    )
    assert WIFI_PASSWORD not in stored
    assert page.evaluate("document.querySelectorAll('textarea[readonly]').length") == 0
    assert all(WIFI_PASSWORD not in item["url"] for item in requests)
    assert all(WIFI_PASSWORD not in cookie["value"] for cookie in context.cookies())
    prove_the_scan_can_find_a_value(context, base)
    found = scan_for_secret(page, WIFI_PASSWORD)
    assert found["hits"] == [], found["hits"]
    assert found["scanned"] > 30 and found["lexical"] > 20, found
    history = page.request.get(
        "/api/action-history", headers={"Authorization": "Bearer " + ADMIN_TOKEN}
    ).text()
    assert CREDENTIALS_PATH in history
    assert WIFI_PASSWORD not in history
    assert ROUTER_PASSWORD not in history

    # A rejected router password stops the buttons until the password changes.
    page.route(
        "**" + CREDENTIALS_PATH,
        lambda route: route.fulfill(
            status=502,
            content_type="application/json",
            body='{"error":"Router login failed. Check the router admin password and try again."}',
        ),
    )
    before = len(credential_requests(requests))
    page.locator("#copy-wifi-password").click()
    expect(page.locator("#message")).to_contain_text("Could not copy. Router login failed.")
    expect(page.locator("#copy-wifi-name")).to_be_disabled()
    expect(page.locator("#copy-wifi-password")).to_be_disabled()
    expect(page.locator("#copy-wifi-password")).to_have_attribute(
        "title", "Router password rejected. Enter it again."
    )
    expect(page.locator("#copy-wifi-password")).to_have_text("Copy Wi-Fi password")
    assert len(credential_requests(requests)) == before + 1
    page.unroute("**" + CREDENTIALS_PATH)
    page.locator("#open-router-credentials").click()
    page.locator("#router-admin-password").fill(ROUTER_PASSWORD + "-new")
    expect(page.locator("#copy-wifi-name")).to_be_enabled()
    expect(page.locator("#copy-wifi-password")).to_be_enabled()
    page.locator("#close-credentials").click()
    page.locator("#copy-wifi-name").click()
    expect(page.locator("#copy-wifi-name")).to_have_text("Copied")
    assert clipboard_text(page) == WIFI_NAME

    # Clearing the router password sends the next click to the panel instead of the router.
    before = len(credential_requests(requests))
    page.locator("#open-router-credentials").click()
    page.locator("#clear-router-password").click()
    page.locator("#close-credentials").click()
    expect(page.locator("#copy-wifi-password")).to_have_attribute(
        "title", "Enter the router password first"
    )
    page.locator("#copy-wifi-password").click()
    expect(page.locator("#credentials-panel")).to_be_visible()
    expect(page.locator("#router-admin-password")).to_be_focused()
    assert len(credential_requests(requests)) == before

    # After a reload the page is locked again and holds nothing.
    page.reload()
    expect(page.locator("#copy-wifi-name")).to_be_disabled()
    expect(page.locator("#copy-wifi-password")).to_be_disabled()
    assert WIFI_PASSWORD not in page.content()

    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
    assert not errors, errors
    context.close()


def run():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Router copy dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                for width, height in ((1920, 1000), (390, 844)):
                    run_width(browser, f"http://127.0.0.1:{port}", width, height)
                print("Router Wi-Fi copy acceptance passed at desktop and phone widths.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
