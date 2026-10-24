"""Exercise name recovery through the served release dashboard."""

import os
import re
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.confirm_dialogs import answer_confirm, cancel_with_escape
from tests.browser.phone import assert_router_status_is_read_only, sidebar_control


def check_wifi_recovery(page):
    button = page.locator("#reset-wifi-name")
    fallback = page.get_by_role("button", name="Manual Pi access-point fallback")
    expect(fallback).to_have_attribute("aria-expanded", "false")
    fallback.click()
    expect(fallback).to_have_attribute("aria-expanded", "true")
    expect(button).to_be_enabled()
    button.scroll_into_view_if_needed()
    expect(button).to_be_in_viewport()
    field = page.locator("#access-point-ssid")
    field.fill("Copied network")
    requests = []
    page.on("request", lambda request: requests.append(request)
            if request.url.endswith("/api/access-point/reset-name") else None)
    ask = dict(title="Restore the Wi-Fi name?", text="The password and security type stay the same.")
    button.click()
    cancel_with_escape(page, button, verb="Restore name", **ask)
    expect(field).to_have_value("Copied network")
    expect(button).to_be_enabled()
    assert not requests

    page.route("**/api/access-point/reset-name", lambda route: route.fulfill(
        status=502, content_type="application/json", body='{"error":"Simulated reset failure"}'))
    button.click()
    answer_confirm(page, "Restore name", **ask)
    expect(page.locator("#wifi-reset-status")).to_contain_text("Simulated reset failure")
    expect(button).to_be_enabled()
    expect(field).to_have_value("Copied network")
    page.unroute("**/api/access-point/reset-name")

    held = []
    page.route("**/api/access-point/reset-name", lambda route: held.append(route))
    with page.expect_request("**/api/access-point/reset-name", timeout=10000):
        button.click()
        answer_confirm(page, "Restore name", **ask)
    expect(button).to_be_disabled()
    assert len(held) == 1
    held[0].continue_()
    expect(field).to_have_value("TestNet")
    expect(button).to_be_enabled()
    assert len(requests) == 2
    page.unroute("**/api/access-point/reset-name")
    page.route("**/api/health", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body='{"helper_configured":false,"mode":"configuration-only"}'))
    page.reload()
    expect(button).to_be_disabled()
    page.unroute("**/api/health")
    page.reload()
    expect(button).to_be_enabled()


def check_router_copy(page):
    sidebar_control(page, "#open-router-credentials").click()
    page.locator("#router-admin-password").fill("browser-router-password")
    page.locator("#check-router-password").click()
    expect(page.locator("#router-credential-state")).to_contain_text("Accepted")
    page.locator("#remember-router-password").check()
    page.locator("#close-credentials").click()
    button = sidebar_control(page, "#sync-router-ap")
    copies = []
    page.on("request", lambda request: copies.append(request)
            if request.url.endswith("/api/router/sync-ap") else None)

    ask = dict(title="Copy the router Wi-Fi to the Pi?", text="Browser home using WPA2 (2.4 GHz)",
               absent="browser-wifi-password")
    button.click()
    cancel_with_escape(page, button, verb="Copy Wi-Fi", **ask)
    expect(page.locator("#router-action-message")).to_contain_text("Copy cancelled")
    assert not copies
    button.click()
    answer_confirm(page, "Copy Wi-Fi", **ask)
    expect(page.locator("#router-action-message")).to_contain_text("Copied Browser home using WPA2")
    assert len(copies) == 1
    assert len(copies[0].post_data_json["expected_fingerprint"]) == 64
    # The network to join stays on screen until it is dismissed. It sits above the controls.
    banner = page.locator("#wifi-change-banner")
    expect(banner).to_contain_text("Browser home")
    banner.get_by_role("button", name="Dismiss").click()
    expect(banner).to_be_hidden()


def check_router_status(page):
    button = page.locator("#router-wifi-toggle")
    checks = []
    page.on("request", lambda request: checks.append(request.post_data_json["refresh"])
            if request.url.endswith("/api/router/status") else None)
    assert_router_status_is_read_only(page)
    sidebar_control(page, "#refresh-router-wifi").click()
    # The password check a moment ago used the router budget, so the Pi does not ask the router
    # again yet. The page says how long to wait and shows no state it does not have.
    expect(page.locator("#message")).to_contain_text("choose Check router Wi-Fi again")
    wait = int(re.search(r"Wait (\d+) second", page.locator("#message").inner_text()).group(1))
    assert 1 <= wait <= 15 and checks == [True], (wait, checks)
    expect(button).to_have_attribute("data-unknown", "true")
    page.wait_for_timeout((wait + 1) * 1000)
    checks.clear()
    sidebar_control(page, "#refresh-router-wifi").click()
    # Router Wi-Fi switching is refused for recovery safety. The label only shows the state.
    expect(button).to_have_text("On", timeout=10000)
    assert checks and checks[0] is True and all(refresh is False for refresh in checks[1:])
    assert_router_status_is_read_only(page)
    expect(page.locator("#router-wifi-label")).to_have_text("Router Wi-Fi")
    expect(button).to_be_in_viewport()
    writes = []
    page.on("request", lambda request: writes.append(request)
            if request.url.endswith("/api/router/wifi") else None)
    button.click(force=True)
    page.wait_for_timeout(300)
    assert not writes
    expect(button).to_have_text("On")
    assert page.request.get(page.url + "api/health").status == 200


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
            context = browser.new_context(viewport={"width": 1920, "height": 1000})
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
                check_router_copy(page)
                check_router_status(page)
                check_wifi_recovery(page)
                expect(page.locator("#reset-wifi-name")).to_be_in_viewport()
                assert_router_status_is_read_only(page)
                sidebar_control(page, "#refresh-router-wifi").click()
                expect(page.locator("#router-wifi-toggle")).to_have_attribute(
                    "data-unknown", "false", timeout=10000)
                page.screenshot(path="test-results/wifi-pi-desktop.png", animations="disabled")
                page.set_viewport_size({"width": 1280, "height": 800})
                # The full-width header and open utilities retain complete controls,
                # with Panic and read-only router status in the separate safety bar.
                expect(page.locator("#router-wifi-toggle")).to_be_in_viewport(ratio=1)
                expect(page.locator("#panic-button")).to_be_in_viewport(ratio=1)
                expect(page.locator("#sync-router-ap")).to_be_in_viewport(ratio=1)
                sidebar = page.locator(".sidebar").bounding_box()
                assert abs(sidebar["x"]) <= 0.5 and abs(sidebar["width"] - 1280) <= 1, sidebar
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path="test-results/wifi-pi-laptop.png", animations="disabled")
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#reset-wifi-name").scroll_into_view_if_needed()
                expect(page.locator("#reset-wifi-name")).to_be_in_viewport()
                page.screenshot(path="test-results/wifi-pi-phone.png", animations="disabled")
                assert page.evaluate("document.documentElement.scrollWidth") <= 391
                page.set_viewport_size({"width": 390, "height": 360})
                # On a phone the credential buttons are in the Controls sheet.
                sidebar_control(page, "#open-admin-credentials").click()
                page.locator("#admin-token").fill("browser-test")
                expect(page.locator("#check-admin-token")).to_be_in_viewport()
                page.locator("#check-admin-token").click()
                expect(page.locator("#admin-credential-state")).to_contain_text("Accepted")
                page.locator("#close-credentials").click()
                assert not errors, errors
                print("Wi-Fi recovery browser acceptance passed.")
            finally:
                context.tracing.stop(path="test-results/wifi-pi-browser.zip")
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
