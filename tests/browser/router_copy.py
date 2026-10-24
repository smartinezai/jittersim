"""Copy router Wi-Fi settings to the Pi through the served dashboard.

The fixture's fake router accepts any router password and answers with the Wi-Fi name
"Browser home" and the Wi-Fi password "browser-wifi-password". Nothing here is a real credential.
"""

import json
import os
import re
import selectors
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.confirm_dialogs import answer_confirm, cancel_with_button, cancel_with_escape
from tests.browser.phone import assert_router_status_is_read_only, is_phone, sidebar_control

ADMIN_TOKEN = "browser-test"
ROUTER_PASSWORD = "browser-router-password"
WIFI_NAME = "Browser home"
WIFI_PASSWORD = "browser-wifi-password"
SYNC_PATH = "/api/router/sync-ap"
PREVIEW_PATH = "/api/router/preview-ap"
CREDENTIALS_PATH = "/api/router/wifi-credentials"
CHECK_PATH = "/api/router/check"


def sign_in(page):
    expect(page.locator("#private-dashboard")).to_be_hidden()
    sidebar_control(page, "#open-admin-credentials").click()
    page.locator("#admin-token").fill(ADMIN_TOKEN)
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    page.locator("#close-credentials").click()


def enter_router_password(page):
    sidebar_control(page, "#open-router-credentials").click()
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


def api_calls(requests, path):
    return [item for item in requests if item["url"].endswith(path)]


def check_router_form_errors(page, requests):
    """Associate local check failures with the password without losing the person's input."""

    sidebar_control(page, "#open-router-credentials").click()
    password = page.locator("#router-admin-password")
    remember = page.locator("#remember-router-password")
    check = page.locator("#check-router-password")
    summary = page.locator("#router-password-errors")
    link = page.locator("#router-password-error-link")
    status = page.locator("#credential-router-status")
    remember.check()

    def clean():
        expect(summary).to_be_hidden()
        expect(link).to_be_empty()
        expect(password).not_to_have_attribute("aria-invalid", "true")

    def failure(message, invalid, value):
        expect(summary).to_be_visible()
        expect(status).to_contain_text(message)
        expect(link).to_have_text(status.inner_text())
        expect(link).to_have_attribute("href", "#router-admin-password")
        expect(password).to_have_attribute("aria-describedby", "credential-router-status")
        if invalid:
            expect(password).to_have_attribute("aria-invalid", "true")
        else:
            expect(password).not_to_have_attribute("aria-invalid", "true")
        expect(password).to_have_value(value)
        expect(remember).to_be_checked()
        expect(check).to_be_enabled()
        expect(summary).to_be_focused()
        link.click()
        expect(password).to_be_focused()

    before = len(api_calls(requests, CHECK_PATH))
    check.click()
    failure("Enter the router admin password first", True, "")
    assert len(api_calls(requests, CHECK_PATH)) == before
    password.fill("fixture-rejected-password")
    clean()
    route_path = "**" + CHECK_PATH
    for code, message, invalid in (
        (502, "Router login failed. Check the router admin password.", True),
        (502, "Could not contact the router", False),
        (503, "Fixture router service unavailable", False),
    ):
        def reject(route):
            route.fulfill(status=code, json={"error": message})

        page.route(route_path, reject)
        try:
            check.click()
            failure(message, invalid, "fixture-rejected-password")
            if invalid:
                before = len(api_calls(requests, CHECK_PATH))
                check.click()
                failure("Change it before trying again", True, "fixture-rejected-password")
                assert len(api_calls(requests, CHECK_PATH)) == before
                password.fill("fixture-edited-password")
                clean()
                password.fill("fixture-rejected-password")
        finally:
            page.unroute(route_path, reject)

    # Hold the next permitted reply to observe cleanup before a successful check finishes.
    held = []

    def hold(route):
        held.append(route)

    page.route(route_path, hold)
    try:
        check.click()
        expect(check).to_be_disabled()
        clean()
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(50)
        assert len(held) == 1
        held[0].fulfill(json={"result": {"ok": True}})
        expect(status).to_contain_text("Router password accepted")
        expect(check).to_be_enabled()
        clean()
        expect(password).to_have_value("fixture-rejected-password")
        expect(remember).to_be_checked()
    finally:
        page.unroute(route_path, hold)

    # Restore the original journey's empty, unremembered password before its real fixture check.
    page.locator("#clear-router-password").click()
    clean()
    expect(password).to_have_value("")
    expect(password).to_be_focused()
    expect(remember).to_be_checked()
    check.click()
    failure("Enter the router admin password first", True, "")
    page.locator("#clear-router-password").click()
    clean()
    expect(password).to_be_focused()
    expect(remember).to_be_checked()
    remember.uncheck()
    page.locator("#close-credentials").click()


