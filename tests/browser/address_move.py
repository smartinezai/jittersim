"""A Mac that comes back with a new private Wi-Fi address can take over its saved entries."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.confirm_dialogs import answer_confirm, cancel_with_escape


def run():
    environment = {**os.environ, "JITTERSIM_BROWSER_TEST_WIFI_PEER": "1",
                   "JITTERSIM_BROWSER_TEST_OLD_ADDRESS": "1"}
    process = subprocess.Popen([sys.executable, "-m", "tests.browser.fixture"],
                               stdout=subprocess.PIPE, text=True, env=environment)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Address move fixture did not start")
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
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{port}")
                    # Nothing about devices or addresses shows before the token is accepted.
                    expect(page.locator("#address-move-card")).to_be_hidden()
                    expect(page.locator("#address-type-note")).to_be_hidden()
                    assert "jittersim" not in page.locator("#dashboard-access-panel").inner_text().lower()
                    page.locator("#open-admin-credentials").click()
                    page.locator("#admin-token").fill("browser-test")
                    page.locator("#check-admin-token").click()
                    expect(page.locator("#token-check-status")).to_contain_text("Allow this device")
                    page.locator("#close-credentials").click()

                    prompt = page.locator("#address-move-card")
                    expect(prompt).to_be_visible()
                    expect(page.locator("#address-move-text")).to_have_text(
                        "This looks like a new address for one of your devices. "
                        "Allow this device and keep it protected?")
                    expect(page.locator("#address-move-picker")).to_contain_text("Old Mac")
                    expect(page.locator("#address-type-note")).to_contain_text("private Wi-Fi address")
                    expect(page.locator("#address-help")).to_contain_text("Private Wi-Fi address, Fixed")
                    assert "jittersim" not in prompt.inner_text().lower()
                    page.screenshot(path=f"test-results/address-move-{width}.png")
                    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1

                    # Not one of mine hides the prompt without changing anything.
                    page.locator("#address-move-dismiss").click()
                    expect(prompt).to_be_hidden()
                    expect(page.locator("#private-dashboard")).to_be_hidden()
                    page.reload()
                    page.locator("#open-admin-credentials").click()
                    page.locator("#admin-token").fill("browser-test")
                    page.locator("#check-admin-token").click()
                    expect(prompt).to_be_visible()
                    page.locator("#close-credentials").click()

                    # A refused move shows the reason and keeps the prompt.
                    page.route("**/api/dashboard-access/move", lambda route: route.fulfill(
                        status=409, content_type="application/json",
                        body='{"error":"There is nothing to move for that device."}'))
                    page.locator("#address-move-confirm").click()
                    expect(page.locator("#address-move-text")).to_contain_text("nothing to move")
                    expect(page.locator("#private-dashboard")).to_be_hidden()
                    page.unroute("**/api/dashboard-access/move")

                    page.locator("#address-move-confirm").click()
                    expect(page.locator("#private-dashboard")).to_be_visible()
                    expect(prompt).to_be_hidden()
                    page.locator('[data-page-link="access"]').click()
                    expect(page.locator("#dashboard-access-list")).to_contain_text("replaced by")
                    expect(page.locator("#address-move-replaced")).to_contain_text("Old Mac")

                    # Undo returns the old entry and signs this device out again.
                    undo = page.locator("#address-move-replaced button")
                    undo.click()
                    cancel_with_escape(page, undo, verb="Reverse move", title="Reverse this move?",
                                       text="Reverse the move for Old Mac?")
                    expect(page.locator("#private-dashboard")).to_be_visible()
                    undo.click()
                    answer_confirm(page, "Reverse move", title="Reverse this move?")
                    expect(page.locator("#private-dashboard")).to_be_hidden()
                    expect(prompt).to_be_visible()
                    assert not errors, errors
                    context.close()
                print("Address move browser acceptance passed.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
