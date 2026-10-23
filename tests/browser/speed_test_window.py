"""Check the device speed test window at desktop and phone widths.

The fixture's fake kernel moves 250 kbit/s through every project queue, so a profile without
limits shows "No speed limit set" and a profile capped at 250 kbit/s shows "Matches the cap".
The fixture's prober answers every echo with 12 ms and sends no packet.
"""

import os
import re
import selectors
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

MAC_ADDRESS = "02:00:00:00:00:01"
IP_ADDRESS = "192.168.50.184"
NAME = "Fixture phone"
HEADERS = {"Authorization": "Bearer browser-test"}
LIMIT = 20000
LIVE = re.compile(r"Now: download 2\d\d kbit/s, upload 2\d\d kbit/s")
PEAK = re.compile(r"Peak: download [23]\d\d kbit/s, upload [23]\d\d kbit/s")
# The Pi judges a speed only after it counted at least three busy seconds (and enough bytes).
# The test waits for five, however slowly the machine runs.
BUSY_SECONDS = 5


def start_fixture(wifi_peer=False):
    environment = os.environ.copy()
    environment["JITTERSIM_BROWSER_TEST_SCHEDULER"] = "1"
    # The fixture then sees every browser request as coming from the phone on the test Wi-Fi.
    environment["JITTERSIM_BROWSER_TEST_WIFI_PEER"] = "1" if wifi_peer else "0"
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True,
        env=environment,
    )
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=15):
            process.terminate()
            raise RuntimeError("Speed test dashboard fixture did not start")
        return process, int(process.stdout.readline().strip())


def sign_in(page, base):
    page.goto(base)
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill("browser-test")
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    page.locator("#remember-admin-token").check()
    page.locator("#close-credentials").click()
    expect(page.locator("#message")).to_contain_text(
        "Token accepted. Diagnostics loaded.", timeout=LIMIT
    )


def wait_for_busy_seconds(page, base, minimum):
    """Wait until the Pi has counted `minimum` busy seconds of the running test.

    The fixture's fake kernel moves traffic through every queue each second, so each sample after
    the first closes one busy second. Counting samples does not depend on how fast the machine is.
    """

    deadline = time.monotonic() + 2 * LIMIT / 1000
    counted = 0
    while time.monotonic() < deadline:
        active = page.request.get(base + "/api/speed-tests?mac=" + MAC_ADDRESS,
                                  headers=HEADERS).json()["active"]
        counted = max(0, active[0]["samples"] - 1) if active else 0
        if counted >= minimum:
            return
        page.wait_for_timeout(500)
    raise AssertionError(f"the Pi counted {counted} busy seconds, wanted {minimum}")


