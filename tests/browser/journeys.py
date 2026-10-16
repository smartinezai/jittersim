"""Exercise complete page behavior against the real API in CI."""

import json
import os
import re
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.confirm_dialogs import (
    answer_confirm,
    cancel_with_button,
    cancel_with_escape,
    check_destructive_confirms,
)
from tests.browser.phone import close_controls, sidebar_control
from tests.browser.service_list_focus import check_service_list_contexts

ROUTER_LINK = re.compile("^Router and Wi-Fi")
UPDATES_LINK = re.compile("^Updates")
DEVICES_LINK = re.compile("^Devices")


def dismiss_toasts(page):
    page.locator("#toast").evaluate("toast => { toast.hidden = true; }")


def unlock(page):
    """Enter the admin token the way the sidebar asks for it and keep it for reloads."""

    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill("browser-test")
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    expect(page.locator("#token-check-status")).to_contain_text("Token accepted")
    page.locator("#remember-admin-token").check()
    page.locator("#close-credentials").click()


def profile_row(page, name):
    return page.locator(".saved-profile-row", has_text=name)


def check_wifi_recovery(page):
    page.get_by_role("link", name=ROUTER_LINK).click()
    # The Pi line keeps the fallback card closed until it is opened.
    fallback = page.get_by_role("button", name="Manual Pi access-point fallback")
    if fallback.get_attribute("aria-expanded") == "false":
        fallback.click()
    button = page.locator("#reset-wifi-name")
    expect(button).to_be_enabled()
    field = page.locator("#access-point-ssid")
    field.fill("Copied network")
    requests = []
    page.on("request", lambda request: requests.append(request)
            if request.url.endswith("/api/access-point/reset-name") else None)
    ask = dict(title="Restore the default Wi-Fi name?", text="The password and security type stay the same.")
    button.click()
    cancel_with_escape(page, button, verb="Restore name", **ask)
    expect(field).to_have_value("Copied network")
    expect(button).to_be_enabled()
    assert not requests
    button.click()
    cancel_with_button(page, button, **ask)
    expect(field).to_have_value("Copied network")
    assert not requests

    page.route("**/api/access-point/reset-name", lambda route: route.fulfill(
        status=502, content_type="application/json", body='{"error":"Simulated reset failure"}'))
    button.click()
    answer_confirm(page, "Restore name", **ask)
    expect(page.locator("#wifi-reset-status")).to_contain_text("Simulated reset failure")
    expect(button).to_be_enabled()
    expect(field).to_have_value("Copied network")
    page.unroute("**/api/access-point/reset-name")

    held = []
    page.route("**/api/access-point/reset-name", lambda route: held.append(route))
    button.click()
    answer_confirm(page, "Restore name", **ask)
    # The control becomes busy before the asynchronous request reaches the route.
    for _ in range(500):
        if held:
            break
        page.wait_for_timeout(10)
    expect(button).to_be_disabled()
    assert len(held) == 1, held
    held[0].continue_()
    expect(field).to_have_value("TestNet")
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


