"""A protected device opens the dashboard with the token alone and sees why it is allowed."""

import json
import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.confirm_dialogs import answer_confirm, cancel_with_escape
from tests.browser.route_support import keep_interception_on

PROTECTED_MAC = "02:00:00:00:00:01"


def check_token(page, token):
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill(token)
    page.locator("#check-admin-token").click()


def idle(page):
    """Wait until no token check, refresh or poll is running, so that a check starts alone."""

    page.wait_for_function(
        "() => !adminTokenChecking && !dashboardRefresh && !dashboardPoll", timeout=30000)


def hold_first_diagnostics(page):
    """Hold the first read of the diagnostics, which every token check makes. Return the held
    routes and the handler to remove."""

    held = []

    def handler(route):
        held.append(route) if not held else route.continue_()

    page.route("**/api/diagnostics", handler)
    return held, handler


def wait_for_held(held, page):
    # page.wait_for_timeout lets Playwright deliver the route event. time.sleep would not.
    for _ in range(300):
        if held:
            return
        page.wait_for_timeout(50)
    raise AssertionError("the token check did not reach its diagnostics read")


def check_a_refresh_during_the_token_check_is_not_a_failed_token(page):
    """A save refreshes the page while a token check reads the diagnostics (the dashboard unlocks
    before the check ends, so that is allowed). The refresh makes that read stale. The check used
    to take the stale answer for a refusal and lock the dashboard with "Token check failed:
    undefined". It reads again instead."""

    idle(page)
    held, handler = hold_first_diagnostics(page)
    page.evaluate("() => { window.checking = checkAdminToken(); }")
    wait_for_held(held, page)
    # A refresh begins here. It invalidates the read that the check is waiting for.
    page.evaluate("() => { window.refreshing = refresh(); }")
    held[0].continue_()
    page.evaluate("() => Promise.all([window.checking, window.refreshing])")
    page.unroute("**/api/diagnostics", handler)
    expect(page.locator("#token-check-status")).to_have_text("Token accepted. Diagnostics loaded.")
    expect(page.locator("#private-dashboard")).to_be_visible()


def check_a_second_token_check_waits_for_the_first(page):
    """Allow this device, Remove and a moved address each end with a token check. A second one
    that started while the first still loaded the page cleared the flags of the first and locked
    the dashboard (the flake of this journey, a "not confirmed" alert that never came). It waits."""

    idle(page)
    reads = []
    page.on("request", lambda request: reads.append(request.url)
            if request.url.endswith("/api/dashboard-access/status") else None)
    held, handler = hold_first_diagnostics(page)
    page.evaluate("() => { window.first = checkAdminToken(); }")
    wait_for_held(held, page)
    before = len(reads)
    page.evaluate("() => { window.second = checkAdminToken(); }")
    page.wait_for_timeout(400)
    assert len(reads) == before, "the second check started while the first was still running"
    held[0].continue_()
    page.evaluate("() => Promise.all([window.first, window.second])")
    page.unroute("**/api/diagnostics", handler)
    assert len(reads) == before + 1, "the second check must run once, after the first"
    expect(page.locator("#token-check-status")).to_have_text("Token accepted. Diagnostics loaded.")
    expect(page.locator("#private-dashboard")).to_be_visible()


