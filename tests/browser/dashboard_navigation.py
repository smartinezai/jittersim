"""Move between dashboard sections, collapse cards and keep the sidebar in reach."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

PAGES = ("devices", "profiles", "schedules", "router", "traffic", "access", "diagnostics")


def unlock(page):
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill("browser-test")
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    page.locator("#remember-admin-token").check()
    page.locator("#close-credentials").click()


def only_visible(page, name):
    for other in PAGES:
        section = page.locator(f"#page-{other}")
        if other == name:
            expect(section).to_be_visible()
        else:
            expect(section).to_be_hidden()
    expect(page.locator(f'[data-page-link="{name}"]')).to_have_attribute("aria-current", "page")


def check_sections(page):
    only_visible(page, "devices")
    for name in PAGES[1:]:
        page.locator(f'[data-page-link="{name}"]').click()
        only_visible(page, name)
        assert page.url.endswith("#" + name)
    page.go_back()
    only_visible(page, "access")
    page.goto(page.url.split("#")[0] + "#router")
    only_visible(page, "router")
    page.goto(page.url.split("#")[0] + "#nonsense")
    only_visible(page, "devices")


def check_nothing_floats(page):
    pinned = page.evaluate(
        """() => Array.from(document.querySelectorAll('body *'))
            .filter((node) => ['fixed', 'sticky'].includes(getComputedStyle(node).position))
            .filter((node) => !node.closest('#toast, #credentials-panel'))
            .filter((node) => !node.hidden && node.getClientRects().length)
            .map((node) => node.className || node.id || node.tagName)"""
    )
    assert pinned == ["sidebar"], pinned
    page.locator('[data-page-link="schedules"]').click()
    page.locator("#page-schedules").evaluate("(node) => node.scrollIntoView({block: 'end'})")
    page.mouse.wheel(0, 3000)
    for control in ("#panic-button", "#apply-button", "#router-wifi-toggle"):
        expect(page.locator(control)).to_be_in_viewport()


def check_collapsing(page):
    page.locator('[data-page-link="schedules"]').click()
    timer = page.get_by_role("button", name="Run a profile timer")
    expect(timer).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#timer-list")).to_be_hidden()
    timer.click()
    expect(timer).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#timer-list")).to_be_visible()
    page.reload()
    only_visible(page, "schedules")
    expect(page.get_by_role("button", name="Run a profile timer")).to_have_attribute(
        "aria-expanded", "true"
    )
    expect(page.locator("#timer-list")).to_be_visible()
    page.get_by_role("button", name="Run a profile timer").click()
    expect(page.locator("#timer-list")).to_be_hidden()


def check_reveal(page):
    page.locator('[data-page-link="devices"]').click()
    only_visible(page, "devices")
    page.evaluate('revealElement(document.getElementById("time-quota-form"))')
    only_visible(page, "schedules")
    assert page.url.endswith("#schedules")
    # Field help icons are buttons too ("Service time limit ID help"), so match the card exactly.
    expect(page.get_by_role("button", name="Service time limit", exact=True)).to_have_attribute(
        "aria-expanded", "true"
    )
    expect(page.locator("#time-quota-form")).to_be_visible()


def check_narrow(page):
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth") <= 391
    expect(page.locator("#panic-button")).to_be_visible()
    for name in PAGES:
        expect(page.locator(f'[data-page-link="{name}"]')).to_be_visible()


def run():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Navigation dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                for width, height in ((1440, 900), (1280, 720)):
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{port}")
                    expect(page.locator(".sidebar-nav")).to_be_hidden()
                    unlock(page)
                    check_sections(page)
                    check_nothing_floats(page)
                    check_collapsing(page)
                    check_reveal(page)
                    page.locator('[data-page-link="devices"]').click()
                    page.screenshot(path=f"test-results/dashboard-devices-{width}.png")
                    if width == 1280:
                        check_narrow(page)
                        page.screenshot(path="test-results/dashboard-devices-390.png")
                    assert not errors, errors
                    context.close()
                print("Dashboard navigation browser acceptance passed.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