def check_again_is_refused_with_a_wait(page, requests, responses):
    """A second Check password right after the first is answered with a wait by the Pi."""

    sidebar_control(page, "#open-router-credentials").click()
    check = page.locator("#check-router-password")
    before = len(api_calls(requests, CHECK_PATH))
    check.click()
    expect(check).to_have_text(re.compile(r"^Wait \d+ s$"))
    expect(check).to_be_disabled()
    expect(page.locator("#credential-router-status")).to_contain_text("Wait")
    expect(page.locator("#credential-router-status")).to_contain_text("before checking the router password again")
    # The earlier success still stands: a refusal says nothing about the password.
    expect(page.locator("#router-credential-state")).to_have_text("Accepted")
    assert len(api_calls(requests, CHECK_PATH)) == before + 1
    refused = [item for item in responses if item["url"].endswith(CHECK_PATH) and item["status"] == 429]
    assert len(refused) == 1, refused
    assert 1 <= json.loads(refused[0]["body"])["retry_after"] <= 15
    assert ROUTER_PASSWORD not in refused[0]["body"]
    page.locator("#close-credentials").click()


CONFIRMATION = dict(
    title="Copy the router Wi-Fi to the Pi?",
    text=[
        f"{WIFI_NAME} using WPA2 (2.4 GHz)",
        "Wi-Fi will restart now", "lose its connection for up to a minute",
        "cannot rejoin by itself", "choose the network again in the Wi-Fi menu",
        "waits for the Pi", "shows how the change went",
        "Keep a separate management connection",
    ],
    absent=WIFI_PASSWORD,
)