def run():
    environment = {**os.environ, "JITTERSIM_BROWSER_TEST_WIFI_PEER": "1",
                   "JITTERSIM_BROWSER_TEST_PROTECTED_PEER": "1"}
    process = subprocess.Popen([sys.executable, "-m", "tests.browser.fixture"],
                               stdout=subprocess.PIPE, text=True, env=environment)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Protected dashboard access fixture did not start")
            port = int(process.stdout.readline().strip())
        base = f"http://127.0.0.1:{port}"
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"))
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                page = context.new_page()
                keep_interception_on(page)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base)
                # Protection is not a sign-in: without the token nothing private is readable.
                for path in ("/api/dashboard-access/status", "/api/dashboard-access", "/api/devices"):
                    assert page.request.get(base + path).status == 401, path
                    assert page.request.get(
                        base + path, headers={"Authorization": "Bearer wrong"}).status == 401, path
                assert page.request.post(
                    base + "/api/dashboard-access/sync-protected", data="{}",
                    headers={"Content-Type": "application/json"}).status == 401
                expect(page.locator("#private-dashboard")).to_be_hidden()
                check_token(page, "wrong-token")
                expect(page.locator("#token-check-status")).to_contain_text("failed")
                expect(page.locator("#private-dashboard")).to_be_hidden()
                # The right token opens the dashboard without a separate registration step.
                page.locator("#admin-token").fill("browser-test")
                page.locator("#check-admin-token").click()
                page.locator("#close-credentials").click()
                expect(page.locator("#private-dashboard")).to_be_visible()
                status = page.locator("#dashboard-access-status")
                expect(status).to_contain_text("comes from protecting this device")
                expect(status).to_contain_text("admin token is still required")
                listing = page.locator("#dashboard-access-list")
                expect(listing).to_contain_text("Owner phone · " + PROTECTED_MAC + " · from protection")
                expect(listing).to_contain_text("Stop protecting under Protected devices")
                expect(listing.locator("button")).to_have_count(0)
                check_a_refresh_during_the_token_check_is_not_a_failed_token(page)
                check_a_second_token_check_waits_for_the_first(page)
                # A separate saved grant adds a second reason; removing it keeps protection access.
                page.locator('[data-page-link="access"]').click()
                expect(page.locator("#dashboard-access-panel")).to_be_visible()
                page.locator("#dashboard-access-name").fill("Owner phone")
                page.locator("#allow-dashboard-device").click()
                expect(status).to_contain_text("a saved grant and from protection")
                expect(listing).to_contain_text("saved grant and protection")
                remove = listing.get_by_role("button", name="Remove saved grant")
                remove.click()
                cancel_with_escape(page, remove, verb="Remove saved grant", title="Remove the saved grant?",
                                   text="It stays allowed while it is protected.")
                expect(listing).to_contain_text("saved grant and protection")
                remove.click()
                answer_confirm(page, "Remove saved grant", title="Remove the saved grant?")
                expect(status).to_contain_text("comes from protecting this device")
                expect(page.locator("#private-dashboard")).to_be_visible()
                # A failed sync is visible with a retry, never shown as working access.
                def failed(route):
                    response = route.fetch()
                    data = response.json()
                    data["protected_access"] = {
                        "synced": False, "protected": 1, "granted": 0,
                        "not_eligible": [], "error": "Helper unreachable."}
                    route.fulfill(response=response, body=json.dumps(data))
                # Every list read reports the failure until the route is removed, so a page
                # refresh cannot replace the alert before it is checked.
                page.route("**/api/dashboard-access", failed)
                page.evaluate("() => loadDashboardAccessList()")
                expect(listing.get_by_role("alert")).to_contain_text("not confirmed")
                expect(listing.get_by_role("alert")).to_contain_text("may still open the dashboard")
                listing.get_by_role("button", name="Retry protected access").click()
                expect(page.locator("#toast-message")).to_contain_text("confirmed")
                page.unroute("**/api/dashboard-access", failed)
                page.evaluate("() => loadDashboardAccessList()")
                expect(listing.get_by_role("alert")).to_have_count(0)
                page.screenshot(path="test-results/protected-dashboard-access.png")
                # Removing this device's protection asks first and can keep access.
                page.evaluate("""() => { window.stopping = request("/api/unaffected-devices/delete", "POST",
                                                {mac_address: "%s"}); }""" % PROTECTED_MAC)
                answer_confirm(page, "Keep access", title="Keep dashboard access?",
                               text="only because it is protected")
                page.evaluate("() => window.stopping")
                page.evaluate("() => checkAdminToken()")
                expect(status).to_contain_text("comes from a saved grant")
                expect(page.locator("#private-dashboard")).to_be_visible()
                assert not errors, errors
                context.close()
                print("Protected dashboard access browser acceptance passed.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
