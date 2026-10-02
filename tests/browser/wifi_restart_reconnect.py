"""A Wi-Fi restart drops the browser's connection. The page must wait and then show the result."""

import os
import re
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

TOKEN_KEY = "jittersim.remembered.admin-token"
RAW_BROWSER_TEXT = re.compile(r"Load failed|Failed to fetch|NetworkError")


def assert_no_raw_error(page):
    text = page.locator("body").inner_text()
    assert not RAW_BROWSER_TEXT.search(text), text
    # Toasts and the status line are hidden or empty elements that inner_text skips, so read them too.
    for selector in ("#message", "#toast-message", "#wifi-reset-status", "#connection-notice"):
        content = page.locator(selector).text_content() or ""
        assert not RAW_BROWSER_TEXT.search(content), (selector, content)


def assert_still_signed_in(page):
    expect(page.locator("#private-dashboard")).to_be_visible()
    assert page.locator("#admin-token").input_value() == "browser-test"
    stored = page.evaluate("key => window.localStorage.getItem(key)", TOKEN_KEY)
    assert stored and "browser-test" in stored


def drop_connection(page, pattern, drops):
    """Fail the next `drops` requests the way a Wi-Fi restart does, then let them through."""

    seen = []

    def handle(route):
        seen.append(route.request.url)
        if len(seen) <= drops:
            route.abort("connectionfailed")
        else:
            route.continue_()

    page.route(pattern, handle)
    return seen


def check_reset_name(page):
    button = page.locator("#reset-wifi-name")
    fallback = page.get_by_role("button", name="Manual Pi access-point fallback")
    if fallback.get_attribute("aria-expanded") != "true":
        fallback.click()
    page.locator("#access-point-ssid").fill("Typed name")
    seen = drop_connection(page, "**/api/wifi-operation**", 2)

    def confirm(dialog):
        # Said plainly before anything is sent.
        assert "Wi-Fi will restart" in dialog.message
        assert "rejoin on its own" in dialog.message
        dialog.accept()

    page.once("dialog", confirm)
    button.click()
    banner = page.locator("#wifi-change-banner")
    expect(banner).to_contain_text("Reconnecting to the Pi", timeout=10000)
    assert_no_raw_error(page)
    assert_still_signed_in(page)
    expect(banner).to_contain_text("Wi-Fi change finished", timeout=20000)
    expect(banner).to_contain_text("restored")
    expect(page.locator("#wifi-reset-status")).to_contain_text("Password unchanged")
    expect(page.locator("#access-point-ssid")).to_have_value("JitterSim")
    assert len(seen) >= 3
    assert_no_raw_error(page)
    assert_still_signed_in(page)
    banner.get_by_role("button", name="Dismiss").click()
    expect(banner).to_be_hidden()
    page.unroute("**/api/wifi-operation**")


def check_access_point_form(page):
    page.locator("#access-point-ssid").fill("Journey net")
    page.locator("#access-point-password").fill("journey-password-1")
    seen = drop_connection(page, "**/api/wifi-operation**", 3)
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Use these values on the Pi").click()
    banner = page.locator("#wifi-change-banner")
    expect(banner).to_contain_text("Reconnecting to the Pi", timeout=10000)
    assert_no_raw_error(page)
    expect(banner).to_contain_text("Wi-Fi change finished", timeout=25000)
    # The network to rejoin stays on screen until it is dismissed, and the password never is.
    expect(banner).to_contain_text("“Journey net”")
    expect(banner).to_contain_text("new password")
    assert "journey-password-1" not in page.locator("body").inner_text()
    expect(page.locator("#access-point-password")).to_have_value("")
    assert len(seen) >= 4
    assert_still_signed_in(page)
    page.wait_for_timeout(1500)
    expect(banner).to_be_visible()
    page.unroute("**/api/wifi-operation**")

    # A reload on the new connection shows the same result until it is dismissed.
    page.reload()
    expect(page.locator("#wifi-change-banner")).to_contain_text("“Journey net”", timeout=15000)
    page.locator("#wifi-change-banner").get_by_role("button", name="Dismiss").click()
    page.reload()
    expect(page.locator("#private-dashboard")).to_be_visible(timeout=15000)
    page.wait_for_timeout(1500)
    expect(page.locator("#wifi-change-banner")).to_have_count(0)


def check_router_copy(page):
    page.locator("#open-router-credentials").click()
    page.locator("#router-admin-password").fill("browser-router-password")
    page.locator("#check-router-password").click()
    expect(page.locator("#router-credential-state")).to_contain_text("Accepted")
    page.locator("#close-credentials").click()
    seen = drop_connection(page, "**/api/router/operation", 2)

    def confirm(dialog):
        assert "Browser home using WPA2" in dialog.message
        assert "Wi-Fi will restart" in dialog.message
        dialog.accept()

    page.once("dialog", confirm)
    page.locator("#sync-router-ap").click()
    banner = page.locator("#wifi-change-banner")
    expect(banner).to_contain_text("Reconnecting to the Pi", timeout=15000)
    assert_no_raw_error(page)
    expect(page.locator("#router-action-message")).to_contain_text("Copied Browser home", timeout=25000)
    expect(banner).to_contain_text("“Browser home”")
    expect(banner).to_contain_text("router’s Wi-Fi password")
    assert len(seen) >= 3
    assert_still_signed_in(page)
    page.unroute("**/api/router/operation")


def check_page_loaded_during_the_outage(page):
    """Opening the page while the Pi is unreachable keeps the token and checks again."""

    page.route("**/api/**", lambda route: route.abort("connectionfailed"))
    page.reload()
    expect(page.locator("#token-check-status")).to_contain_text("Lost the connection to the Pi", timeout=15000)
    assert_no_raw_error(page)
    expect(page.locator("#admin-token")).to_have_value("browser-test")
    stored = page.evaluate("key => window.localStorage.getItem(key)", TOKEN_KEY)
    assert stored and "browser-test" in stored
    assert "Rejected" not in page.locator("#admin-credential-state").text_content()
    page.unroute("**/api/**")
    expect(page.locator("#private-dashboard")).to_be_visible(timeout=20000)


def run():
    process = subprocess.Popen([sys.executable, "-m", "tests.browser.fixture"],
                               stdout=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Browser fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"))
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(f"http://127.0.0.1:{port}")
                page.locator("#open-admin-credentials").click()
                page.locator("#admin-token").fill("browser-test")
                page.locator("#check-admin-token").click()
                expect(page.locator("#admin-credential-state")).to_contain_text("Accepted")
                page.locator("#remember-admin-token").check()
                page.locator("#close-credentials").click()
                page.locator('[data-page-link="router"]').click()
                check_reset_name(page)
                check_access_point_form(page)
                check_router_copy(page)
                page.screenshot(path="test-results/wifi-reconnect-desktop.png", animations="disabled")
                check_page_loaded_during_the_outage(page)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth") <= 391
                assert not errors, errors
                print("Wi-Fi restart reconnect browser acceptance passed.")
            finally:
                context.tracing.stop(path="test-results/wifi-reconnect-browser.zip")
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