def run_width(browser, base, width, height):
    context = browser.new_context(
        viewport={"width": width, "height": height},
        base_url=base,
    )
    context.add_init_script("""(() => {
        window.clipboardCalls = {reads: 0, writes: 0};
        Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
            readText: () => { window.clipboardCalls.reads++; throw Error('Unexpected clipboard read'); },
            writeText: () => { window.clipboardCalls.writes++; throw Error('Unexpected clipboard write'); },
            read: () => { window.clipboardCalls.reads++; throw Error('Unexpected clipboard read'); },
            write: () => { window.clipboardCalls.writes++; throw Error('Unexpected clipboard write'); }
        }});
        const execCommand = Document.prototype.execCommand;
        Document.prototype.execCommand = function(command, ...args) {
            if (command === 'copy' || command === 'cut' || command === 'paste') {
                window.clipboardCalls[command === 'paste' ? 'reads' : 'writes']++;
                throw Error('Unexpected clipboard command');
            }
            return execCommand.call(this, command, ...args);
        };
    })();""")
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
    button = page.locator("#sync-router-ap")
    expect(button).to_be_disabled()
    expect(button).to_have_attribute("title", "Check the admin token first")
    assert page.locator("#copy-wifi-name, #copy-wifi-password").count() == 0
    sign_in(page)

    # The button remains available without a router password so it can open credentials.
    expect(button).to_be_enabled()
    expect(button).to_have_attribute("title", "Enter the router password first")
    sidebar_control(page, "#sync-router-ap").click()
    expect(page.locator("#credentials-panel")).to_be_visible()
    expect(page.locator("#router-admin-password")).to_be_focused()
    assert api_calls(requests, PREVIEW_PATH) == []
    page.locator("#close-credentials").click()

    check_router_form_errors(page, requests)
    enter_router_password(page)
    check_again_is_refused_with_a_wait(page, requests, responses)
    expect(button).to_be_enabled()
    assert_router_status_is_read_only(page)
    expect(page.locator("#router-wifi-label")).to_have_text("Router Wi-Fi")
    assert api_calls(requests, "/api/router/wifi") == []
    expect(page.locator("#router-action-message")).to_have_text("")
    sidebar_control(page, "#sync-router-ap")
    control = page.locator(".router-wifi-control").bounding_box()
    row = page.locator(".router-wifi-copy").bounding_box()
    switch = page.locator("#router-wifi-toggle").bounding_box()
    action = button.bounding_box()
    assert abs(action["x"] - row["x"]) <= 1
    assert abs(action["width"] - row["width"]) <= 1
    if is_phone(page):
        bar = page.locator(".safety-bar").bounding_box()
        assert abs(bar["y"] + bar["height"] - height) <= 1, bar
        assert switch["y"] >= bar["y"]
        assert switch["y"] + switch["height"] <= bar["y"] + bar["height"] + 0.5
        assert row["y"] + row["height"] <= bar["y"] + 0.5, (row, bar)
    else:
        assert row["y"] >= control["y"] + control["height"]
        assert abs(row["x"] - control["x"]) <= 1
        assert abs(row["width"] - control["width"]) <= 1
        assert switch["x"] > control["x"] + control["width"] / 2
        assert switch["y"] >= control["y"]
    page.screenshot(path=f"test-results/router-copy-{width}.png", animations="disabled")

    # A status in the sidebar is visible even while another dashboard page is open.
    page.locator('[data-page-link="devices"]').click()
    sidebar_control(page, "#sync-router-ap")
    expect(button).to_be_visible()
    button.focus()
    expect(button).to_be_focused()
    page.keyboard.press("Enter")
    cancel_with_escape(page, button, verb="Copy Wi-Fi", **CONFIRMATION)
    expect(page.locator("#router-action-message")).to_contain_text("Copy cancelled")
    expect(page.locator("#router-action-message")).to_be_visible()
    assert api_calls(requests, SYNC_PATH) == []

    # A held preview keeps both router actions disabled until it finishes.
    held = []
    page.route("**" + PREVIEW_PATH, lambda route: held.append(route))
    sidebar_control(page, "#sync-router-ap").click()
    expect(button).to_be_disabled()
    assert_router_status_is_read_only(page)
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    assert len(held) == 1
    assert api_calls(requests, SYNC_PATH) == []
    held[0].continue_()
    page.unroute("**" + PREVIEW_PATH)
    # The preview is back, so the question opens. Cancel is the button that has focus, and
    # pressing it cancels the copy.
    cancel_with_button(page, button, **CONFIRMATION)
    expect(button).to_be_enabled()
    expect(page.locator("#router-action-message")).to_contain_text("Copy cancelled")

    sidebar_control(page, "#sync-router-ap").click()
    answer_confirm(page, "Copy Wi-Fi", **CONFIRMATION)
    expect(page.locator("#router-action-message")).to_contain_text(
        f"Copied {WIFI_NAME} using WPA2"
    )
    syncs = api_calls(requests, SYNC_PATH)
    assert len(syncs) == 1, syncs
    assert syncs[0]["method"] == "POST"
    body = json.loads(syncs[0]["body"])
    assert body["admin_password"] == ROUTER_PASSWORD
    assert len(body["expected_fingerprint"]) == 64
    assert body["confirm_legacy"] is False
    assert api_calls(requests, CREDENTIALS_PATH) == []
    assert page.evaluate("window.clipboardCalls") == {"reads": 0, "writes": 0}
    assert WIFI_PASSWORD not in page.content()
    assert WIFI_PASSWORD not in page.evaluate(
        "JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])"
    )
    found = scan_for_secret(page, WIFI_PASSWORD)
    assert found["hits"] == [], found["hits"]
    history = page.request.get(
        "/api/action-history", headers={"Authorization": "Bearer " + ADMIN_TOKEN}
    ).text()
    assert WIFI_PASSWORD not in history
    assert ROUTER_PASSWORD not in history

    # A failed router login blocks another attempt until the password changes.
    page.route("**" + PREVIEW_PATH, lambda route: route.fulfill(
        status=502, content_type="application/json",
        body='{"error":"Router login failed. Check the router admin password and try again."}',
    ))
    sidebar_control(page, "#sync-router-ap").click()
    expect(page.locator("#router-action-message")).to_contain_text("Router login failed")
    expect(button).to_be_disabled()
    expect(button).to_have_attribute("title", "Router password rejected. Enter it again.")
    page.unroute("**" + PREVIEW_PATH)
    sidebar_control(page, "#open-router-credentials").click()
    page.locator("#router-admin-password").fill(ROUTER_PASSWORD + "-new")
    expect(button).to_be_enabled()
    page.locator("#close-credentials").click()

    # A missing helper is reported as unavailable without calling the router.
    def without_helper(route):
        response = route.fetch()
        body = response.json()
        body["helper_configured"] = False
        route.fulfill(response=response, json=body)

    page.route("**/api/health", without_helper)
    page.reload()
    sign_in(page)
    expect(button).to_be_disabled()
    expect(button).to_have_attribute("title", "Needs the Pi helper")
    page.unroute("**/api/health")

    for item in responses:
        if item["body"] is not None:
            assert WIFI_PASSWORD not in item["body"], item["url"]
            assert ROUTER_PASSWORD not in item["body"], item["url"]
    assert all(WIFI_PASSWORD not in item["url"] for item in requests)
    assert all(WIFI_PASSWORD not in cookie["value"] for cookie in context.cookies())
    prove_the_scan_can_find_a_value(context, base)
    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
    assert not errors, errors
    context.close()


@contextmanager
def fixture_server():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Router copy dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        yield port
    finally:
        process.terminate()
        process.wait(timeout=10)


def run():
    Path("test-results").mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
        )
        try:
            for width, height in ((1920, 1000), (375, 844)):
                # The Pi spaces router password checks apart, so each width gets its own Pi.
                with fixture_server() as port:
                    run_width(browser, f"http://127.0.0.1:{port}", width, height)
            print("Router Wi-Fi import acceptance passed at desktop and 375px widths.")
        finally:
            browser.close()


if __name__ == "__main__":
    run()