def check_access_and_help(page):
    if page.get_by_role("button", name="Discard unfinished edits", exact=True).is_visible():
        page.get_by_role("button", name="Discard unfinished edits", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="Continue", exact=True).click()
        expect(page.locator("#draft-notice")).to_be_hidden()
    page.get_by_role("link", name=DEVICES_LINK).click()
    page.get_by_role("button", name="Manage device and site access", exact=True).click()
    expect(page.locator("#site-rule-form")).to_be_visible()
    page.get_by_label("Rule name", exact=True).fill("Video for the test phone")
    page.get_by_label("Applies to", exact=True).select_option("device:alice")
    page.get_by_label("Domains to block", exact=True).fill("video.example.com\nstream.example.com")
    page.get_by_role("button", name="Save site rule", exact=True).click()
    expect(page.locator("#site-rule-list")).to_contain_text("Video for the test phone")
    expect(page.locator("#access-summary")).to_contain_text("Saved rules differ")
    page.locator("#dns-failure-percent").fill("20")
    page.get_by_role("button", name="Save access rules", exact=True).click()
    expect(page.locator("#dns-failure-status")).to_contain_text(
        re.compile(r"(?:Saved|Applied) DNS failure chance: 20%")
    )
    page.get_by_role("button", name="Apply access rules", exact=True).click()
    expect(page.locator("#dns-failure-status")).to_contain_text("Applied DNS failure chance: 20%")
    expect(page.locator("#access-summary")).to_contain_text("Saved access rules are active")
    page.set_viewport_size({"width": 1440, "height": 1000})
    dismiss_toasts(page)
    page.evaluate("window.scrollTo(0, 0)")
    page.locator(".access-options").screenshot(
        path="test-results/dns-failure-controls-desktop.png"
    )
    page.get_by_role("button", name="Reset network", exact=True).click()
    expect(page.locator("#message")).to_contain_text("reset")
    expect(page.locator("#access-summary")).to_contain_text("Saved access rules are active")
    page.get_by_role("link", name=DEVICES_LINK).click()
    expect(page.locator(".device-access-status").first).to_contain_text("Video for the test phone")
    page.get_by_role("button", name="Manage device and site access", exact=True).click()
    block = page.get_by_role("button", name="Block Test phone", exact=True)
    block.click()
    # The one-argument question keeps its Continue button, and focus returns to the busy opener
    # as soon as its action ends.
    cancel_with_escape(page, block, verb="Continue", text="Block network access for Test phone?")
    expect(block).to_be_enabled()
    expect(page.locator("#access-device-list")).to_contain_text("Network access allowed")
    block.click()
    page.get_by_role("dialog").get_by_role("button", name="Continue", exact=True).click()
    expect(page.locator("#access-device-list")).to_contain_text("Network access blocked")
    page.get_by_role("button", name="Allow Test phone", exact=True).click()
    page.get_by_role("dialog").get_by_role("button", name="Continue", exact=True).click()
    expect(page.locator("#access-device-list")).to_contain_text("Network access allowed")
    page.get_by_role("button", name="Edit Video for the test phone", exact=True).click()
    page.get_by_label("Domains to block", exact=True).fill("unfinished.example.com")
    # Polling status must not overwrite an unfinished domain list.
    page.locator("#apply-button").click()
    expect(page.locator("#message")).to_contain_text(re.compile(r"^(Started|Started)"))
    expect(page.get_by_label("Domains to block", exact=True)).to_have_value("unfinished.example.com")
    page.set_viewport_size({"width": 320, "height": 740})
    page.get_by_role("link", name="Profiles", exact=True).click()
    page.get_by_text("Randomness, outages and website access", exact=True).click()
    page.emulate_media(color_scheme="light", reduced_motion="reduce")
    assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == "dark"
    for direction in ("Upload", "Download"):
        for field in ("delay", "jitter", "loss", "bandwidth", "reorder", "duplicate", "corrupt"):
            button = page.get_by_role("button", name=f"{direction} {field} help", exact=True)
            button.click()
            tooltip = page.locator("#" + button.get_attribute("aria-controls"))
            expect(tooltip).to_be_visible()
            box = tooltip.bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= 320, field
            page.keyboard.press("Escape")
            expect(tooltip).to_be_hidden()
    button = page.get_by_role("button", name="Upload delay help", exact=True)
    button.focus()
    expect(page.locator("#" + button.get_attribute("aria-controls"))).to_be_visible()
    page.keyboard.press("Tab")
    expect(page.locator("#" + button.get_attribute("aria-controls"))).to_be_hidden()
    layout = page.evaluate(
        """() => {
          const root = document.documentElement;
          const overflow = [...document.querySelectorAll('body *')]
            .map(node => {
              const box = node.getBoundingClientRect();
              const style = getComputedStyle(node);
              return {
                tag: node.tagName,
                id: node.id,
                className: typeof node.className === 'string' ? node.className : '',
                left: Math.round(box.left),
                right: Math.round(box.right),
                width: Math.round(box.width),
                overflowX: style.overflowX,
                parent: node.parentElement?.className || ''
              };
            })
            .filter(node => node.left < -1 || node.right > root.clientWidth + 1)
            .sort((left, right) => right.right - left.right)
            .slice(0, 12);
          return {
            viewport: innerWidth,
            clientWidth: root.clientWidth,
            scrollWidth: root.scrollWidth,
            overflow
          };
        }"""
    )
    assert layout["scrollWidth"] <= 321, f"320px layout overflow: {layout}"
    page.screenshot(path="test-results/access-profiles-phone.png", full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.screenshot(path="test-results/access-profiles-desktop.png", full_page=True)
    page.get_by_role("link", name="Access and rules", exact=True).click()
    page.screenshot(path="test-results/access-rules-desktop.png", full_page=True)
    # Panic works without discarding an unfinished form or another confirmation.
    page.get_by_role("button", name="Normal access", exact=True).click()
    expect(page.locator("#normal-access-notice")).to_be_visible()
    expect(page.get_by_label("Domains to block", exact=True)).to_have_value("unfinished.example.com")
    expect(page.locator("#access-summary")).to_contain_text("disabled")
    page.reload()
    expect(page.locator("#normal-access-notice")).to_be_visible()
    expect(page.locator("#site-rule-list")).to_contain_text("Video for the test phone")
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("link", name=DEVICES_LINK).click()
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path="test-results/access-normal-phone.png")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.screenshot(path="test-results/access-normal-desktop.png")


