"""Save a forced window and watch it switch the router's Wi-Fi off and back on.

The fixture's router is simulated. A window that covers the whole week is running at any time so
the journey does not wait for a clock. The service ticks twice a second in the fixture.
"""

import os
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.apply_clarity import start_fixture
from tests.browser.phone_layout import sign_in

HEADERS = {"Authorization": "Bearer browser-test"}
WINDOWS = ((1280, 800), (390, 844))
WHOLE_WEEK = {"id": "all-week", "start_day": 0, "start_minute": 0, "end_day": 6, "end_minute": 1439,
              "enabled": True}
NOTICE = "The router's Wi-Fi is off until Sunday 23:59."


def router_enabled(page, base):
    """Whether the simulated router's Wi-Fi is on. The fixture answers without the cache."""

    response = page.request.get(base + "/fixture/router-wifi")
    assert response.ok, response.text()
    return response.json()["enabled"]


def refresh_until(page, check, what, seconds=20):
    """Ask the card to reload until the check holds. The page polls every five seconds."""

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        page.evaluate("loadForcedWindows()")
        if check():
            return
        page.wait_for_timeout(300)
    raise AssertionError(f"timed out waiting for: {what}")


def fill_window(page):
    page.locator("#forced-window-id").fill("all-week")
    page.locator("#forced-window-start-day").select_option("0")
    page.locator("#forced-window-start-time").fill("00:00")
    page.locator("#forced-window-end-day").select_option("6")
    page.locator("#forced-window-end-time").fill("23:59")
    page.locator("#forced-window-save").click()


def check_window(page, base, width, height):
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate("location.hash = 'schedules'")
    refresh_until(page, lambda: page.locator("#forced-window-card").is_visible(), "the card")
    expect(page.locator("#forced-window-card")).to_be_visible()
    expect(page.locator("#forced-window-status")).to_contain_text("No window is running.")
    expect(page.locator("#forced-window-login-status")).to_contain_text("No router login is saved")
    expect(page.locator("#forced-window-login-remove")).to_be_disabled()

    # A window without a saved login waits and says why.
    fill_window(page)
    expect(page.locator("#forced-window-list")).to_contain_text("all-week: Monday 00:00 to Sunday 23:59")
    refresh_until(
        page, lambda: "No router login is saved" in page.locator("#forced-window-status").inner_text(),
        "the missing login reason",
    )
    assert router_enabled(page, base) is True, "the router's Wi-Fi must stay on without a login"
    page.screenshot(path=f"test-results/forced-window-{width}-waiting.png")

    # The login goes to the Pi once and the field is empty again.
    page.locator("#forced-window-password").fill("router-secret")
    page.locator("#forced-window-login-save").click()
    expect(page.locator("#forced-window-password")).to_have_value("")
    expect(page.locator("#forced-window-login-status")).to_contain_text("A router login is saved on the Pi")
    assert "router-secret" not in page.content()

    # The window starts: the router's Wi-Fi goes off and every page says so.
    refresh_until(page, lambda: NOTICE in page.locator("#forced-window-banner").inner_text(), "the notice")
    expect(page.locator("#forced-window-banner")).to_be_visible()
    assert router_enabled(page, base) is False
    page.evaluate("location.hash = 'devices'")
    expect(page.locator("#forced-window-banner")).to_be_visible()
    page.screenshot(path=f"test-results/forced-window-{width}-running.png")

    # End window now brings the router's Wi-Fi back and the window does not start again.
    page.evaluate("location.hash = 'schedules'")
    page.locator("#forced-window-end").click()
    page.locator("dialog button", has_text="Continue").click()
    refresh_until(page, lambda: page.locator("#forced-window-banner").is_hidden(), "the notice to go")
    refresh_until(page, lambda: router_enabled(page, base) is True, "the router's Wi-Fi to come back")
    page.wait_for_timeout(2000)
    page.evaluate("loadForcedWindows()")
    expect(page.locator("#forced-window-banner")).to_be_hidden()
    assert router_enabled(page, base) is True

    # Delete the window.
    page.locator('button[aria-label="Delete all-week"]').click()
    page.locator("dialog button", has_text="Continue").click()
    expect(page.locator("#forced-window-list")).to_contain_text("No windows saved yet.")

    # Removing the login leaves the Pi without one.
    page.locator("#forced-window-login-remove").click()
    expect(page.locator("#forced-window-login-status")).to_contain_text("No router login is saved")
    expect(page.locator("#forced-window-login-remove")).to_be_disabled()


def check_panic(page, base):
    """Panic ends a running window and the router's Wi-Fi comes back."""

    page.evaluate("location.hash = 'schedules'")
    assert page.request.post(base + "/api/forced-windows/login", headers=HEADERS,
                             data={"admin_password": "router-secret"}).ok
    assert page.request.post(base + "/api/forced-windows", headers=HEADERS, data=WHOLE_WEEK).ok
    refresh_until(page, lambda: NOTICE in page.locator("#forced-window-banner").inner_text(), "the notice")
    assert router_enabled(page, base) is False
    page.locator("#panic-button").click()
    refresh_until(page, lambda: page.locator("#forced-window-banner").is_hidden(), "Panic to end the window")
    refresh_until(page, lambda: router_enabled(page, base) is True, "the router's Wi-Fi after Panic")
    assert page.request.post(base + "/api/forced-windows/delete", headers=HEADERS,
                             data={"id": "all-week"}).ok


def run():
    process, port = start_fixture()
    base = f"http://127.0.0.1:{port}"
    try:
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                sign_in(page, base)
                for width, height in WINDOWS:
                    check_window(page, base, width, height)
                check_panic(page, base)
                assert not errors, errors
                context.close()
            finally:
                browser.close()
        print("Forced window acceptance passed at desktop and phone widths.")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