def check_width(browser, width, height):
    process, port = start_fixture()
    try:
        base = f"http://127.0.0.1:{port}"
        context = browser.new_context(viewport={"width": width, "height": height},
                                      accept_downloads=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        # The page asks in its own dialog. A browser question would be a regression.
        native_questions = []
        page.on("dialog", lambda question: (native_questions.append(question.message),
                                            question.dismiss()))

        def post(path, data):
            response = page.request.post(base + path, headers=HEADERS, data=data)
            assert response.ok, (path, response.status, response.text())
            return response.json()

        def apply(profile_id):
            post("/api/connected-devices/apply-profile",
                 {"ipv4_address": IP_ADDRESS, "mac_address": MAC_ADDRESS, "profile_id": profile_id})
            post("/api/apply", {})

        post("/api/profiles", {"id": "nolimits", "name": "No limits"})
        post("/api/profiles", {"id": "capped", "name": "Capped",
                               "download": {"bandwidth_kbit": 250},
                               "upload": {"bandwidth_kbit": 250}})
        post("/api/connected-device-labels",
             {"mac_address": MAC_ADDRESS, "display_name": NAME, "device_type": "phone"})
        apply("nolimits")
        sign_in(page, base)
        page.locator('[data-page-link="devices"]').first.click()
        row = page.locator(f'.connected-row[data-mac-address="{MAC_ADDRESS}"]')
        open_button = row.get_by_role("button", name=f"Test speed of {NAME}")
        expect(open_button).to_be_enabled(timeout=LIMIT)
        open_button.click()

        dialog = page.locator("#speed-test-dialog")
        expect(dialog).to_be_visible()
        expect(page.locator("#speed-test-title")).to_have_text(f"Speed test: {NAME}")
        expect(page.locator("#speed-test-provider")).to_have_value("cloudflare")
        expect(page.locator("#speed-test-history")).to_contain_text("No speed tests saved")

        def measure(label, busy_seconds=BUSY_SECONDS):
            page.locator("#speed-test-duration").fill("30")
            page.locator("#speed-test-label").fill(label)
            page.locator("#speed-test-begin").click()
            link = page.locator("#speed-test-link")
            expect(link).to_have_attribute("href", "https://speed.cloudflare.com/")
            expect(link).to_have_attribute("rel", "noopener noreferrer")
            expect(page.locator("svg.speed-test-qr")).to_have_attribute(
                "aria-label", "QR code for https://speed.cloudflare.com/")
            expect(page.locator("#speed-test-progress")).to_contain_text("Measuring:")
            expect(page.locator("#speed-test-source")).to_contain_text("This device's own queues")
            expect(page.locator("#speed-test-live")).to_contain_text(LIVE, timeout=LIMIT)
            wait_for_busy_seconds(page, base, busy_seconds)
            expect(page.locator("#speed-test-live")).to_contain_text(PEAK)
            assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
            dialog.screenshot(path=f"test-results/device-speed-test-running-{width}.png")
            page.locator("#speed-test-finish").click()
            expect(page.locator("#speed-test-announce")).to_have_text(
                "Speed test saved.", timeout=LIMIT)
            expect(page.locator("#speed-test-start")).to_be_visible()

        # A length that is not a whole number is refused in the window with a message. It is not
        # swapped for the default, and the page's toast (under the window) stays unused.
        alert = dialog.locator("#speed-test-alert")
        expect(alert).to_have_attribute("role", "alert")
        expect(alert).to_be_hidden()
        page.locator("#speed-test-duration").fill("45.5")
        page.locator("#speed-test-begin").click()
        expect(alert).to_have_text("Length must be a whole number of seconds from 15 to 90.")
        expect(alert).to_be_visible()
        expect(page.locator("#speed-test-duration")).to_be_focused()
        expect(page.locator("#speed-test-start")).to_be_visible()
        expect(page.locator("#toast-message")).not_to_contain_text("Length must")
        expect(page.locator("#action-alert")).not_to_contain_text("Length must")
        active = page.request.get(base + "/api/speed-tests?mac=" + MAC_ADDRESS,
                                  headers=HEADERS).json()["active"]
        assert not active, active

        # a baseline without limits, with the numbers the device's page showed typed in
        measure("Before")
        expect(alert).to_have_text("")
        history = page.locator("#speed-test-history tbody tr")
        expect(history).to_have_count(1)
        expect(history.first).to_contain_text("No limits")
        expect(history.first).to_contain_text("No speed limit set")
        expect(history.first).to_contain_text("12 ms")
        expect(page.locator("#speed-test-editor")).to_be_visible()
        page.locator("#speed-test-reported-download-mbit").fill("48.5")
        page.locator("#speed-test-reported-latency-ms").fill("9")
        page.locator("#speed-test-save-reported").click()
        expect(page.locator("#speed-test-announce")).to_have_text("Result details saved.")

        # the same device after a capped profile
        apply("capped")
        measure("After")
        expect(history).to_have_count(2)
        expect(history.first).to_contain_text("Capped")
        expect(history.first).to_contain_text("Matches the cap")

        page.locator("#speed-test-compare-button").click()
        comparison = page.locator("#speed-test-comparison")
        expect(comparison).to_contain_text("Download, average while busy")
        expect(comparison).to_contain_text("Device page: download")
        expect(comparison).to_contain_text("before No speed limit set; after Matches the cap")
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
        dialog.screenshot(path=f"test-results/device-speed-test-compare-{width}.png")

        # An error shows in the open window, on its own alert line and not on the status line.
        # The page's toast would sit under the window and its regions are inert behind it.
        def refuse_compare(route):
            route.fulfill(status=400, content_type="application/json",
                          body='{"error":"Choose two saved results"}')

        page.route("**/api/speed-tests/compare*", refuse_compare)
        page.locator("#speed-test-compare-button").click()
        expect(alert).to_have_text("Choose two saved results")
        expect(alert).to_be_visible()
        expect(page.locator("#speed-test-announce")).to_have_text("")
        expect(page.locator("#toast-message")).not_to_contain_text("Choose two saved results")
        expect(page.locator("#action-alert")).not_to_contain_text("Choose two saved results")
        page.unroute("**/api/speed-tests/compare*", refuse_compare)
        page.locator("#speed-test-compare-button").click()
        expect(page.locator("#speed-test-announce")).to_have_text("Comparison ready.")
        expect(alert).to_have_text("")

        with page.expect_download() as download_info:
            page.locator("#speed-test-export-csv").click()
        exported = Path(download_info.value.path()).read_text()
        lines = exported.strip().splitlines()
        assert lines[0].startswith("id,started_at"), lines[0]
        assert len(lines) == 3, exported
        assert "browser-test" not in exported and "Bearer" not in exported

        # A failed export says why in the window (the dashboard's own error text) and adds no
        # "information is out of date" note, because a download is one click and not a poll.
        def refuse_export(route):
            route.fulfill(status=503, content_type="application/json",
                          body='{"error":"The Pi is busy. Try again."}')

        page.route("**/api/speed-tests/export*", refuse_export)
        page.locator("#speed-test-export-json").click()
        expect(alert).to_have_text("The Pi is busy. Try again.")
        expect(page.locator("#connection-notice")).to_be_hidden()
        page.unroute("**/api/speed-tests/export*", refuse_export)

        # cancelling saves nothing
        page.locator("#speed-test-label").fill("Cancelled")
        page.locator("#speed-test-begin").click()
        expect(page.locator("#speed-test-progress")).to_contain_text("Measuring:")
        page.locator("#speed-test-cancel").click()
        expect(page.locator("#speed-test-announce")).to_have_text("Cancelled. Nothing was saved.")
        expect(history).to_have_count(2)

        # a Finish that arrives after the run ended (cancelled elsewhere here) must not leave the
        # window stuck in the running view
        page.locator("#speed-test-begin").click()
        expect(page.locator("#speed-test-progress")).to_contain_text("Measuring:")
        run_id = page.request.get(base + "/api/speed-tests?mac=" + MAC_ADDRESS,
                                  headers=HEADERS).json()["active"][0]["id"]

        def cancel_first(route):
            post("/api/speed-tests/cancel", {"run_id": run_id})
            route.continue_()

        page.route("**/api/speed-tests/finish", cancel_first)
        page.locator("#speed-test-finish").click()
        expect(page.locator("#speed-test-announce")).to_have_text(
            "Cancelled. Nothing was saved.", timeout=LIMIT)
        expect(page.locator("#speed-test-start")).to_be_visible()
        expect(page.locator("#speed-test-running")).to_be_hidden()
        page.unroute("**/api/speed-tests/finish")
        expect(history).to_have_count(2)

        # a running test survives closing the window and shows again on reopening
        page.locator("#speed-test-begin").click()
        expect(page.locator("#speed-test-progress")).to_contain_text("Measuring:")
        page.locator("#speed-test-close").click()
        expect(dialog).to_be_hidden()
        open_button.click()
        expect(page.locator("#speed-test-progress")).to_contain_text("Measuring:", timeout=LIMIT)
        page.locator("#speed-test-cancel").click()
        expect(page.locator("#speed-test-announce")).to_have_text("Cancelled. Nothing was saved.")

        # deleting one result asks first, in the page's own question above the open window
        delete = history.first.get_by_role("button", name=re.compile("^Delete result"))
        question = page.locator("dialog.workflow-confirm")
        delete.click()
        expect(question).to_be_visible()
        expect(question.get_by_role("heading", name="Delete speed test result")).to_be_visible()
        expect(question).to_contain_text("This cannot be undone.")
        expect(question.get_by_role("button", name="Cancel", exact=True)).to_be_focused()
        page.keyboard.press("Escape")
        expect(question).to_have_count(0)
        expect(dialog).to_be_visible()
        expect(history).to_have_count(2)
        expect(delete).to_be_focused()
        delete.click()
        question.get_by_role("button", name="Cancel", exact=True).click()
        expect(question).to_have_count(0)
        expect(history).to_have_count(2)
        expect(delete).to_be_focused()
        delete.click()
        question.get_by_role("button", name="Delete result", exact=True).click()
        expect(question).to_have_count(0)
        expect(history).to_have_count(1)
        expect(page.locator("#speed-test-announce")).to_have_text("Result deleted.")
        expect(page.locator("#speed-test-title")).to_be_focused()

        # A result saved after its applied profile changes must be marked incomplete in the UI.
        page.locator("#speed-test-label").fill("Changed during test")
        page.locator("#speed-test-duration").fill("90")
        page.locator("#speed-test-begin").click()
        expect(page.locator("#speed-test-live")).to_contain_text(LIVE, timeout=LIMIT)
        active = page.request.get(
            base + "/api/speed-tests?mac=" + MAC_ADDRESS, headers=HEADERS
        ).json()["active"]
        assert len(active) == 1, active
        apply("nolimits")
        page.wait_for_timeout(1500)
        active = page.request.get(
            base + "/api/speed-tests?mac=" + MAC_ADDRESS, headers=HEADERS
        ).json()["active"]
        if active:
            post("/api/speed-tests/finish", {"run_id": active[0]["id"]})
        expect(page.locator("#speed-test-announce")).to_contain_text(
            "Speed test saved, but may be incomplete.", timeout=LIMIT)
        changed = page.request.get(
            base + "/api/speed-tests?mac=" + MAC_ADDRESS, headers=HEADERS
        ).json()["results"][0]
        assert changed["status"] == "changed", changed
        assert "queues changed" in changed["summary"]["ended_reason"].lower(), changed
        assert page.locator("#speed-test-announce").text_content().endswith(
            changed["summary"]["ended_reason"])
        expect(history).to_have_count(2)

        # A sampler failure is persisted as a fault and keeps its recorded reason in the message.
        page.locator("#speed-test-label").fill("Fixture fault")
        page.locator("#speed-test-duration").fill("90")
        page.locator("#speed-test-begin").click()
        expect(page.locator("#speed-test-announce")).to_have_text(
            "Measuring. Open the speed test on the device now.", timeout=LIMIT)
        active = page.request.get(
            base + "/api/speed-tests?mac=" + MAC_ADDRESS, headers=HEADERS
        ).json()["active"]
        assert len(active) == 1 and active[0]["label"] == "Fixture fault", active
        expect(page.locator("#speed-test-announce")).to_contain_text(
            "Speed test saved, but may be incomplete.", timeout=LIMIT)
        faulted = page.request.get(
            base + "/api/speed-tests?mac=" + MAC_ADDRESS, headers=HEADERS
        ).json()["results"][0]
        assert faulted["label"] == active[0]["label"], faulted
        assert faulted["status"] == "fault", faulted
        assert faulted["summary"]["ended_reason"], faulted
        assert page.locator("#speed-test-announce").text_content().endswith(
            faulted["summary"]["ended_reason"])
        expect(history).to_have_count(3)
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()

        # Locking the dashboard removes the private dialog and invalidates an in-flight poll.
        refused_status_requests = []

        def refuse_speed_status(route):
            refused_status_requests.append(route.request.url)
            route.fulfill(status=401, content_type="application/json",
                          body='{"error":"Sign in again","registration_required":true}')

        page.route("**/api/speed-tests/status*", refuse_speed_status)
        open_button.click()
        page.locator("#speed-test-duration").fill("15")
        page.locator("#speed-test-begin").click()
        expect(page.locator("#private-dashboard")).to_be_hidden(timeout=LIMIT)
        expect(page.locator("#speed-test-dialog")).to_have_count(0)
        page.wait_for_timeout(1200)
        assert len(refused_status_requests) == 1, refused_status_requests
        active = page.request.get(base + "/api/speed-tests?mac=" + MAC_ADDRESS,
                                  headers=HEADERS).json()["active"]
        assert len(active) == 1, active
        post("/api/speed-tests/cancel", {"run_id": active[0]["id"]})
        page.unroute("**/api/speed-tests/status*", refuse_speed_status)
        page.goto(base)
        expect(page.locator("#private-dashboard")).to_be_visible(timeout=LIMIT)

        # A rejected Start can lock the dashboard while its button handler is awaiting the reply.
        def refuse_speed_start(route):
            route.fulfill(status=401, content_type="application/json",
                          body='{"error":"Sign in again","registration_required":true}')

        page.route("**/api/speed-tests/start", refuse_speed_start)
        open_button.click()
        page.locator("#speed-test-begin").click()
        expect(page.locator("#private-dashboard")).to_be_hidden(timeout=LIMIT)
        expect(page.locator("#speed-test-dialog")).to_have_count(0)
        expect(page.locator("#toast-message")).not_to_have_text("Sign in again")
        page.unroute("**/api/speed-tests/start", refuse_speed_start)
        page.goto(base)
        expect(page.locator("#private-dashboard")).to_be_visible(timeout=LIMIT)

        # A refused initial load must not show a stale toast or leave focus in private UI.
        def refuse_speed_open(route):
            route.fulfill(status=401, content_type="application/json",
                          body='{"error":"Sign in again","registration_required":true}')

        page.route("**/api/speed-tests?mac=*", refuse_speed_open)
        open_button.click()
        expect(page.locator("#private-dashboard")).to_be_hidden(timeout=LIMIT)
        expect(page.locator("#speed-test-dialog")).to_have_count(0)
        expect(page.locator("#toast-message")).not_to_have_text("Sign in again")
        expect(page.locator("#open-admin-credentials")).to_be_focused()
        page.unroute("**/api/speed-tests?mac=*", refuse_speed_open)
        page.goto(base)
        expect(page.locator("#private-dashboard")).to_be_visible(timeout=LIMIT)

        # a protected device cannot be measured
        post("/api/unaffected-devices", {"mac_address": MAC_ADDRESS, "display_name": NAME})
        page.reload()
        expect(page.locator("#private-dashboard")).to_be_visible(timeout=LIMIT)
        page.locator('[data-page-link="devices"]').first.click()
        expect(row.get_by_role("button", name=f"Test speed of {NAME}")).to_be_disabled(
            timeout=LIMIT)
        expect(row).to_contain_text("Protected devices are not measured.")
        refused = page.request.post(base + "/api/speed-tests/start", headers=HEADERS,
                                    data={"mac_address": MAC_ADDRESS})
        assert refused.status == 409, refused.text()
        assert not errors, errors
        assert not native_questions, native_questions
        context.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def check_dashboard_test(browser):
    """The phone opens the dashboard itself and tests its speed without an outside site."""

    process, port = start_fixture(wifi_peer=True)
    try:
        base = f"http://127.0.0.1:{port}"
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        # Over loopback the Pi's 64 MiB per part would arrive before the browser's one-second
        # warm-up ends. A Wi-Fi-like 8 Mbit/s keeps the timing realistic.
        context.new_cdp_session(page).send("Network.emulateNetworkConditions", {
            "offline": False, "latency": 5, "downloadThroughput": 1_000_000,
            "uploadThroughput": -1})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        for path, data in (
                ("/api/dashboard-access/register-self", {"name": NAME}),
                ("/api/profiles", {"id": "capped", "name": "Capped",
                                   "download": {"bandwidth_kbit": 250}}),
                ("/api/connected-device-labels",
                 {"mac_address": MAC_ADDRESS, "display_name": NAME, "device_type": "phone"}),
                ("/api/connected-devices/apply-profile",
                 {"ipv4_address": IP_ADDRESS, "mac_address": MAC_ADDRESS, "profile_id": "capped"}),
                ("/api/apply", {})):
            response = page.request.post(base + path, headers=HEADERS, data=data)
            assert response.ok, (path, response.status, response.text())
        sign_in(page, base)
        page.locator('[data-page-link="devices"]').first.click()
        row = page.locator(f'.connected-row[data-mac-address="{MAC_ADDRESS}"]')
        row.get_by_role("button", name=f"Test speed of {NAME}").click()
        # This browser is the phone, so the dashboard test is chosen and needs no length.
        expect(page.locator("#speed-test-provider")).to_have_value("dashboard")
        expect(page.locator("#speed-test-duration-label")).to_be_hidden()
        page.locator("#speed-test-label").fill("From the dashboard")
        page.locator("#speed-test-begin").click()
        expect(page.locator("#speed-test-open")).to_contain_text("Keep this window open")
        expect(page.locator("svg.speed-test-qr")).to_have_count(0)
        expect(page.locator("#speed-test-announce")).to_have_text(
            "Speed test saved.", timeout=2 * LIMIT)
        history = page.locator("#speed-test-history tbody tr")
        expect(history).to_have_count(1)
        expect(history.first).to_contain_text("Not tested")
        expect(history.first).to_contain_text("this browser:")
        result = page.request.get(base + "/api/speed-tests?mac=" + MAC_ADDRESS,
                                  headers=HEADERS).json()["results"][0]
        assert result["provider"] == "dashboard", result
        browser_speeds = result["summary"]["browser"]
        assert browser_speeds["download_kbit"] and browser_speeds["reference_kbit"], result

        # A lost response header must end on the browser's deadline, even before a reader exists.
        stalled = []
        pattern = "**/api/speed-tests/download?run=deadline-fixture&part=shaped"

        def hold_headers(route):
            stalled.append(route)

        page.route(pattern, hold_headers)
        try:
            bounded = page.evaluate("""async () => {
              let guard;
              try {
                const result = await Promise.race([
                  speedTestPull({id: 'deadline-fixture'}, 'shaped', 0, speedTestState.serial),
                  new Promise((_, reject) => {
                    guard = window.setTimeout(() => reject(new Error('Download did not stop')), 10000);
                  })
                ]);
                return {result, released: speedTestState.streamController === null};
              } finally { window.clearTimeout(guard); }
            }""")
            assert len(stalled) == 1, stalled
            assert bounded == {"result": None, "released": True}, bounded
        finally:
            page.unroute(pattern, hold_headers)

        assert page.evaluate("document.documentElement.scrollWidth") <= 1441
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
            check_dashboard_test(browser)
            print("Device speed test acceptance passed at desktop and phone widths.")
        finally:
            browser.close()


if __name__ == "__main__":
    run()
