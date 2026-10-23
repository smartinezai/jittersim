"""Exercise first enrollment, failures and removal in the served dashboard."""

import os
import re
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.a11y_axe import load_engine
from tests.browser.confirm_dialogs import answer_confirm, cancel_with_escape
from tests.browser.phone import sidebar_control


NAME = re.compile("jitter\\s*sim", re.IGNORECASE)
# Everything a person or a screen reader could meet: the title, the text and the attributes of the
# whole page including the parts that stay hidden until sign-in, but not the script itself.
MARKUP = """() => {
  const copy = document.body.cloneNode(true);
  copy.querySelectorAll('script,style').forEach((node) => node.remove());
  return [document.title, copy.innerHTML, copy.textContent].join('\\n');
}"""


def check_neutral_before_sign_in(page, base):
    expect(page).to_have_title("Sign in")
    page.locator("#open-admin-credentials").click()
    expect(page.locator("#admin-token-help")).not_to_be_empty()
    assert not NAME.search(page.evaluate(MARKUP)), "the page names the product before sign-in"
    assert not NAME.search(page.locator("body").inner_text())
    page.locator("#close-credentials").click()
    icon = page.request.get(base + "/favicon.ico")
    assert icon.status == 200 and icon.headers["content-type"] == "image/x-icon"
    assert not NAME.search(" ".join(f"{k}: {v}" for k, v in icon.headers.items()))


def check_this_device_is_named(page, width):
    """The browser's own Wi-Fi device is named in words on its row and in the Block question."""

    page.locator('a[data-page-link="devices"]').first.click()
    row = page.locator("#connected-devices .connected-row")
    expect(row).to_have_count(1)
    badge = row.locator(".connected-address .this-device-badge")
    expect(badge).to_have_text("This device")
    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
    # The badge says it in words, not by colour. Axe checks that the words are readable.
    page.evaluate(load_engine()[0])
    result = page.evaluate("""async () => {
        const run = await axe.run(document.querySelector('.this-device-badge'),
                                  {runOnly: ['color-contrast']});
        return [run.violations.length, run.passes.map((rule) => rule.id)];
    }""")
    assert result == [0, ["color-contrast"]], result
    page.locator('a[data-page-link="access"]').first.click()
    block = page.locator("#access-device-list").get_by_role("button", name=re.compile("^Block "))
    block.click()
    cancel_with_escape(page, block, verb="Continue", text=[
        "This takes effect immediately.",
        "This is the device you are using: its connection will be affected."])