def run():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"],
        stdout=subprocess.PIPE,
        text=True,
        # Main's fixture always ran a scheduler; the Pi line's fixture makes it opt-in.
        env={**os.environ, "JITTERSIM_BROWSER_TEST_SEED": "1", "JITTERSIM_BROWSER_TEST_SCHEDULER": "1"},
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("browser fixture did not start")
            port = int(process.stdout.readline().strip())
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            errors = []
            release_checks = []
            release_installs = []
            online_checks = []
            preference_requests = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "request",
                lambda request: release_checks.append(request.url)
                if request.method == "GET" and request.url.endswith("/api/updates")
                else None,
            )
            page.on(
                "request",
                lambda request: release_installs.append(request.url)
                if request.method == "POST" and request.url.endswith("/api/updates/install")
                else None,
            )
            page.on(
                "request",
                lambda request: online_checks.append(request.url)
                if request.method == "POST" and request.url.endswith("/api/updates/check-online")
                else None,
            )
            page.on(
                "request",
                lambda request: preference_requests.append(request.method)
                if request.url.endswith("/api/update-preferences")
                else None,
            )
            page.clock.install()
            Path("test-results").mkdir(exist_ok=True)
            try:
                page.goto(f"http://127.0.0.1:{port}")
                # The public health check has no system details. Signed in the
                # health card reads them from the private route.
                public_health = page.request.get(f"http://127.0.0.1:{port}/api/health").json()
                assert "system" not in public_health, public_health
                # Keep periodic status reads from racing the exact request counts.
                page.clock.pause_at(page.evaluate("Date.now() / 1000") + 1)
                unlock(page)
                expect(page.locator("#health-details")).to_contain_text("Version: ")
                expect(page.locator("#health-details")).not_to_contain_text("Version: Unknown")
                expect(page.locator("#update-preference-status")).to_contain_text(
                    "Automatic checking is on."
                )
                expect(page.locator("#release-status")).to_contain_text("candidate-release")
                expect(
                    page.get_by_role("link", name="Updates, update available", exact=True)
                ).to_be_visible()
                assert len(release_checks) == 1, release_checks
                assert not release_installs, release_installs
                page.get_by_role("link", name="Updates, update available", exact=True).click()
                automatic_check = page.locator("#check-staged-updates-on-open")
                expect(automatic_check).to_be_checked()
                automatic_check.uncheck()
                expect(page.locator("#update-preference-status")).to_contain_text(
                    "Automatic checking is off."
                )
                # On-open checks are optional. Visible Updates always reads local status.
                page.get_by_role("link", name=DEVICES_LINK).click()
                page.reload()
                expect(page.locator("#update-preference-status")).to_contain_text(
                    "Automatic checking is off."
                )
                assert len(release_checks) == 1, release_checks
                assert not online_checks, online_checks
                assert not release_installs, release_installs
                page.get_by_role("link", name=UPDATES_LINK).click()
                expect(page.locator("#release-status")).to_contain_text("candidate-release")
                assert len(release_checks) == 2, release_checks
                page.get_by_role("button", name="Check for updates", exact=True).click()
                expect(page.get_by_role("button", name="Check for updates", exact=True)).to_be_enabled()
                expect(page.locator("#release-status")).to_contain_text("candidate-release")
                assert len(release_checks) == 3, release_checks
                automatic_check = page.locator("#check-staged-updates-on-open")
                automatic_check.check()
                expect(automatic_check).to_be_enabled()
                expect(page.locator("#update-preference-status")).to_contain_text(
                    "Automatic checking is on."
                )
                expect(page.locator("#update-available-badge")).to_be_visible()
                assert len(release_checks) == 4, release_checks
                preference_writes = preference_requests.count("POST")
                preference_reads = preference_requests.count("GET")
                page.route(
                    "**/api/update-preferences",
                    lambda route: route.fulfill(
                        status=503,
                        content_type="application/json",
                        body=json.dumps({"error": "preference save unavailable"}),
                    )
                    if route.request.method == "POST"
                    else route.continue_(),
                )
                automatic_check.uncheck()
                expect(automatic_check).to_be_checked()
                expect(automatic_check).to_be_enabled()
                expect(page.locator("#update-preference-status")).to_contain_text(
                    "Automatic checking is on."
                )
                assert preference_requests.count("POST") == preference_writes + 1, preference_requests
                assert preference_requests.count("GET") == preference_reads + 1, preference_requests
                page.unroute("**/api/update-preferences")
                page.get_by_role("link", name=DEVICES_LINK).click()
                preference_read_count = len(release_checks)
                page.route(
                    "**/api/update-preferences",
                    lambda route: route.fulfill(
                        status=503,
                        content_type="application/json",
                        body=json.dumps({"error": "preference read unavailable"}),
                    ),
                )
                page.reload()
                expect(page.locator("#check-staged-updates-on-open")).to_be_disabled()
                expect(page.locator("#update-preference-status")).to_contain_text(
                    "The saved preference is unknown."
                )
                assert len(release_checks) == preference_read_count, release_checks
                assert not online_checks, online_checks
                assert not release_installs, release_installs
                page.get_by_role("link", name=UPDATES_LINK).click()
                expect(page.locator("#release-status")).to_contain_text("candidate-release")
                assert len(release_checks) == preference_read_count + 1, release_checks
                page.get_by_role("button", name="Check for updates", exact=True).click()
                expect(page.get_by_role("button", name="Check for updates", exact=True)).to_be_enabled()
                expect(page.locator("#release-status")).to_contain_text("candidate-release")
                expect(page.locator("#check-staged-updates-on-open")).to_be_disabled()
                expect(page.locator("#update-preference-status")).to_contain_text(
                    "The saved preference is unknown."
                )
                assert len(release_checks) == preference_read_count + 2, release_checks
                page.unroute("**/api/update-preferences")
                page.get_by_role("link", name=DEVICES_LINK).click()
                page.reload()
                expect(page.locator("#update-preference-status")).to_contain_text(
                    "Automatic checking is on."
                )
                expect(page.locator("#check-staged-updates-on-open")).to_be_checked()
                expect(page.locator("#release-status")).to_contain_text("candidate-release")
                assert len(release_checks) == preference_read_count + 3, release_checks
                # The remaining journey exercises ordinary status polling and timers.
                page.clock.resume()
                page.get_by_role("link", name="Access and rules", exact=True).click()
                draft = page.get_by_label("Global blocked domains (comma separated)", exact=True)
                draft.fill("unsaved.example")
                # On a phone Start is in the Controls sheet of the bar at the bottom.
                sidebar_control(page, "#apply-button").click()
                expect(page.locator("#message")).to_contain_text(re.compile(r"^(Started|Started)"))
                expect(draft).to_have_value("unsaved.example")
                close_controls(page)
                page.get_by_role("link", name=DEVICES_LINK).click()
                page.get_by_role("button", name="Open Test phone", exact=True).click()
                page.get_by_role("combobox", name="Temporary profile", exact=True).select_option(
                    "slow"
                )
                page.get_by_role("button", name="Start device timer", exact=True).click()
                expect(page.locator("#workspace-state")).to_contain_text("Slow connection")
                expect(page.locator("#workspace-state")).to_contain_text("Timer")
                expect(page.get_by_text(re.compile(r"^Active: Slow connection"))).to_be_visible()
                page.get_by_role("link", name="Access and rules", exact=True).click()
                expect(draft).to_have_value("unsaved.example")
                page.get_by_role("link", name=DEVICES_LINK).click()
                page.get_by_role("button", name="Stop timer", exact=True).click()
                expect(page.locator("#workspace-state")).to_contain_text("Normal connection")
                page.get_by_role("link", name="Profiles", exact=True).click()
                # The Pi line's rows name their buttons after the profile.
                profile_row(page, "Slow connection").get_by_role(
                    "button", name="Duplicate Slow connection", exact=True
                ).click()
                expect(page.locator("#download-delay")).to_have_value("300")
                expect(page.locator("#profile-id")).to_have_value("slow-copy")
                profile_row(page, "Slow connection").get_by_role(
                    "button", name="Edit Slow connection", exact=True
                ).click()
                page.get_by_role("dialog").get_by_role("button", name="Cancel", exact=True).click()
                expect(page.locator("#profile-id")).to_have_value("slow-copy")
                # The form's heading is a card toggle with the same name.
                page.locator("#profile-form button[type='submit']").click()
                expect(profile_row(page, "Slow connection copy")).to_contain_text("300 ms")
                page.get_by_role("link", name="Access and rules", exact=True).click()
                expect(draft).to_have_value("unsaved.example")
                page.get_by_role("link", name=DEVICES_LINK).click()
                # A failed read retains the confirmed device state and labels it stale.
                page.route(
                    "**/api/conditions",
                    lambda route: route.fulfill(
                        status=503,
                        content_type="application/json",
                        body='{"error":"Test connection failure"}',
                    ),
                )
                expect(page.locator("#connection-notice")).to_contain_text(
                    "out of date", timeout=10000
                )
                expect(page.locator("#workspace-state")).to_contain_text("Normal connection")
                page.unroute("**/api/conditions")
                expect(page.locator("#connection-notice")).to_be_hidden(timeout=10000)
                page.get_by_role("link", name="Measurements", exact=True).click()
                measurement = Path("test-results/measurement.json")
                measurement.write_text(
                    json.dumps(
                        {
                            "format": "jittersim-measurement",
                            "version": 1,
                            "label": "Recovered measurement",
                            "target": "1.1.1.1",
                            "summary": {"sent": 2, "loss_percent": 0, "rtt_avg_ms": 12},
                            "samples": [{"rtt_ms": 10}, {"rtt_ms": 14}],
                        }
                    )
                )
                page.locator("#measurement-files").set_input_files(measurement)
                page.get_by_label("Test name", exact=True).fill("Normal then slow")
                page.get_by_role("button", name="Start test session", exact=True).click()
                expect(page.locator("#test-session-running")).to_contain_text("Recording:")
                page.get_by_role("button", name="Attach displayed measurements", exact=True).click()
                expect(page.locator("#test-session-summary")).to_contain_text("1 measurements")
                page.get_by_label("Test notes", exact=True).fill(
                    "The phone recovered after the timer ended."
                )
                page.get_by_role("button", name="Finish test session", exact=True).click()
                expect(page.locator("#test-session-running")).to_have_text(
                    "No test session recording."
                )
                with page.expect_download() as download:
                    page.get_by_role("button", name="Download test report", exact=True).click()
                report = Path(download.value.path()).read_text()
                assert "The phone recovered" in report and "Settings at the start" in report
                assert "Recovered measurement" in report and "12.00 ms" in report
                page.get_by_role("link", name="Diagnostics", exact=True).click()
                versions = page.locator("#settings-versions")
                versions.select_option(index=versions.locator("option").count() - 1)
                page.get_by_role("button", name="Preview changes", exact=True).click()
                expect(page.locator("#settings-preview")).to_contain_text("profiles")
                page.get_by_role("button", name="Restore this version", exact=True).click()
                page.get_by_role("button", name="Continue", exact=True).click()
                expect(page.locator("#message")).to_contain_text("Settings restored.")
                page.get_by_role("link", name="Profiles", exact=True).click()
                expect(profile_row(page, "Slow connection copy")).to_have_count(0)
                check_access_and_help(page)
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("link", name=ROUTER_LINK).click()
                expect(page.locator("#gateway-persistence-state")).to_contain_text(
                    "Router reachable."
                )
                page.locator("#gateway-persistence-toggle").check()
                page.get_by_role("button", name="Save outage behavior", exact=True).click()
                expect(page.locator("#gateway-persistence-state")).to_contain_text(
                    "Saved and applied to the networking helper."
                )
                dismiss_toasts(page)
                page.locator(
                    "#page-router section", has=page.locator("#gateway-persistence-save")
                ).first.screenshot(path="test-results/router-outage-persistence-desktop.png")
                page.get_by_role("link", name=UPDATES_LINK).click()
                page.get_by_role("button", name="Check for updates", exact=True).click()
                expect(page.locator("#release-status")).to_contain_text("test-release")
                expect(page.locator("#release-status")).to_contain_text(
                    "candidate-release"
                )
                dismiss_toasts(page)
                page.locator("#release-controls").screenshot(
                    path="test-results/staged-update-check-desktop.png"
                )
                page.route(
                    "**/api/updates",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "phase": "idle",
                                "installed": "test-release",
                                "available": "test-release",
                                "rollback_available": False,
                                "message": "No different release is staged.",
                            }
                        ),
                    ),
                )
                page.get_by_role("button", name="Check for updates", exact=True).click()
                expect(page.locator("#update-available-badge")).to_be_hidden()
                expect(page.locator("#release-install")).to_be_disabled()
                page.unroute("**/api/updates")
                page.route(
                    "**/api/updates",
                    lambda route: route.fulfill(
                        status=503,
                        content_type="application/json",
                        body=json.dumps({"error": "release status unavailable"}),
                    ),
                )
                page.get_by_role("button", name="Check for updates", exact=True).click()
                expect(page.locator("#release-status")).to_contain_text(
                    "Staged release status is unknown."
                )
                expect(page.get_by_role("button", name="Check for updates")).to_be_enabled()
                page.unroute("**/api/updates")
                page.route(
                    "**/api/updates",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "phase": "idle",
                                "installed": None,
                                "available": None,
                                "rollback_available": False,
                                "message": "No release is staged.",
                            }
                        ),
                    ),
                )
                page.get_by_role("button", name="Check for updates", exact=True).click()
                expect(page.locator("#release-status")).to_contain_text(
                    "Installed: test-release · Available: None staged"
                )
                expect(page.locator("#update-available-badge")).to_be_hidden()
                expect(page.locator("#release-install")).to_be_disabled()
                page.unroute("**/api/updates")
                release_operation = {"phase": "installing"}

                def release_progress(route):
                    phase = release_operation["phase"]
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "phase": phase,
                                "request_id": "a" * 32,
                                "request_kind": "install",
                                "installed": "candidate-release" if phase == "complete" else "test-release",
                                "available": "candidate-release",
                                "rollback_available": True,
                                "message": "Update " + phase + ".",
                            }
                        ),
                    )

                page.route("**/api/updates", release_progress)
                page.get_by_role("button", name="Check for updates", exact=True).click()
                expect(page.locator("#release-status")).to_contain_text("Update installing.")
                expect(page.locator("#release-install")).to_be_disabled()
                # Updates follows the same operation until the installed version confirms it.
                release_operation["phase"] = "complete"
                expect(page.locator("#release-status")).to_contain_text(
                    "Update complete.", timeout=15000
                )
                expect(page.locator("#update-progress")).to_have_text("Updated to candidate-release.")
                expect(page.locator("#update-installed")).to_have_text("candidate-release")
                expect(page.locator("#release-rollback")).to_be_enabled()
                assert not release_installs, release_installs
                page.unroute("**/api/updates")
                page.set_viewport_size({"width": 320, "height": 740})
                for tab in (
                    DEVICES_LINK,
                    "Profiles",
                    "Schedules and limits",
                    ROUTER_LINK,
                    "Traffic",
                    "Measurements",
                    "Activity",
                    "Access and rules",
                    "Diagnostics",
                ):
                    page.get_by_role("link", name=tab, exact=isinstance(tab, str)).click()
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
                    ), tab
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("link", name=DEVICES_LINK).click()
                discard = page.get_by_role("button", name="Discard unfinished edits", exact=True)
                if discard.is_visible():
                    discard.click()
                    page.get_by_role("dialog").get_by_role(
                        "button", name="Continue", exact=True
                    ).click()
                page.locator("#apply-button").click()
                expect(page.locator("#message")).to_contain_text(
                    re.compile(r"^(Started|Started)")
                )
                page.set_viewport_size({"width": 390, "height": 844})
                page.get_by_role("link", name=DEVICES_LINK).click()
                page.get_by_role("button", name="Open Test phone", exact=True).click()
                page.get_by_role("combobox", name="Temporary profile", exact=True).select_option(
                    "slow"
                )
                page.get_by_role("button", name="Start device timer", exact=True).click()
                expect(page.locator("#workspace-state")).to_contain_text("Slow connection")
                expect(page.get_by_text(re.compile(r"^Active: Slow connection"))).to_be_visible()
                dismiss_toasts(page)
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(
                    path="test-results/experience-overview-desktop.png", full_page=False
                )
                page.set_viewport_size({"width": 430, "height": 1200})
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(
                    path="test-results/experience-overview-phone.png", full_page=False
                )
                check_service_list_contexts(browser, f"http://127.0.0.1:{port}/")
                check_wifi_recovery(page)
                check_destructive_confirms(page)
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.screenshot(path="test-results/wifi-recovery-desktop.png", full_page=True)
                page.set_viewport_size({"width": 430, "height": 1000})
                page.screenshot(path="test-results/wifi-recovery-phone.png", full_page=True)
                assert not errors, errors
            finally:
                page.screenshot(path="test-results/browser-journeys.png", full_page=True)
                context.tracing.stop(path="test-results/browser-journeys.zip")
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
