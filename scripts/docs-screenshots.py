#!/usr/bin/env python3
"""Retake the screenshots in docs/screenshots from the browser test fixture.

The fixture (tests/browser/fixture.py) is a disposable dashboard with a fake Pi. It holds sample
data only: one test phone, two profiles and a staged release named candidate-release. No real
phone, Pi or home network is shown. Run it from the repository root with Playwright installed
(`pip install 'playwright==1.63.0'`, the version of the browser jobs, and
`python -m playwright install chromium`):

    python scripts/docs-screenshots.py

It writes the five files named in docs/README.md ("Screenshots") and nothing else. `--out DIR`
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


def start_fixture():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "JITTERSIM_BROWSER_TEST_SEED": "1", "JITTERSIM_BROWSER_TEST_SCHEDULER": "1"},
    )
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=20):
            process.kill()
            raise RuntimeError("the browser fixture did not start")
        return process, int(process.stdout.readline().strip())


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


def main():
    parser = argparse.ArgumentParser(description="Retake the screenshots in docs/screenshots.")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "screenshots",
                        help="folder for the five images (default: docs/screenshots)")
    out = parser.parse_args().out
    out.mkdir(parents=True, exist_ok=True)
    process, port = start_fixture()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"))
            overview(browser, port, out / "experience-overview-desktop.png", DESKTOP, False, True)
            overview(browser, port, out / "experience-overview-phone.png", PHONE, True, False)
            cards(browser, port, out)
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
    print("Wrote the five images in", out)


if __name__ == "__main__":
    main()
