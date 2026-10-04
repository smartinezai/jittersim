"""Credential changes cannot retain or restore private device address details."""

import json
import os
import selectors
import subprocess
import sys

from playwright.sync_api import expect, sync_playwright

from tests.browser.phone import sidebar_control


def run():
    environment = {
        **os.environ,
        "JITTERSIM_BROWSER_TEST_WIFI_PEER": "1",
        "JITTERSIM_BROWSER_TEST_OLD_ADDRESS": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"],
        stdout=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Address privacy fixture did not start")
            port = int(process.stdout.readline().strip())
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                for width, height in ((1920, 1000), (390, 844)):
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{port}")
                    sidebar_control(page, "#open-admin-credentials").click()
                    token = page.locator("#admin-token")

                    registered = False

                    def sign_in():
                        token.fill("browser-test")
                        page.locator("#check-admin-token").click()
                        expect(page.locator("#token-check-status")).to_contain_text(
                            "Diagnostics loaded" if registered else "Allow this device"
                        )
                        expect(page.locator("#address-move-card")).to_be_visible()

                    def cleared():
                        for name in (
                            "address-move-card",
                            "address-type-note",
                            "address-move-replaced",
                            "address-help",
                        ):
                            expect(page.locator("#" + name)).to_be_hidden()
                        assert "Old Mac" not in page.locator("#address-move-picker").text_content()
                        assert "02:00:00" not in page.locator("#address-type-note").text_content()

                    sign_in()
                    page.locator("#clear-admin-token").click()
                    cleared()
                    sign_in()
                    token.fill("wrong-token")
                    cleared()
                    page.locator("#check-admin-token").click()
                    expect(page.locator("#token-check-status")).to_contain_text("failed")
                    cleared()
                    sign_in()

                    page.locator("#close-credentials").click()
                    page.locator("#allow-dashboard-device").click()
                    expect(page.locator("#private-dashboard")).to_be_visible()
                    page.wait_for_function("() => adminTokenVerified && !adminTokenChecking")
                    registered = True
                    sidebar_control(page, "#open-admin-credentials").click()
                    # An obsolete error must not hide a newly accepted offer.
                    held = []

                    def hold_first(route):
                        if not held:
                            held.append(route)
                        else:
                            route.continue_()

                    page.route("**/api/dashboard-access/moves", hold_first)
                    page.evaluate("() => { void loadAddressMoves(); }")
                    page.wait_for_function("() => adminTokenChecking === false")
                    # Pump browser events until the old request is held.
                    for _ in range(50):
                        if held:
                            break
                        page.wait_for_timeout(20)
                    assert held
                    token.fill("another-token")
                    sign_in()
                    held[0].fulfill(
                        status=503,
                        content_type="application/json",
                        body=json.dumps({"error": "obsolete failure"}),
                    )
                    page.wait_for_timeout(100)
                    expect(page.locator("#address-move-card")).to_be_visible()
                    page.unroute("**/api/dashboard-access/moves")

                    # A late success must not restore a signed-out device's details.
                    held.clear()
                    page.route("**/api/dashboard-access/moves", lambda route: held.append(route))
                    page.evaluate("() => { void loadAddressMoves(); }")
                    for _ in range(50):
                        if held:
                            break
                        page.wait_for_timeout(20)
                    assert held
                    page.locator("#clear-admin-token").click()
                    held[0].fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "offer": True,
                                "current": {
                                    "mac_address": "02:00:00:00:00:01",
                                    "private_address": True,
                                    "registered": False,
                                    "protected": False,
                                },
                                "candidates": [
                                    {
                                        "mac_address": "02:00:00:00:00:99",
                                        "name": "Old Mac",
                                        "registered": True,
                                        "protected": True,
                                    }
                                ],
                                "replaced": [],
                            }
                        ),
                    )
                    page.wait_for_timeout(100)
                    cleared()
                    page.unroute("**/api/dashboard-access/moves")
                    sign_in()
                    page.locator("#close-credentials").click()
                    # A completed old write must not show private text or recheck new credentials.
                    held.clear()
                    page.route("**/api/dashboard-access/move", lambda route: held.append(route))
                    page.locator("#address-move-confirm").click()
                    for _ in range(50):
                        if held:
                            break
                        page.wait_for_timeout(20)
                    assert len(held) == 1
                    sidebar_control(page, "#open-admin-credentials").click()
                    page.locator("#clear-admin-token").click()
                    held[0].fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps({"saved": True, "allowed": True}),
                    )
                    page.wait_for_timeout(100)
                    cleared()
                    expect(page.locator("#token-check-status")).to_contain_text("Token changed")
                    assert "Old Mac" not in page.locator("#message").text_content()
                    assert "Old Mac" not in page.locator("#toast-message").text_content()
                    assert len(held) == 1
                    assert not errors, errors
                    response = context.request.post(
                        f"http://127.0.0.1:{port}/api/dashboard-access/remove",
                        headers={"Authorization": "Bearer browser-test"},
                        data={"mac_address": "02:00:00:00:00:01"},
                    )
                    assert response.ok
                    context.close()
                print("Address privacy acceptance passed on desktop and phone.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
