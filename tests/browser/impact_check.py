"""Check the Impact card in Diagnostics: live rows and a restart and reset and protected clients.

The fixture's fake kernel answers every queue counter read with larger numbers: 100 more
forwarded packets and 11 more dropped ones and 150000 more bytes. A profile with 10% loss shows
about 9.9% actual loss and the download cap of 250 kbit/s is reached at about 240 kbit/s.
"""

import json
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
RESET_MESSAGE = "Network is reset. Choose Start with saved settings to start impact checks."
LOSS_LINE = re.compile(r"Loss: set 10% · actual 9\.9% \((\d+) of (\d+) packets dropped\)")
SPEED_LINE = re.compile(
    r"Speed: set 250 kbit/s · using \d+ kbit/s · peak \d+ kbit/s · cap reached"
)
AFFECTED = "Affected in the last minute"
PAUSED = {
    "available": False,
    "paused": True,
    "message": "Impact could not be read right now.",
    "sampled_at": 0,
    "queues": [],
    "protected": [],
}
LIMIT = 25_000  # milliseconds to wait for a few 5 second polls


def sign_in(page, base):
    page.goto(base)
    expect(page.locator("#private-dashboard")).to_be_hidden()
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill("browser-test")
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    page.locator("#remember-admin-token").check()
    page.locator("#close-credentials").click()
    # The check ends after many requests to a dashboard that answers one at a time.
    # A slow shared CI machine needs more than 5 seconds (like apply_clarity).
    expect(page.locator("#message")).to_contain_text(
        "Token accepted. Diagnostics loaded.", timeout=20000
    )


