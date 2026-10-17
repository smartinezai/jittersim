"""A Wi-Fi restart drops the browser's connection. The page must wait and then show the result."""

import os
import re
import selectors
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.confirm_dialogs import answer_confirm

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
    seen = drop_connection(page, "**/api/wifi-operation?id=*", 2)

    button.click()
    # Said plainly before anything is sent.
    answer_confirm(page, "Restore name", title="Restore the default Wi-Fi name?",
                   text=["Wi-Fi will restart", "Wi-Fi menu"])
    banner = page.locator("#wifi-change-banner")
    expect(banner).to_contain_text("Reconnecting to the Pi", timeout=10000)
    assert_no_raw_error(page)
    assert_still_signed_in(page)
    expect(banner).to_contain_text("Wi-Fi change finished", timeout=20000)
    expect(banner).to_contain_text("restored")
    expect(page.locator("#wifi-reset-status")).to_contain_text("Password unchanged")
    expect(page.locator("#access-point-ssid")).to_have_value("TestNet")
    assert len(seen) >= 3
    assert_no_raw_error(page)
    assert_still_signed_in(page)
    banner.get_by_role("button", name="Dismiss").click()
    expect(banner).to_be_hidden()
    page.unroute("**/api/wifi-operation?id=*")


def check_access_point_form(page):
    page.locator("#access-point-ssid").fill("Journey net")
    page.locator("#access-point-password").fill("journey-password-1")
    seen = drop_connection(page, "**/api/wifi-operation?id=*", 3)
    page.get_by_role("button", name="Use these values on the Pi").click()
    answer_confirm(page, "Change Pi Wi-Fi", title="Change the Pi Wi-Fi?", text="Wi-Fi will restart")
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
    page.unroute("**/api/wifi-operation?id=*")

    # A reload on the new connection shows the same result until it is dismissed.
    page.reload()
    expect(page.locator("#wifi-change-banner")).to_contain_text("“Journey net”", timeout=15000)
    page.locator("#wifi-change-banner").get_by_role("button", name="Dismiss").click()
    page.reload()
    expect(page.locator("#private-dashboard")).to_be_visible(timeout=15000)
    page.wait_for_timeout(1500)
    expect(page.locator("#wifi-change-banner")).to_have_count(0)


def check_router_copy(page):
    controls = page.locator("#controls-toggle")
    if controls.is_visible() and not page.locator("#open-router-credentials").is_visible():
        controls.click()
    page.locator("#open-router-credentials").click()
    page.locator("#router-admin-password").fill("browser-router-password")
    page.locator("#check-router-password").click()
    expect(page.locator("#router-credential-state")).to_contain_text("Accepted")
    page.locator("#close-credentials").click()
    seen = drop_connection(page, "**/api/router/operation", 2)

    expect(page.locator("#sync-router-ap")).to_be_enabled(timeout=15000)
    page.locator("#sync-router-ap").click()
    answer_confirm(page, "Copy Wi-Fi", title="Copy the router Wi-Fi to the Pi?",
                   text=["Browser home using WPA2", "Wi-Fi will restart"])
    banner = page.locator("#wifi-change-banner")
    expect(banner).to_contain_text("Reconnecting to the Pi", timeout=15000)
    assert_no_raw_error(page)
    expect(page.locator("#router-action-message")).to_contain_text("Copied Browser home", timeout=25000)
    expect(banner).to_contain_text("“Browser home”")
    expect(banner).to_contain_text("router’s Wi-Fi password")
    assert len(seen) >= 3
    assert_still_signed_in(page)
    page.unroute("**/api/router/operation")
    if controls.is_visible() and controls.get_attribute("aria-expanded") == "true":
        controls.click()


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


def check_lost_acceptance_is_confirmed_without_another_write(page):
    writes = []
    completed = []
    def lose_reply(route):
        writes.append(route.request.post_data_json)
        response = route.fetch(timeout=5000, max_retries=0)
        assert response.status == 202
        assert response.json()["operation_id"] == route.request.post_data_json["request_id"]
        route.abort("connectionfailed")
        completed.append(True)
    page.route("**/api/access-point/reset-name", lose_reply)
    page.locator("#reset-wifi-name").click()
    answer_confirm(page, "Restore name")
    expect(page.locator("#wifi-change-banner")).to_contain_text("Wi-Fi change finished", timeout=15000)
    deadline = time.monotonic() + 5
    while not completed and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert completed == [True], "The accepted write reply was not dropped completely"
    assert len(writes) == 1
    assert writes[0]["request_id"]
    page.unroute("**/api/access-point/reset-name", lose_reply)
    page.locator("#wifi-change-banner").get_by_role("button", name="Dismiss").click()


def check_late_result_keeps_new_edits_and_page(page):
    page.locator('[data-page-link="router"]').click()
    fallback = page.get_by_role("button", name="Manual Pi access-point fallback")
    if fallback.get_attribute("aria-expanded") != "true":
        fallback.click()
    page.locator("#access-point-ssid").fill("Submitted name")
    page.locator("#access-point-password").fill("submitted-password")
    held = []
    writes = []
    page.route("**/api/wifi-operation?id=*", lambda route: held.append(route))
    page.on("request", lambda request: writes.append(request) if request.url.endswith("/api/access-point") else None)
    page.get_by_role("button", name="Use these values on the Pi").click()
    answer_confirm(page, "Change Pi Wi-Fi")
    expect(page.locator("#wifi-change-banner")).to_be_visible()
    page.wait_for_timeout(1000)
    assert held
    page.locator("#access-point-ssid").fill("New unsaved name")
    page.locator("#access-point-password").fill("new-unsaved-password")
    page.locator('[data-page-link="devices"]').click()
    focus = page.evaluate("document.activeElement.id")
    for route in held:
        route.continue_()
    page.unroute("**/api/wifi-operation?id=*")
    expect(page.locator("#wifi-change-banner")).to_contain_text("Wi-Fi change finished", timeout=15000)
    assert page.locator("body").get_attribute("data-section") == "devices"
    if page.locator("#controls-toggle").is_visible():
        expect(page.locator("#controls-toggle")).to_have_attribute("aria-expanded", "false")
        assert page.evaluate("document.activeElement.id") == focus
    assert len(writes) == 1
    page.locator('[data-page-link="router"]').click()
    expect(page.locator("#access-point-ssid")).to_have_value("New unsaved name")
    expect(page.locator("#access-point-password")).to_have_value("new-unsaved-password")
    assert page.evaluate('formDrafts.has("access-point-form")')
    page.locator("#wifi-change-banner").get_by_role("button", name="Dismiss").click()


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
            width = int(os.environ.get("JITTERSIM_RECONNECT_VIEWPORT", "1280"))
            context = browser.new_context(viewport={"width": width, "height": 900 if width > 600 else 844})
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
                check_lost_acceptance_is_confirmed_without_another_write(page)
                page.screenshot(path="test-results/wifi-reconnect-%s.png" % width, animations="disabled")
                check_page_loaded_during_the_outage(page)
                check_late_result_keeps_new_edits_and_page(page)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth") <= 391
                assert not errors, errors
                print("Wi-Fi restart reconnect browser acceptance passed.")
            finally:
                context.tracing.stop(path="test-results/wifi-reconnect-browser-%s.zip" % width)
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
