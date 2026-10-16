"""A protected device opens the dashboard with the token alone and sees why it is allowed."""

import json
import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright

PROTECTED_MAC = "02:00:00:00:00:01"


def check_token(page, token):
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill(token)
    page.locator("#check-admin-token").click()


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
                # A separate saved grant adds a second reason; removing it keeps protection access.
                page.locator('[data-page-link="access"]').click()
                expect(page.locator("#dashboard-access-panel")).to_be_visible()
                page.locator("#dashboard-access-name").fill("Owner phone")
                page.locator("#allow-dashboard-device").click()
                expect(status).to_contain_text("a saved grant and from protection")
                expect(listing).to_contain_text("saved grant and protection")
                page.once("dialog", lambda dialog: dialog.accept())
                listing.get_by_role("button", name="Remove saved grant").click()
                expect(status).to_contain_text("comes from protecting this device")
                expect(page.locator("#private-dashboard")).to_be_visible()
                # A failed sync is visible with a retry, never shown as working access.
                def failed(route):
                    try:
                        response = route.fetch()
                        data = response.json()
                        data["protected_access"] = {
                            "synced": False, "protected": 1, "granted": 0,
                            "not_eligible": [], "error": "Helper unreachable."}
                        route.fulfill(response=response, body=json.dumps(data))
                    except PlaywrightError:
                        pass  # A list refresh still in flight when the route is removed.
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
                dialogs = []

                def keep(dialog):
                    dialogs.append(dialog.message)
                    dialog.accept()

                page.on("dialog", keep)
                page.evaluate("""() => request("/api/unaffected-devices/delete", "POST",
                                                {mac_address: "%s"})""" % PROTECTED_MAC)
                page.remove_listener("dialog", keep)
                assert dialogs and "only because it is protected" in dialogs[0], dialogs
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
