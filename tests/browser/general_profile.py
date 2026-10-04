"""Apply, pause and stop a general profile through the served dashboard."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.phone import sidebar_control


def wait_for_automatic_token_check(page):
    """Wait until the remembered token check has finished its refresh.

    That check ends with a status message. If a later action message is shown
    before it ends the late message overwrites the one a test is waiting for.
    """

    # The check ends after many requests to a dashboard that answers one at a time.
    # A slow shared CI machine needs more than 5 seconds (like apply_clarity).
    expect(page.locator("#message")).to_contain_text(
        "Token accepted. Diagnostics loaded.", timeout=20000
    )


def run():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("General profile dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
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
                    base = f"http://127.0.0.1:{port}"
                    headers = {"Authorization": "Bearer browser-test"}
                    assert page.request.post(
                        base + "/api/general-profile/stop", headers=headers, data={}
                    ).ok
                    page.goto(base)
                    expect(page.locator("#private-dashboard")).to_be_hidden()
                    page.locator("#open-admin-credentials").click()
                    page.locator("#admin-token").fill("browser-test")
                    page.locator("#check-admin-token").click()
                    expect(page.locator("#private-dashboard")).to_be_visible()
                    page.locator("#close-credentials").click()
                    assert page.request.post(
                        base + "/api/profiles",
                        headers=headers,
                        data={
                            "id": "slow",
                            "name": "Shared slow network",
                            "upload": {"bandwidth_kbit": 1000},
                            "download": {"bandwidth_kbit": 2000},
                        },
                    ).ok
                    sidebar_control(page, "#open-admin-credentials").click()
                    page.locator("#remember-admin-token").check()
                    # A reload before the token is stored would forget it.
                    expect(page.locator("#remember-admin-token-note")).to_contain_text(
                        "Remembered until", timeout=20000
                    )
                    page.locator("#close-credentials").click()
                    page.reload()
                    wait_for_automatic_token_check(page)
                    expect(page.locator("#general-profile-select")).to_be_enabled()
                    page.locator("#general-profile-select").select_option("slow")
                    page.locator("#general-profile-apply").click()
                    expect(page.locator("#general-profile-status")).to_contain_text(
                        "Active: Shared slow network"
                    )
                    expect(page.locator("#general-profile-note")).to_contain_text(
                        "Protected devices bypass it completely"
                    )
                    expect(page.locator("#general-profile-stop")).to_be_enabled()
                    assert (
                        page.request.get(base + "/api/devices", headers=headers).json()["devices"]
                        == []
                    )
                    page.reload()
                    wait_for_automatic_token_check(page)
                    expect(page.locator("#general-profile-status")).to_contain_text("Active:")
                    page.locator("section[aria-labelledby='general-profile-title']").screenshot(
                        path=f"test-results/general-profile-{width}.png"
                    )
                    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
                    page.locator("#panic-button").click()
                    expect(page.locator("#general-profile-status")).to_contain_text("Paused:")
                    page.reload()
                    wait_for_automatic_token_check(page)
                    expect(page.locator("#general-profile-status")).to_contain_text("Paused:")
                    page.locator("#general-profile-apply").click()
                    expect(page.locator("#general-profile-status")).to_contain_text("Active:")
                    page.route(
                        "**/api/general-profile/stop",
                        lambda route: route.fulfill(
                            status=502,
                            content_type="application/json",
                            body='{"error":"Fixture network update failed"}',
                        ),
                    )
                    page.locator("#general-profile-stop").click()
                    expect(page.locator("#message")).to_contain_text(
                        "Fixture network update failed"
                    )
                    expect(page.locator("#general-profile-stop")).to_be_enabled()
                    expect(page.locator("#general-profile-status")).to_contain_text("Active:")
                    page.unroute("**/api/general-profile/stop")
                    page.locator("#general-profile-stop").click()
                    expect(page.locator("#general-profile-status")).to_contain_text(
                        "Nothing set for everyone"
                    )
                    expect(page.locator("#general-profile-stop")).to_be_disabled()
                    assert not errors, errors
                    context.close()
                print("General profile dashboard acceptance passed at desktop and phone widths.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
