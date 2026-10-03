"""Use the optional device switch and domain exceptions at desktop and phone widths."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.protected_clients import HEADERS, MAC_ADDRESS, sign_in


def check_width(browser, base, width, height):
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    sign_in(page, base)
    request = page.request
    response = request.post(
        base + "/api/unaffected-devices",
        headers=HEADERS,
        data={"mac_address": MAC_ADDRESS, "display_name": "Fixture client"},
    )
    assert response.ok, response.text()
    request.post(
        base + "/api/ad-blocking",
        headers=HEADERS,
        data={"mac_address": MAC_ADDRESS, "enabled": False},
    )
    page.reload()
    expect(page.locator("#private-dashboard")).to_be_visible()
    page.locator('a[data-page-link="devices"]').first.click()
    toggle = page.get_by_role("button", name="Protected devices", exact=True)
    if toggle.get_attribute("aria-expanded") == "false":
        toggle.click()
    switch = page.get_by_role("checkbox", name="Block ads for Fixture client", exact=True)
    expect(switch).not_to_be_checked()
    switch.focus()
    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/ad-blocking") and response.request.method == "POST"
        )
    ):
        page.keyboard.press("Space")
    expect(switch).to_be_checked()
    expect(page.locator("#ad-blocking-status")).to_contain_text("Waiting")
    data = request.get(base + "/api/ad-blocking", headers=HEADERS).json()
    assert data["mac_addresses"] == [MAC_ADDRESS]
    assert data["service"]["ready"] is False
    page.reload()
    page.locator('a[data-page-link="devices"]').first.click()
    expect(switch).to_be_checked()
    page.get_by_text("Ad-blocking options", exact=True).click()
    page.locator("#ad-blocking-allowances").fill("GOOD.TEST.\nchild.example.com")
    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/ad-blocking") and response.request.method == "POST"
        )
    ):
        page.get_by_role("button", name="Save allowed domains").click()
    data = request.get(base + "/api/ad-blocking", headers=HEADERS).json()
    assert data["allowed_domains"] == ["child.example.com", "good.test"]
    expect(page.locator("#ad-blocking-allowances")).to_have_value("child.example.com\ngood.test")
    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/ad-blocking") and response.request.method == "POST"
        )
    ):
        page.get_by_role("button", name="Update ad list").click()
    request.post(base + "/api/reset", headers=HEADERS, data={})
    page.reload()
    page.locator('a[data-page-link="devices"]').first.click()
    expect(switch).to_be_checked()
    expect(page.locator("#ad-blocking-status")).to_contain_text("paused")
    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/ad-blocking") and response.request.method == "POST"
        )
    ):
        switch.uncheck()
    expect(switch).not_to_be_checked()
    assert request.get(base + "/api/unaffected-devices", headers=HEADERS).json()["devices"]
    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
    page.get_by_text("Ad-blocking options", exact=True).click()
    dismiss = page.get_by_role("button", name="Dismiss", exact=True)
    if dismiss.is_visible():
        dismiss.click()
    page.locator("#unaffected-title").locator(
        "xpath=ancestor::section[contains(@class, 'card')][1]"
    ).screenshot(path=f"test-results/ad-blocking-{width}.png")
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
                raise RuntimeError("Ad-blocking dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                for width, height in ((1920, 1000), (390, 844)):
                    check_width(browser, f"http://127.0.0.1:{port}", width, height)
            finally:
                browser.close()
        print("Ad-blocking controls passed at desktop and phone widths.")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