def run():
    environment = {**os.environ, "JITTERSIM_BROWSER_TEST_WIFI_PEER": "1"}
    process = subprocess.Popen([sys.executable, "-m", "tests.browser.fixture"],
                               stdout=subprocess.PIPE, text=True, env=environment)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Dashboard access fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"))
            try:
                for width, height in ((1920, 1000), (390, 844)):
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    errors = []
                    requested = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda request: requested.append(request.url))
                    failed = []
                    page.on("response", lambda response: failed.append((response.status, response.url))
                            if response.status >= 400 else None)
                    page.goto(f"http://127.0.0.1:{port}")
                    check_neutral_before_sign_in(page, f"http://127.0.0.1:{port}")
                    assert not failed, failed  # no 404 for the favicon or anything else
                    expect(page.locator("#private-dashboard")).to_be_hidden()
                    expect(page.locator("#open-router-credentials")).to_be_hidden()
                    page.locator("#open-admin-credentials").click()
                    page.locator("#admin-token").fill("wrong-token")
                    page.locator("#check-admin-token").click()
                    expect(page.locator("#token-check-status")).to_contain_text("failed")
                    expect(page.locator("#allow-dashboard-device")).to_be_disabled()
                    page.locator("#admin-token").fill("browser-test")
                    page.locator("#check-admin-token").click()
                    expect(page.locator("#token-check-status")).to_contain_text("Allow this device")
                    # The status line is silent, so the toast says it and the working message is gone.
                    expect(page.locator("#toast-message")).to_have_text("Token accepted. Choose Allow this device.")
                    expect(page.locator("#allow-dashboard-device")).to_be_enabled()
                    expect(page.locator("#private-dashboard")).to_be_hidden()
                    assert not any(url.endswith("/api/devices") for url in requested)
                    assert not any("/api/updates" in url or "/api/update-preferences" in url for url in requested)
                    page.locator("#close-credentials").click()
                    page.route("**/api/dashboard-access/register-self", lambda route: route.fulfill(
                        status=503, content_type="application/json", body='{"error":"Cannot save fixture access"}'))
                    page.locator("#allow-dashboard-device").click()
                    expect(page.locator("#dashboard-access-status")).to_contain_text("Cannot save")
                    expect(page.locator("#private-dashboard")).to_be_hidden()
                    page.unroute("**/api/dashboard-access/register-self")
                    page.locator("#dashboard-access-name").fill("Browser Mac")
                    page.locator("#allow-dashboard-device").click()
                    expect(page.locator("#private-dashboard")).to_be_visible()
                    expect(page.locator("#dashboard-access-list")).to_contain_text("Browser Mac")
                    expect(page.locator("#admin-credential-state")).to_contain_text("Accepted")
                    # After sign-in the page may use the real name and takes it from a private route.
                    expect(page).to_have_title("JitterSim")
                    expect(page.locator("#brand-title")).to_have_text("JitterSim")
                    expect(page.locator("#measure-command")).to_have_text("jittersim measure --label normal")
                    expect(page.locator("#update-token-command")).to_have_text("sudo jittersim update set-token")
                    expect(page.locator("#release-status")).not_to_have_text("Sign in to read update status.")
                    assert any(url.endswith("/api/updates") for url in requested)
                    expect(page.locator("#access-point-ssid")).to_have_value("TestNet")
                    sidebar_control(page, "#open-admin-credentials").click()
                    page.locator("#remember-admin-token").check()
                    page.locator("#close-credentials").click()
                    page.reload()
                    expect(page.locator("#private-dashboard")).to_be_visible()
                    expect(page.locator("#dashboard-access-list")).to_contain_text("Browser Mac")
                    page.screenshot(path=f"test-results/dashboard-access-{width}.png")
                    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
                    check_this_device_is_named(page, width)
                    page.locator('[data-page-link="access"]').click()
                    for _ in range(100):
                        if page.evaluate("() => adminToolsPhysicalRead === null"):
                            break
                        page.wait_for_timeout(50)
                    assert page.evaluate("() => adminToolsPhysicalRead === null"), (
                        "the previous diagnostics read did not finish within five seconds"
                    )
                    held_diagnostics = []

                    def hold_diagnostics(route):
                        response = route.fetch()
                        assert response.ok, response.status
                        held_diagnostics.append((route, response))

                    page.route("**/api/diagnostics", hold_diagnostics)
                    page.evaluate("() => { window.delayedPrivateRead = loadAdminTools(); }")
                    for _ in range(500):
                        if held_diagnostics:
                            break
                        page.wait_for_timeout(10)
                    assert len(held_diagnostics) == 1, "the private diagnostics read did not start"
                    remove = page.locator("#dashboard-access-list button")
                    remove.click()
                    cancel_with_escape(page, remove, verb="Remove access", title="Remove dashboard access?",
                                       text="Removing this device returns it to sign-in.")
                    remove.click()
                    answer_confirm(page, "Remove access", title="Remove dashboard access?")
                    expect(page.locator("#private-dashboard")).to_be_hidden()
                    expect(page.locator("#token-check-status")).to_contain_text("Allow this device")
                    # Signed out again, the name goes away again.
                    expect(page).to_have_title("Sign in")
                    expect(page.locator("#brand-title")).to_have_text("Network lab")
                    expect(page.locator("#update-token-command")).to_have_text("the setup command shown after sign-in")
                    expect(page.locator("#online-check-status")).to_have_text("Sign in to read update status.")
                    expect(page.locator("#online-daily-check")).to_be_disabled()
                    assert not NAME.search(page.evaluate(MARKUP))
                    # A reply read while enrolled must not restore private text after removal.
                    route, response = held_diagnostics.pop()
                    route.fulfill(response=response)
                    page.evaluate("window.delayedPrivateRead")
                    page.unroute("**/api/diagnostics", hold_diagnostics)
                    assert not NAME.search(page.evaluate(MARKUP))
                    expect(page.locator("#random-website-rows")).to_be_empty()
                    expect(page.locator("#random-website-device option")).to_have_count(1)
                    expect(page.locator("#random-website-device")).to_have_text("All devices")
                    assert not errors, errors
                    context.close()
                print("Dashboard access browser acceptance passed.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
