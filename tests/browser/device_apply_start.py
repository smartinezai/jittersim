"""Apply a profile to one connected device after a Reset: only that device starts.

Reproduces the reported sequence through the served dashboard and a real helper
with simulated commands: low saved for Everyone, Reset, then Apply slow-test to
the connected client. The client gets slow-test at once while Everyone and the
other saved device stay paused. Start then resumes every saved setting.
"""

import os
import re
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.phone import close_controls, open_status, sidebar_control
from tests.browser.protected_clients import IP_ADDRESS, MAC_ADDRESS, controls, sign_in

HEADERS = {"Authorization": "Bearer browser-test"}
CLIENT_ID = "client-" + MAC_ADDRESS.replace(":", "")
ONLY_ONE = "Running for 1 device only. Everyone and other saved settings stay paused. Start resumes them all."


def start_fixture():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "JITTERSIM_BROWSER_TEST_SCHEDULER": "1"},
    )
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=15):
            process.terminate()
            raise RuntimeError("Device apply dashboard fixture did not start")
        return process, int(process.stdout.readline().strip())


def check_width(browser, base, width, height):
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    request = page.request

    def post(path, data, ok=True):
        response = request.post(base + path, headers=HEADERS, data=data)
        if ok:
            assert response.ok, (path, response.status, response.text())
        return response

    def get(path):
        response = request.get(base + path, headers=HEADERS)
        assert response.ok, (path, response.status)
        return response.json()

    # A clean start whatever an earlier width left behind.
    post("/api/devices/delete", {"id": CLIENT_ID}, ok=False)
    post("/api/profiles", {"id": "low", "name": "daily climb manual",
                           "upload": {"delay_ms": 40}, "download": {"delay_ms": 40}})
    post("/api/profiles", {"id": "slow-test", "name": "Slow test",
                           "upload": {"bandwidth_kbit": 500}, "download": {"bandwidth_kbit": 800}})
    post("/api/devices", {"id": "tv", "name": "Living room TV", "ipv4_address": "192.168.50.200",
                          "profile_id": "low"})
    sign_in(page, base)

    # Everyone runs low, so a device of its own is refused before anything is saved.
    post("/api/general-profile", {"profile_id": "low"})
    page.reload()
    row = page.locator(f'.connected-row[data-mac-address="{MAC_ADDRESS}"]')
    expect(row).to_be_visible(timeout=20000)
    controls(row)["profile"].select_option("slow-test")
    controls(row)["apply"].click()
    expect(page.locator("#message")).to_contain_text(
        "Everyone uses daily climb manual right now", timeout=20000)
    assert all(device["id"] != CLIENT_ID for device in get("/api/devices")["devices"])

    # The reported sequence: Reset with low saved for Everyone, then Apply slow-test.
    post("/api/reset", {})
    page.reload()
    row = page.locator(f'.connected-row[data-mac-address="{MAC_ADDRESS}"]')
    expect(row).to_be_visible(timeout=20000)
    expect(page.locator("#runtime-summary")).to_have_text("Off. No traffic shaping is running.",
                                                          timeout=20000)
    controls(row)["profile"].select_option("slow-test")
    controls(row)["apply"].click()
    expect(page.locator("#message")).to_contain_text("Started for", timeout=20000)
    expect(page.locator("#message")).to_contain_text("Everyone and other saved settings stay paused")
    expect(page.locator("#runtime-summary")).to_have_text(ONLY_ONE, timeout=20000)
    open_status(page)
    expect(page.locator("#status-general")).to_have_text("Everyone: daily climb manual · paused")

    state = get("/api/state")["state"]
    assert state["device_only"] == [CLIENT_ID], state
    assert state["last_action"] == "reset", state
    conditions = get("/api/conditions")
    assert conditions["applied"] is True, conditions
    assert set(conditions["devices"]) == {CLIENT_ID}, conditions
    assert conditions["everyone_else"] is None, conditions
    saved = {device["id"]: device for device in get("/api/devices")["devices"]}
    assert saved[CLIENT_ID]["profile_id"] == "slow-test"
    assert saved[CLIENT_ID]["ipv4_address"] == IP_ADDRESS
    assert get("/api/general-profile")["profile"]["id"] == "low"
    page.screenshot(path=f"test-results/device-apply-start-{width}.png")
    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1

    # Start is the explicit whole-scope action: Everyone resumes for every device.
    close_controls(page)
    sidebar_control(page, "#apply-button").click()
    expect(page.locator("#runtime-summary")).not_to_have_text(ONLY_ONE, timeout=20000)
    expect(page.locator("#runtime-summary")).to_have_text(re.compile("^Running"), timeout=20000)
    assert get("/api/state")["state"]["device_only"] == []
    assert get("/api/general-profile")["active"] is True

    # Leave the fixture stopped with nothing for Everyone for the next width.
    post("/api/general-profile/stop", {})
    post("/api/reset", {})
    assert not errors, errors
    context.close()


def run():
    process, port = start_fixture()
    try:
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                for width, height in ((1920, 1000), (390, 844)):
                    check_width(browser, f"http://127.0.0.1:{port}", width, height)
                print("Device apply-and-start acceptance passed at desktop and phone widths.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
