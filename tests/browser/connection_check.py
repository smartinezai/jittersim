"""Check the connection check window at desktop and phone widths.

The fixture's prober answers like a device behind the applied download queue (every tenth probe
lost for a loss of 10 percent, the set delay plus 12 ms) and sends no packet. So a profile with
nothing set gives a healthy link and a profile with 100 ms and 10 percent loss gives numbers the
check says match it.
"""

import os
import re
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

MAC_ADDRESS = "02:00:00:00:00:01"
IP_ADDRESS = "192.168.50.184"
NAME = "Fixture phone"
HEADERS = {"Authorization": "Bearer browser-test"}
LIMIT = 20000
PROBES = "20"


def start_fixture():
    environment = os.environ.copy()
    environment["JITTERSIM_BROWSER_TEST_SCHEDULER"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True,
        env=environment,
    )
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=15):
            process.terminate()
            raise RuntimeError("Connection check dashboard fixture did not start")
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


def check_width(browser, width, height):
    process, port = start_fixture()
    try:
        base = f"http://127.0.0.1:{port}"
        context = browser.new_context(viewport={"width": width, "height": height},
                                      accept_downloads=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def post(path, data):
            response = page.request.post(base + path, headers=HEADERS, data=data)
            assert response.ok, (path, response.status, response.text())
            return response.json()

        def apply(profile_id):
            post("/api/connected-devices/apply-profile",
                 {"ipv4_address": IP_ADDRESS, "mac_address": MAC_ADDRESS, "profile_id": profile_id})
            post("/api/apply", {})

        post("/api/profiles", {"id": "nolimits", "name": "No limits"})
        post("/api/profiles", {"id": "lossy", "name": "Lossy",
                               "download": {"delay_ms": 100, "loss_percent": 10}})
        post("/api/connected-device-labels",
             {"mac_address": MAC_ADDRESS, "display_name": NAME, "device_type": "phone"})
        apply("nolimits")
        sign_in(page, base)
        page.locator('[data-page-link="devices"]').first.click()
        row = page.locator(f'.connected-row[data-mac-address="{MAC_ADDRESS}"]')
        open_button = row.get_by_role("button", name=f"Check connection of {NAME}")
        expect(open_button).to_be_enabled(timeout=LIMIT)
        # The two buttons sit side by side on one line.
        expect(row.locator(".speed-test-line").get_by_role("button")).to_have_count(2)
        open_button.click()

        dialog = page.locator("#conn-check-dialog")
        history = page.locator("#conn-check-history tbody tr")
        result = page.locator("#conn-check-result")
        expect(dialog).to_be_visible()
        expect(page.locator("#conn-check-title")).to_have_text(f"Check connection: {NAME}")
        expect(page.locator("#conn-check-title")).to_be_focused()
        expect(page.locator("#conn-check-probes")).to_have_value("100")
        expect(page.locator("#conn-check-hint")).to_contain_text("about 5% or more")
        expect(page.locator("#conn-check-history")).to_contain_text("No checks saved")
        expect(result).to_be_hidden()

        def run_check(label):
            page.locator("#conn-check-probes").fill(PROBES)
            page.locator("#conn-check-label").fill(label)
            page.locator("#conn-check-begin").click()
            expect(page.locator("#conn-check-progress")).to_contain_text("probes", timeout=LIMIT)
            expect(page.locator("#conn-check-running")).to_be_visible()
            expect(page.locator("#conn-check-start")).to_be_hidden()
            expect(page.locator("#conn-check-announce")).to_contain_text(
                "Check saved.", timeout=LIMIT)
            expect(result).to_be_visible()
            expect(page.locator("#conn-check-start")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth") <= width + 1

        # A healthy link with a profile that sets nothing.
        run_check("Before")
        expect(history).to_have_count(1)
        expect(history.first).to_contain_text("No limits")
        expect(history.first).to_contain_text("Matches the profile")
        expect(result).to_contain_text("20 probes sent, 20 answered, 0 lost")
        expect(result.locator('tr[data-metric="loss"]')).to_contain_text("Not set")
        dialog.screenshot(path=f"test-results/connection-check-before-{width}.png")

        # The same device after a profile with a delay and a loss.
        apply("lossy")
        run_check("After")
        expect(history).to_have_count(2)
        expect(history.first).to_contain_text("Lossy")
        expect(history.first).to_contain_text("10% lost")
        expect(history.first).to_contain_text(re.compile(r"median 11[2-4] ms"))
        expect(result).to_contain_text("18 answered, 2 lost")
        loss = result.locator('tr[data-metric="loss"]')
        expect(loss).to_contain_text("10%")
        expect(loss).to_contain_text("2 of 20 lost")
        expect(loss).to_contain_text("Matches")
        delay = result.locator('tr[data-metric="delay"]')
        expect(delay).to_contain_text("100 ms")
        expect(delay).to_contain_text("Matches")
        # Every number carries a status word, never only a colour.
        for metric in ("loss", "delay", "jitter", "duplicate", "reorder"):
            expect(result.locator(f'tr[data-metric="{metric}"] td').last).not_to_be_empty()
        expect(result).to_contain_text("Only the download side of a profile can be checked")
        expect(result.locator("tr[data-metric=jitter]")).to_contain_text("Not judged")
        dialog.screenshot(path=f"test-results/connection-check-after-{width}.png")

        # An older result can be shown again.
        history.nth(1).get_by_role("button", name=re.compile("^Show check")).click()
        expect(result).to_contain_text("Label: Before")
        expect(page.locator("#conn-check-result-heading")).to_be_focused()

        # Both exports hold the numbers and no secret.
        with page.expect_download() as download_info:
            page.locator("#conn-check-export-csv").click()
        exported = Path(download_info.value.path()).read_text()
        lines = exported.strip().splitlines()
        assert lines[0].startswith("id,started_at"), lines[0]
        assert len(lines) == 3, exported
        assert "set_loss_percent" in lines[0] and "verdict" in lines[0]
        assert "browser-test" not in exported and "Bearer" not in exported
        with page.expect_download() as download_info:
            page.locator("#conn-check-export-json").click()
        assert '"jittersim-connection-checks"' in Path(download_info.value.path()).read_text()

        # Cancelling saves nothing.
        page.locator("#conn-check-probes").fill("300")
        page.locator("#conn-check-begin").click()
        expect(page.locator("#conn-check-progress")).to_contain_text("probes", timeout=LIMIT)
        page.locator("#conn-check-cancel").click()
        expect(page.locator("#conn-check-announce")).to_have_text(
            "Cancelled. Nothing was saved.", timeout=LIMIT)
        expect(page.locator("#conn-check-start")).to_be_visible()
        expect(history).to_have_count(2)

        # A check survives closing the window and shows again on reopening.
        page.locator("#conn-check-begin").click()
        expect(page.locator("#conn-check-progress")).to_contain_text("probes", timeout=LIMIT)
        page.locator("#conn-check-close").click()
        expect(dialog).to_be_hidden()
        expect(open_button).to_be_focused()
        open_button.click()
        expect(page.locator("#conn-check-progress")).to_contain_text("probes", timeout=LIMIT)
        page.locator("#conn-check-cancel").click()
        expect(page.locator("#conn-check-announce")).to_have_text(
            "Cancelled. Nothing was saved.", timeout=LIMIT)

        # A cancel that arrives after the check ended must not leave the window stuck.
        page.locator("#conn-check-begin").click()
        expect(page.locator("#conn-check-progress")).to_contain_text("probes", timeout=LIMIT)
        run_id = page.request.get(base + "/api/connection-checks?mac=" + MAC_ADDRESS,
                                  headers=HEADERS).json()["active"][0]["id"]

        def cancel_first(route):
            post("/api/connection-checks/cancel", {"run_id": run_id})
            route.continue_()

        page.route("**/api/connection-checks/cancel", cancel_first)
        page.locator("#conn-check-cancel").click()
        expect(page.locator("#conn-check-announce")).to_have_text(
            "Cancelled. Nothing was saved.", timeout=LIMIT)
        expect(page.locator("#conn-check-start")).to_be_visible()
        expect(page.locator("#conn-check-running")).to_be_hidden()
        page.unroute("**/api/connection-checks/cancel")
        expect(history).to_have_count(2)

        # Deleting a result asks first, in the page's own question above the open window.
        delete = history.first.get_by_role("button", name=re.compile("^Delete check"))
        question = page.locator("dialog.workflow-confirm")
        delete.click()
        expect(question).to_be_visible()
        expect(question.get_by_role("heading", name="Delete connection check")).to_be_visible()
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
        question.get_by_role("button", name="Delete check", exact=True).click()
        expect(question).to_have_count(0)
        expect(history).to_have_count(1)
        expect(page.locator("#conn-check-announce")).to_have_text("Check deleted.")
        expect(page.locator("#conn-check-title")).to_be_focused()
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        expect(open_button).to_be_focused()

        # A refused first load must not leave the private window or focus behind.
        def refuse_open(route):
            route.fulfill(status=401, content_type="application/json",
                          body='{"error":"Sign in again","registration_required":true}')

        page.route("**/api/connection-checks?mac=*", refuse_open)
        open_button.click()
        expect(page.locator("#private-dashboard")).to_be_hidden(timeout=LIMIT)
        expect(page.locator("#conn-check-dialog")).to_have_count(0)
        expect(page.locator("#open-admin-credentials")).to_be_focused()
        page.unroute("**/api/connection-checks?mac=*", refuse_open)
        page.goto(base)
        expect(page.locator("#private-dashboard")).to_be_visible(timeout=LIMIT)

        # A protected device can be checked and shows the plain Wi-Fi path.
        post("/api/unaffected-devices", {"mac_address": MAC_ADDRESS, "display_name": NAME})
        page.reload()
        expect(page.locator("#private-dashboard")).to_be_visible(timeout=LIMIT)
        page.locator('[data-page-link="devices"]').first.click()
        expect(row.get_by_role("button", name=f"Test speed of {NAME}")).to_be_disabled(
            timeout=LIMIT)
        expect(open_button).to_be_enabled()
        expect(row).to_contain_text("plain Wi-Fi path")
        open_button.click()
        run_check("Protected")
        expect(history.first).to_contain_text("Baseline")
        expect(result).to_contain_text("no profile applies")
        expect(result).to_contain_text("Not judged")
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
            print("Connection check acceptance passed at desktop and phone widths.")
        finally:
            browser.close()


if __name__ == "__main__":
    run()
