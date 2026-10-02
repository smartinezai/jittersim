"""Exercise name recovery through the served release dashboard."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def check_wifi_recovery(page):
    button = page.locator("#reset-wifi-name")
    fallback = page.get_by_role("button", name="Manual Pi access-point fallback")
    expect(fallback).to_have_attribute("aria-expanded", "false")
    fallback.click()
    expect(fallback).to_have_attribute("aria-expanded", "true")
    expect(button).to_be_enabled()
    expect(button).to_be_in_viewport()
    field = page.locator("#access-point-ssid")
    field.fill("Copied network")
    requests = []
    page.on("request", lambda request: requests.append(request)
            if request.url.endswith("/api/access-point/reset-name") else None)
    page.once("dialog", lambda dialog: dialog.dismiss())
    button.click()
    expect(field).to_have_value("Copied network")
    assert not requests

    page.route("**/api/access-point/reset-name", lambda route: route.fulfill(
        status=502, content_type="application/json", body='{"error":"Simulated reset failure"}'))
    page.once("dialog", lambda dialog: dialog.accept())
    button.click()
    expect(page.locator("#wifi-reset-status")).to_contain_text("Simulated reset failure")
    expect(button).to_be_enabled()
    expect(field).to_have_value("Copied network")
    page.unroute("**/api/access-point/reset-name")

    held = []
    page.route("**/api/access-point/reset-name", lambda route: held.append(route))
    page.once("dialog", lambda dialog: dialog.accept())
    button.click()
    expect(button).to_be_disabled()
    assert len(held) == 1
    held[0].continue_()
    expect(field).to_have_value("JitterSim")
    expect(button).to_be_enabled()
    assert len(requests) == 2
    page.unroute("**/api/access-point/reset-name")
    page.route("**/api/health", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body='{"helper_configured":false,"mode":"configuration-only"}'))
    page.reload()
    expect(button).to_be_disabled()
    page.unroute("**/api/health")
    page.reload()
    expect(button).to_be_enabled()


def check_router_copy(page):
    page.locator("#open-router-credentials").click()
    page.locator("#router-admin-password").fill("browser-router-password")
    page.locator("#check-router-password").click()
    expect(page.locator("#router-credential-state")).to_contain_text("Accepted")
    page.locator("#remember-router-password").check()
    page.locator("#close-credentials").click()
    button = page.locator("#sync-router-ap")
    copies = []
    page.on("request", lambda request: copies.append(request)
            if request.url.endswith("/api/router/sync-ap") else None)

    def check_dialog(dialog, accept):
        assert "Browser home using WPA2 (2.4 GHz)" in dialog.message
        assert "browser-wifi-password" not in dialog.message
        dialog.accept() if accept else dialog.dismiss()

    page.once("dialog", lambda dialog: check_dialog(dialog, False))
    button.click()
    expect(page.locator("#router-action-message")).to_contain_text("Copy cancelled")
    assert not copies
    page.once("dialog", lambda dialog: check_dialog(dialog, True))
    button.click()
    expect(page.locator("#router-action-message")).to_contain_text("Copied Browser home using WPA2")
    assert len(copies) == 1
    assert len(copies[0].post_data_json["expected_fingerprint"]) == 64


def check_router_switch(page):
    button = page.locator("#router-wifi-toggle")
    state = page.locator("#router-wifi-status")
    expect(button).to_be_enabled(timeout=10000)
    expect(button).to_have_attribute("aria-checked", "true")
    expect(button).to_be_in_viewport()
    writes = []
    page.on("request", lambda request: writes.append(request)
            if request.url.endswith("/api/router/wifi") else None)
    page.once("dialog", lambda dialog: dialog.dismiss())
    button.click()
    assert not writes
    expect(button).to_have_attribute("aria-checked", "true")

    held = []
    page.route("**/api/router/wifi", lambda route: held.append(route))
    page.once("dialog", lambda dialog: dialog.accept())
    button.click()
    expect(button).to_be_disabled()
    expect(state).to_contain_text("Changing")
    assert len(held) == 1 and held[0].request.post_data_json["background"] is True
    assert page.request.get(page.url + "api/health").status == 200
    held[0].continue_()
    expect(button).to_have_attribute("aria-checked", "false", timeout=10000)
    expect(state).to_contain_text("Off")
    expect(button).to_be_enabled()
    page.unroute("**/api/router/wifi")
    button.click()
    expect(button).to_have_attribute("aria-checked", "true", timeout=10000)
    expect(state).to_contain_text("On")
    assert len(writes) == 2


def run():
    process = subprocess.Popen([sys.executable, "-m", "tests.browser.fixture"],
                               stdout=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Browser fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"))
            context = browser.new_context(viewport={"width": 1920, "height": 1000})
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(f"http://127.0.0.1:{port}")
                page.locator("#open-admin-credentials").click()
                page.locator("#admin-token").fill("browser-test")
                page.locator("#check-admin-token").click()
                expect(page.locator("#admin-credential-state")).to_contain_text("Accepted")
                page.locator("#remember-admin-token").check()
                page.locator("#close-credentials").click()
                page.locator('[data-page-link="router"]').click()
                check_router_copy(page)
                check_router_switch(page)
                check_wifi_recovery(page)
                expect(page.locator("#reset-wifi-name")).to_be_in_viewport()
                expect(page.locator("#router-wifi-toggle")).to_be_enabled(timeout=10000)
                page.screenshot(path="test-results/wifi-pi-desktop.png", animations="disabled")
                page.set_viewport_size({"width": 1280, "height": 800})
                # Whole controls and not a sliver of them. The sidebar lists nine pages
                # and a laptop screen must still show Panic and the router switch and both
                # copy buttons. The Linux fonts on CI are wider than the Mac ones.
                expect(page.locator("#router-wifi-toggle")).to_be_in_viewport(ratio=1)
                expect(page.locator("#panic-button")).to_be_in_viewport(ratio=1)
                expect(page.locator("#copy-wifi-name")).to_be_in_viewport(ratio=1)
                expect(page.locator("#copy-wifi-password")).to_be_in_viewport(ratio=1)
                sidebar = page.locator(".sidebar").bounding_box()
                assert sidebar["x"] == 0 and sidebar["width"] < 300
                page.screenshot(path="test-results/wifi-pi-laptop.png", animations="disabled")
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#reset-wifi-name").scroll_into_view_if_needed()
                expect(page.locator("#reset-wifi-name")).to_be_in_viewport()
                page.screenshot(path="test-results/wifi-pi-phone.png", animations="disabled")
                assert page.evaluate("document.documentElement.scrollWidth") <= 391
                page.set_viewport_size({"width": 390, "height": 360})
                page.locator("#open-admin-credentials").click()
                page.locator("#admin-token").fill("browser-test")
                expect(page.locator("#check-admin-token")).to_be_in_viewport()
                page.locator("#check-admin-token").click()
                expect(page.locator("#admin-credential-state")).to_contain_text("Accepted")
                page.locator("#close-credentials").click()
                assert not errors, errors
                print("Wi-Fi recovery browser acceptance passed.")
            finally:
                context.tracing.stop(path="test-results/wifi-pi-browser.zip")
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
