"""Exercise complete page behavior against the real API in CI."""

import json
import re
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def run():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("browser fixture did not start")
            port = int(process.stdout.readline().strip())
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            Path("test-results").mkdir(exist_ok=True)
            try:
                page.goto(f"http://127.0.0.1:{port}")
                page.get_by_label("Token", exact=True).fill("browser-test")
                page.get_by_role("button", name="Sign in", exact=True).click()
                expect(page.get_by_text("Signed in.", exact=True)).to_be_visible()
                page.get_by_role("link", name="Rules & limits", exact=True).click()
                page.get_by_role("button", name="Access rules", exact=True).click()
                draft = page.get_by_label("Global blocked domains (comma separated)", exact=True)
                draft.fill("unsaved.example")
                page.get_by_role("button", name="Apply current configuration", exact=True).click()
                expect(page.locator("#message")).to_have_text("Configuration applied successfully.")
                expect(draft).to_have_value("unsaved.example")
                page.get_by_role("link", name="Devices", exact=True).click()
                page.get_by_role("button", name="Open Test phone", exact=True).click()
                page.get_by_role("combobox", name="Temporary profile", exact=True).select_option(
                    "slow"
                )
                page.get_by_role("button", name="Start device timer", exact=True).click()
                expect(page.locator("#workspace-state")).to_contain_text("Slow connection")
                expect(page.locator("#workspace-state")).to_contain_text("Timer")
                page.get_by_role("link", name="Overview", exact=True).click()
                expect(page.get_by_text(re.compile(r"^Active: Slow connection"))).to_be_visible()
                page.get_by_role("link", name="Rules & limits", exact=True).click()
                expect(draft).to_have_value("unsaved.example")
                page.get_by_role("link", name="Devices", exact=True).click()
                page.get_by_role("button", name="Stop timer", exact=True).click()
                expect(page.locator("#workspace-state")).to_contain_text("Normal connection")
                page.get_by_role("link", name="Profiles", exact=True).click()
                page.get_by_role("row").filter(has_text="Slow connection").get_by_role(
                    "button", name="Duplicate", exact=True
                ).click()
                expect(page.locator("#download-delay")).to_have_value("300")
                expect(page.locator("#profile-id")).to_have_value("slow-copy")
                page.get_by_role("row").filter(has_text="Slow connection").get_by_role(
                    "button", name="Edit", exact=True
                ).click()
                page.get_by_role("dialog").get_by_role("button", name="Cancel", exact=True).click()
                expect(page.locator("#profile-id")).to_have_value("slow-copy")
                page.get_by_role("button", name="Save profile", exact=True).click()
                expect(
                    page.get_by_role("row").filter(has_text="Slow connection copy")
                ).to_contain_text("300 ms")
                page.get_by_role("link", name="Rules & limits", exact=True).click()
                expect(draft).to_have_value("unsaved.example")
                page.get_by_role("link", name="Devices", exact=True).click()
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
                expect(
                    page.get_by_role("row").filter(has_text="Slow connection copy")
                ).to_have_count(0)
                page.set_viewport_size({"width": 320, "height": 740})
                for tab in (
                    "Overview",
                    "Devices",
                    "Profiles",
                    "Rules & limits",
                    "Measurements",
                    "Setup",
                    "Diagnostics",
                ):
                    page.get_by_role("link", name=tab, exact=True).click()
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
                    ), tab
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
