"""Exercise first enrollment, failures and removal in the served dashboard."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.phone import sidebar_control


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
                    page.goto(f"http://127.0.0.1:{port}")
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
                    expect(page.locator("#allow-dashboard-device")).to_be_enabled()
                    expect(page.locator("#private-dashboard")).to_be_hidden()
                    assert not any(url.endswith("/api/devices") for url in requested)
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
                    sidebar_control(page, "#open-admin-credentials").click()
                    page.locator("#remember-admin-token").check()
                    page.locator("#close-credentials").click()
                    page.reload()
                    expect(page.locator("#private-dashboard")).to_be_visible()
                    expect(page.locator("#dashboard-access-list")).to_contain_text("Browser Mac")
                    page.screenshot(path=f"test-results/dashboard-access-{width}.png")
                    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
                    page.locator('[data-page-link="access"]').click()
                    page.once("dialog", lambda dialog: dialog.accept())
                    page.locator("#dashboard-access-list button").click()
                    expect(page.locator("#private-dashboard")).to_be_hidden()
                    expect(page.locator("#token-check-status")).to_contain_text("Allow this device")
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
