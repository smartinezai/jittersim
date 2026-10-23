#!/usr/bin/env python3
"""Retake the screenshots in docs/screenshots from the browser test fixture.

The fixture (tests/browser/fixture.py) is a disposable dashboard with a fake Pi. It holds sample
data only: one test phone, two profiles and a staged release named candidate-release. No real
phone, Pi or home network is shown. Run it from the repository root with Playwright installed
(`pip install 'playwright==1.63.0'`, the version of the browser jobs, and
`python -m playwright install chromium`):

    python scripts/docs-screenshots.py

It writes the files named in docs/README.md ("Screenshots") and nothing else. `--out DIR`
writes them somewhere else first so that you can compare. Set JITTERSIM_BROWSER_EXECUTABLE to use
another Chromium. Commit only the images that changed.
"""

import argparse
import json
import os
import re
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = {"width": 1440, "height": 1000}
PHONE = {"width": 430, "height": 1200}
TOKEN = "browser-test"  # the fixture's own test token, never a real one


def start_fixture(*, seeded=False, address_follow=False, scheduler=False, wifi_peer=False):
    environment = os.environ.copy()
    environment.update({
        "JITTERSIM_BROWSER_TEST_SEED": "1" if seeded else "0",
        "JITTERSIM_BROWSER_TEST_ADDRESS_FOLLOW": "1" if address_follow else "0",
        "JITTERSIM_BROWSER_TEST_SCHEDULER": "1" if scheduler else "0",
        # Every browser request then comes from the fixture's phone on the test Wi-Fi.
        "JITTERSIM_BROWSER_TEST_WIFI_PEER": "1" if wifi_peer else "0",
        "PYTHONPATH": os.pathsep.join(
            part for part in (str(ROOT / "src"), environment.get("PYTHONPATH", "")) if part
        ),
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        text=True,
        env=environment,
    )
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=20):
            process.kill()
            raise RuntimeError("the browser fixture did not start")
        output = process.stdout.readline().strip()
        if not output:
            raise RuntimeError(f"the browser fixture exited with status {process.poll()}")
        return process, int(output)


