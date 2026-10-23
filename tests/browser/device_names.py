"""Check that the pages from main show device names and not the "Client <ip>" placeholder.

The fixture's one connected client has the MAC address 02:00:00:00:00:01 and the address
192.168.50.184. It is saved as a device with the placeholder name and gets a saved label.
A second saved device has no MAC address and no name so it is an unknown device.
"""

import os
import re
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.confirm_dialogs import cancel_with_escape

MAC_ADDRESS = "02:00:00:00:00:01"
NAME = "Fixture laptop"
UNKNOWN = "Unknown device (192.168.50.77)"
HEADERS = {"Authorization": "Bearer browser-test"}
PLACEHOLDER = re.compile(r"Client 192\.168\.50\.\d+")
LIMIT = 15000


def start_fixture():
    # a scheduler like a real install so that scenarios and timers run and are logged
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
            raise RuntimeError("Device names dashboard fixture did not start")
        return process, int(process.stdout.readline().strip())


def check_width(browser, width, height):
    process, port = start_fixture()
    try:
        base = f"http://127.0.0.1:{port}"
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def post(path, data):
            response = page.request.post(base + path, headers=HEADERS, data=data)
            assert response.ok, (path, response.status, response.text())
            return response.json()

        def get(path):
            response = page.request.get(base + path, headers=HEADERS)
            assert response.ok, (path, response.status, response.text())
            return response.json()

        def open_page(name):
            page.locator(f'a[data-page-link="{name}"]').first.click()
            panel = page.locator(f"#page-{name}")
            expect(panel).to_be_visible()
            return panel

        post("/api/profiles", {"id": "slow", "name": "Slow office",
                               "download": {"delay_ms": 300}})
        post("/api/connected-device-labels",
             {"mac_address": MAC_ADDRESS, "display_name": NAME, "device_type": "laptop"})
        # the placeholder a client saved from the connected list gets
        post("/api/devices", {"id": "laptop", "name": "Client 192.168.50.184",
                              "ipv4_address": "192.168.50.184", "profile_id": "slow",
                              "mac_address": MAC_ADDRESS})
        post("/api/devices", {"id": "spare", "name": "Client 192.168.50.77",
                              "ipv4_address": "192.168.50.77", "profile_id": "slow"})
        post("/api/schedules", {"id": "evening", "device_id": "laptop", "profile_id": "slow",
                                "days_of_week": [0, 1, 2, 3, 4, 5, 6], "start_minute": 0,
                                "end_minute": 1439, "enabled": False})
        post("/api/scenarios", {"id": "commute", "name": "Commute", "steps": [
            {"profile_id": "slow", "duration_seconds": 600}]})

        page.goto(base)
        page.locator("#open-admin-credentials").click()
        page.locator("#admin-token").fill("browser-test")
        page.locator("#check-admin-token").click()
        expect(page.locator("#private-dashboard")).to_be_visible()
        page.locator("#remember-admin-token").check()
        page.locator("#close-credentials").click()

        post("/api/apply", {})
        post("/api/scenarios/start", {"scenario_id": "commute", "device_id": "laptop"})
        post("/api/timers", {"device_id": "spare", "profile_id": "slow", "duration_minutes": 5})
        version = get("/api/access")["version"]
        post("/api/site-blocks", {"version": version, "rule": {
            "id": "block-video", "name": "Video", "enabled": True, "domains": ["example.com"],
            "device_id": "laptop", "group_id": None}})
        post("/api/test-sessions/start", {"name": "Laptop run", "target": "device:laptop"})
        page.reload()
        expect(page.locator("#message")).to_contain_text("Token accepted", timeout=LIMIT)

        # Devices: the table on a wide screen and the cards on a phone, then the device controls
        devices_page = open_page("devices")
        if width > 600:
            first = page.locator("#device-table tbody tr").first
            expect(first.locator("td").first).to_contain_text(NAME)
            first.get_by_role("button", name="Open controls").click()
        else:
            card = page.locator("#device-cards .device-card").first
            expect(card.locator("h3")).to_have_text(NAME)
            expect(card.locator("p").first).to_have_text("192.168.50.184")
            expect(page.locator("#device-cards").get_by_role(
                "button", name="Open " + UNKNOWN)).to_be_visible()
            card.get_by_role("button", name="Open " + NAME).click()
        expect(page.locator("#workspace-title")).to_have_text(NAME)
        expect(page.locator("#workspace-address")).to_contain_text("192.168.50.184")
        expect(devices_page).not_to_contain_text(PLACEHOLDER)
        # This browser is on loopback, so the Pi names no Wi-Fi device for it: no row says so.
        expect(page.locator("#connected-devices .connected-row")).to_have_count(1)
        expect(page.locator("#connected-devices")).not_to_contain_text("This device")

        # Schedules and limits: the schedule, the running scenario and the timer
        schedules = open_page("schedules")
        for title in ("Run a profile timer", "Scenarios"):
            heading = schedules.locator("h2", has_text=title)
            card = heading.locator("xpath=ancestor::section[1]")
            if card.get_attribute("data-collapse") == "closed":
                heading.click()
        expect(page.locator("#schedule-list")).to_contain_text(NAME + " → Slow office")
        expect(page.locator("#scenario-run-list")).to_contain_text(NAME + " · Commute",
                                                                   timeout=LIMIT)
        expect(page.locator("#timer-list")).to_contain_text(UNKNOWN + " → Slow office")
        expect(schedules).not_to_contain_text(PLACEHOLDER)

        # Access and rules: the site rule picker and owner and the device access rows
        access = open_page("access")
        expect(page.locator("#site-rule-target")).to_contain_text(NAME + " (192.168.50.184)")
        expect(page.locator("#site-rule-list")).to_contain_text(NAME + " · Enabled")
        expect(page.locator("#access-device-list")).to_contain_text(NAME)
        expect(page.locator("#access-device-list").get_by_role(
            "button", name="Block " + NAME)).to_be_visible()
        expect(access).not_to_contain_text(PLACEHOLDER)
        expect(page.locator("#site-rule-target")).not_to_contain_text(PLACEHOLDER)
        block = page.locator("#access-device-list").get_by_role("button", name="Block " + NAME)
        block.click()
        cancel_with_escape(page, block, verb="Continue", text="Block network access for " + NAME + "?",
                           absent="device you are using")

        # Activity: the scheduler's events and their Open buttons
        activity = open_page("activity")
        expect(page.locator("#activity-list")).to_contain_text(
            NAME + ": scenario Commute", timeout=LIMIT)
        expect(page.locator("#activity-list")).to_contain_text(UNKNOWN + ": timer started")
        expect(page.locator("#activity-list").get_by_role(
            "button", name="Open " + NAME)).to_be_visible()
        expect(activity).not_to_contain_text(PLACEHOLDER)

        # Measurements: the test session says which device it tests
        measurements = open_page("measurements")
        page.locator("#test-session-list").select_option(label="Laptop run · Running")
        expect(page.locator("#test-session-summary")).to_contain_text(
            "Target: " + NAME + " (192.168.50.184)")
        expect(page.locator("#test-session-target")).to_contain_text(NAME + " (192.168.50.184)")
        expect(measurements).not_to_contain_text(PLACEHOLDER)
        session_id = get("/api/test-sessions")["sessions"][0]["id"]
        report = get("/api/test-sessions/" + session_id)["report"]
        assert "\nTarget: " + NAME + " (192.168.50.184)\n" in report, report[:200]

        assert not errors, errors
        context.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def run():
    Path("test-results").mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
        )
        try:
            for width, height in ((1440, 1000), (390, 844)):
                check_width(browser, width, height)
            print("Device names acceptance passed at desktop and phone widths.")
        finally:
            browser.close()


if __name__ == "__main__":
    run()