def check_width(browser, base, width, height, check_idle):
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    impact_requests = []
    page.on(
        "request", lambda request: impact_requests.append(1) if "/api/impact" in request.url else None
    )
    sign_in(page, base)

    def post(path, data):
        response = page.request.post(base + path, headers=HEADERS, data=data)
        assert response.ok, (path, response.status, response.text())

    # start from an unprotected client with a fresh set of queues whatever an earlier width did
    page.request.post(
        base + "/api/unaffected-devices/delete", headers=HEADERS, data={"mac_address": MAC_ADDRESS}
    )
    post(
        "/api/profiles",
        {
            "id": "lossy",
            "name": "Lossy",
            "upload": {"loss_percent": 10, "delay_ms": 100, "jitter_ms": 20},
            "download": {"loss_percent": 10, "bandwidth_kbit": 250},
        },
    )
    post(
        "/api/connected-device-labels",
        {"mac_address": MAC_ADDRESS, "display_name": NAME, "device_type": "phone"},
    )
    post(
        "/api/connected-devices/apply-profile",
        {"ipv4_address": IP_ADDRESS, "mac_address": MAC_ADDRESS, "profile_id": "lossy"},
    )
    post("/api/apply", {})

    page.locator('[data-page-link="diagnostics"]').click()
    card = page.locator("section.impact-card")
    expect(card).to_be_visible()
    # the card is the first one on the page
    expect(page.locator("#page-diagnostics > section").first).to_have_class(
        re.compile("impact-card")
    )
    state = page.locator("#impact-state")
    listing = page.locator("#impact-list")
    rows = listing.locator(".impact-row")
    upload = rows.filter(has_text="Upload")
    download = rows.filter(has_text="Download")

    # first one sample only and then the figures of the last minute
    expect(rows).to_have_count(2)
    expect(listing).to_contain_text("Collecting…")
    expect(upload).to_contain_text(AFFECTED, timeout=LIMIT)
    expect(download).to_contain_text(AFFECTED, timeout=LIMIT)
    expect(upload).to_contain_text(NAME)
    expect(upload.locator(".impact-lines")).to_contain_text(LOSS_LINE)
    expect(download.locator(".impact-lines")).to_contain_text(LOSS_LINE)
    expect(download.locator(".impact-lines")).to_contain_text(SPEED_LINE)
    expect(state).to_have_text("Live")
    # screen readers hear the verdict change once and the list itself is not a live region
    expect(page.locator("#impact-status")).to_contain_text(f"{NAME} upload: {AFFECTED}")
    assert listing.get_attribute("aria-live") is None
    assert page.locator("#impact-status").get_attribute("role") == "status"
    expect(page.locator("#impact-note")).to_contain_text(
        "Delay, jitter, reorder, duplicate and corrupt are shown as configured"
    )
    # delay is shown as set and never as measured
    expect(upload.locator(".impact-lines")).to_contain_text(
        "Delay: set 100 ms ± 20 · configured, not measured"
    )
    expect(download).not_to_contain_text("Delay")
    expect(page.locator("#impact-protected")).to_be_hidden()
    page.wait_for_timeout(300)
    card.screenshot(path=f"test-results/impact-check-{width}.png")
    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1

    # the rows are updated in place so a text selection and a screen reader are left alone
    page.evaluate("document.querySelectorAll('#impact-list .impact-row')"
                  ".forEach((row) => { row.dataset.mark = 'kept'; })")
    before = upload.locator(".impact-lines").inner_text()
    expect(upload.locator(".impact-lines")).not_to_have_text(before, timeout=LIMIT)
    expect(listing.locator(".impact-row[data-mark='kept']")).to_have_count(2)

    # a late answer keeps the rows and the badge says so
    page.route(
        "**/api/impact",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(PAUSED)
        ),
    )
    expect(state).to_have_text("Update paused", timeout=LIMIT)
    expect(rows).to_have_count(2)
    expect(upload).to_contain_text(AFFECTED)
    expect(listing.locator(".impact-row[data-mark='kept']")).to_have_count(2)
    page.unroute("**/api/impact")
    expect(state).to_have_text("Live", timeout=LIMIT)
    expect(listing.locator(".impact-row[data-mark='kept']")).to_have_count(2)

    if check_idle:
        # polling stops when Diagnostics is not on screen
        page.locator('[data-page-link="devices"]').click()
        page.wait_for_timeout(800)
        seen = len(impact_requests)
        page.wait_for_timeout(7000)
        assert len(impact_requests) == seen, "the card kept polling on another page"
        page.locator('[data-page-link="diagnostics"]').click()
        expect(card).to_be_visible()
        expect(upload).to_contain_text(AFFECTED, timeout=LIMIT)

    # a new apply replaces the queues and the counters start again so the card starts again too
    post("/api/apply", {})
    expect(listing).to_contain_text("Collecting…", timeout=LIMIT)
    expect(upload).to_contain_text(AFFECTED, timeout=LIMIT)
    text = upload.locator(".impact-lines").inner_text()
    found = LOSS_LINE.search(text)
    assert found and int(found.group(1)) >= 0 and int(found.group(2)) > 0, text

    # a reset network has no queues to read
    post("/api/reset", {})
    expect(listing).to_have_text(RESET_MESSAGE, timeout=LIMIT)
    expect(state).to_have_text("Unavailable")
    expect(rows).to_have_count(0)
    expect(page.locator("#impact-note")).to_be_hidden()

    # a protected client has no queue and appears only by name on one muted line
    post("/api/unaffected-devices", {"mac_address": MAC_ADDRESS, "display_name": NAME})
    post("/api/apply", {})
    protected = page.locator("#impact-protected")
    expect(protected).to_have_text(f"Protected (not affected): {NAME}", timeout=LIMIT)
    expect(rows).to_have_count(0)
    expect(listing).to_contain_text("Nothing is slowed down right now")
    shown = card.inner_text()
    assert IP_ADDRESS not in shown and MAC_ADDRESS not in shown, shown
    answer = page.request.get(base + "/api/impact", headers=HEADERS).json()
    assert answer["queues"] == [] and answer["protected"] == [NAME], answer
    page.wait_for_timeout(300)
    card.screenshot(path=f"test-results/impact-check-protected-{width}.png")
    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1

    assert not errors, errors
    context.close()


def run():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Impact check dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                for width, height, check_idle in ((1920, 1000, True), (390, 844, False)):
                    check_width(browser, f"http://127.0.0.1:{port}", width, height, check_idle)
                print("Impact check acceptance passed at desktop and phone widths.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