def stop_fixture(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def unlock(page):
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill(TOKEN)
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    expect(page.locator("#token-check-status")).to_contain_text("Token accepted")
    page.locator("#close-credentials").click()


def rewrite_updates(route, staged):
    """Change the fixture's update status. It always stages candidate-release and sends release
    notes, which the real Pi's status does not carry yet. The overview shows the ordinary state of
    nothing staged, so that the sidebar has room for every page link."""

    response = route.fetch()
    body = response.json()
    body.pop("notes", None)
    if not staged:
        body.update(available=None, installable=False, signed_by=None)
    route.fulfill(response=response, body=json.dumps(body))


def settle(page):
    """Let the page redraw, hide the toast and show the top of the page."""

    page.locator("#toast").evaluate("toast => { toast.hidden = true; }")
    page.evaluate("window.scrollTo(0, 0)")
    # The phone header slides back in after a scroll. Wait for it.
    page.wait_for_timeout(1500)


def start_everything(page, phone):
    controls = page.locator("#controls-toggle")
    if phone and controls.is_visible():
        controls.click()
    page.locator("#apply-button").click()
    expect(page.locator("#message")).to_contain_text("Started")
    if phone and controls.is_visible():
        controls.click()


def start_slow_timer(page):
    page.get_by_role("link", name=re.compile("^Devices")).click()
    page.get_by_role("button", name=re.compile("^Open ")).first.click()
    page.get_by_role("combobox", name="Temporary profile", exact=True).select_option("slow")
    page.get_by_role("button", name="Start device timer", exact=True).click()
    expect(page.locator("#workspace-state")).to_contain_text("Timer")


def overview(browser, port, path, viewport, phone, prepare):
    """The Devices page with the test phone on a slow profile. The first call sets that up."""

    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    page.route(re.compile(r"/api/updates$"), lambda route: rewrite_updates(route, staged=False))
    page.goto(f"http://127.0.0.1:{port}")
    unlock(page)
    if prepare:
        start_everything(page, phone)
        start_slow_timer(page)
    page.get_by_role("link", name=re.compile("^Devices")).click()
    settle(page)
    page.screenshot(path=str(path))
    context.close()


def cards(browser, port, out):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    page.route(re.compile(r"/api/updates$"), lambda route: rewrite_updates(route, staged=True))
    page.goto(f"http://127.0.0.1:{port}")
    unlock(page)

    page.get_by_role("link", name="Access and rules", exact=True).click()
    page.locator("#dns-failure-percent").fill("20")
    page.get_by_role("button", name="Save access rules", exact=True).click()
    expect(page.locator("#dns-failure-status")).to_contain_text(re.compile(r"(?:Saved|Applied) DNS failure chance: 20%"))
    page.get_by_role("button", name="Apply access rules", exact=True).click()
    expect(page.locator("#dns-failure-status")).to_contain_text("Applied DNS failure chance: 20%")
    settle(page)
    page.locator(".access-options").screenshot(path=str(out / "dns-failure-controls.png"))

    page.get_by_role("link", name=re.compile("^Router and Wi-Fi")).click()
    page.locator("#gateway-persistence-toggle").check()
    page.locator("#gateway-persistence-save").click()
    expect(page.locator("#gateway-persistence-state")).to_contain_text("Saved")
    settle(page)
    page.locator("section.card:has(#gateway-persistence-toggle)").screenshot(
        path=str(out / "router-outage-persistence.png"))

    page.get_by_role("link", name=re.compile("^Updates")).click()
    expect(page.locator("#release-status")).to_contain_text("candidate-release")
    expect(page.get_by_role("button", name="Install candidate-release", exact=True)).to_be_enabled()
    settle(page)
    page.locator("#release-controls").screenshot(path=str(out / "staged-update-check.png"))
    context.close()


def address_changes(browser, out):
    process, port = start_fixture(address_follow=True)
    try:
        context = browser.new_context(viewport=DESKTOP)
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{port}")
        unlock(page)

        card = page.locator("#address-follow-card")
        expect(card).to_be_visible()
        expect(card.locator(".address-follow-row")).to_contain_text("Dad's phone")
        expect(page.locator("#address-follow-mode")).to_have_value("ask")
        settle(page)
        card.screenshot(path=str(out / "address-changes.png"))
        context.close()
    finally:
        stop_fixture(process)


def speed_test_controls(browser, out):
    process, port = start_fixture(seeded=True, scheduler=True)
    try:
        context = browser.new_context(viewport=DESKTOP)
        page = context.new_page()
        base = f"http://127.0.0.1:{port}"
        page.goto(base)

        headers = {"Authorization": f"Bearer {TOKEN}"}

        def post(path, data):
            response = page.request.post(base + path, headers=headers, data=data)
            assert response.ok, (path, response.status, response.text())

        post("/api/connected-device-labels", {
            "mac_address": "02:00:00:00:00:01",
            "display_name": "Fixture phone",
            "device_type": "phone",
        })
        post("/api/connected-devices/apply-profile", {
            "ipv4_address": "192.168.50.184",
            "mac_address": "02:00:00:00:00:01",
            "profile_id": "normal",
        })
        post("/api/apply", {})

        unlock(page)
        page.locator('[data-page-link="devices"]').first.click()
        row = page.locator('.connected-row[data-mac-address="02:00:00:00:00:01"]')
        row.get_by_role("button", name="Test speed of Fixture phone").click()

        dialog = page.locator("#speed-test-dialog")
        expect(dialog).to_be_visible()
        expect(page.locator("#speed-test-provider")).to_have_value("cloudflare")
        expect(page.locator("#speed-test-begin")).to_be_visible()
        page.locator("#speed-test-duration").fill("45")
        page.locator("#speed-test-label").fill("Before profile")
        page.locator("#speed-test-label").evaluate("element => element.blur()")
        settle(page)
        dialog.screenshot(path=str(out / "device-speed-test-controls.png"))
        context.close()
    finally:
        stop_fixture(process)


def this_device(browser, out):
    """The browser is the fixture's phone on the test Wi-Fi, so its row says This device and the
    speed test can run from the dashboard itself."""

    process, port = start_fixture(seeded=True, scheduler=True, wifi_peer=True)
    try:
        context = browser.new_context(viewport=DESKTOP)
        page = context.new_page()
        base = f"http://127.0.0.1:{port}"
        page.goto(base)

        headers = {"Authorization": f"Bearer {TOKEN}"}

        def post(path, data):
            response = page.request.post(base + path, headers=headers, data=data)
            assert response.ok, (path, response.status, response.text())

        post("/api/dashboard-access/register-self", {"name": "Fixture phone"})
        post("/api/connected-device-labels", {
            "mac_address": "02:00:00:00:00:01",
            "display_name": "Fixture phone",
            "device_type": "phone",
        })
        post("/api/connected-devices/apply-profile", {
            "ipv4_address": "192.168.50.184",
            "mac_address": "02:00:00:00:00:01",
            "profile_id": "normal",
        })
        post("/api/apply", {})

        unlock(page)
        page.locator('[data-page-link="devices"]').first.click()
        row = page.locator('.connected-row[data-mac-address="02:00:00:00:00:01"]')
        expect(row.locator(".this-device-badge")).to_have_text("This device")
        expect(page.locator("#quick-reset-wifi-name")).to_be_enabled()
        settle(page)
        # The top of the row: the name with its label, the details and the buttons.
        box = row.bounding_box()
        page.screenshot(path=str(out / "this-device.png"), full_page=True, clip={**box, "height": 255})
        page.locator(".router-wifi-copy").screenshot(path=str(out / "reset-wifi-name-button.png"))

        row.get_by_role("button", name="Test speed of Fixture phone").click()
        dialog = page.locator("#speed-test-dialog")
        expect(dialog).to_be_visible()
        expect(page.locator("#speed-test-provider")).to_have_value("dashboard")
        expect(page.locator("#speed-test-begin")).to_be_visible()
        page.locator("#speed-test-label").fill("Before profile")
        page.locator("#speed-test-label").evaluate("element => element.blur()")
        settle(page)
        dialog.screenshot(path=str(out / "device-speed-test-dashboard.png"))
        context.close()
    finally:
        stop_fixture(process)


def main():
    parser = argparse.ArgumentParser(description="Retake the screenshots in docs/screenshots.")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "screenshots",
                        help="folder for the ten images (default: docs/screenshots)")
    out = parser.parse_args().out
    out.mkdir(parents=True, exist_ok=True)
    process, port = start_fixture(seeded=True, scheduler=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"))
            overview(browser, port, out / "experience-overview-desktop.png", DESKTOP, False, True)
            overview(browser, port, out / "experience-overview-phone.png", PHONE, True, False)
            cards(browser, port, out)
            address_changes(browser, out)
            speed_test_controls(browser, out)
            this_device(browser, out)
            browser.close()
    finally:
        stop_fixture(process)
    print("Wrote the ten images in", out)


if __name__ == "__main__":
    main()
