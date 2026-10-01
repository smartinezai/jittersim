"""Check that the Apply button says what it does and whether JitterSim is running."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

MAC_ADDRESS = "02:00:00:00:00:01"
IP_ADDRESS = "192.168.50.184"
PROFILE_NAME = "Fixture slow"
HEADERS = {"Authorization": "Bearer browser-test"}
OLD_PHRASES = (
    "Apply current configuration",
    "Apply the configuration",
    "No network action has run yet",
    "Configuration applied successfully",
)

NOT_STARTED = "Not started. Choose Start with saved settings."
OFF = "Off. No traffic shaping is running."
RUNNING = "Running. Your saved settings are applied. New changes apply on their own."
START = "Start with saved settings"
REAPPLY = "Re-apply saved settings"
APPLY_HELP = (
    "Starts JitterSim with everything you saved: device and group profiles and schedules"
    " and limits and service lists and access rules."
)
RESET_HELP = "Stops JitterSim and removes every rule from the Pi so all devices run normally."


def start_fixture():
    # a scheduler like a real install so that an Apply keeps the network running
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "JITTERSIM_BROWSER_TEST_SCHEDULER": "1"},
    )
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=15):
            process.terminate()
            raise RuntimeError("Apply clarity dashboard fixture did not start")
        return process, int(process.stdout.readline().strip())


def wait_for_automatic_token_check(page):
    expect(page.locator("#message")).to_contain_text("Token accepted. Diagnostics loaded.")


def assert_old_wording_is_gone(page):
    text = page.locator("body").inner_text()
    html = page.content()
    for phrase in OLD_PHRASES:
        assert phrase not in text, (phrase, "is visible")
        assert phrase not in html, (phrase, "is in the page")


def tip_of(page, locator):
    """Read the help text a help icon shows from its pseudo-element."""

    return locator.evaluate(
        """(icon) => {
            const style = getComputedStyle(icon, '::after');
            return {
                shown: style.display !== 'none' && style.visibility === 'visible'
                    && Number(style.opacity) > 0,
                text: style.content.replace(/^"|"$/g, ''),
            };
        }"""
    )


def check_width(browser, width, height):
    # a fresh fixture for each width so that each run starts before the first Apply
    process, port = start_fixture()
    try:
        base = f"http://127.0.0.1:{port}"
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        sidebar = page.locator(".sidebar")
        apply_button = page.locator("#apply-button")
        runtime = page.locator("#runtime-summary")
        summary = page.locator("#apply-summary")
        message = page.locator("#message")

        def post(path, data):
            response = page.request.post(base + path, headers=HEADERS, data=data)
            assert response.ok, (path, response.status, response.text())

        def shoot(name):
            page.wait_for_timeout(300)
            sidebar.screenshot(path=f"test-results/apply-clarity-{name}-{width}.png")

        page.goto(base)
        page.locator("#open-admin-credentials").click()
        page.locator("#admin-token").fill("browser-test")
        page.locator("#check-admin-token").click()
        expect(page.locator("#private-dashboard")).to_be_visible()
        page.locator("#remember-admin-token").check()
        page.locator("#close-credentials").click()

        # a saved profile and one saved device without starting anything
        post(
            "/api/profiles",
            {
                "id": "slow",
                "name": PROFILE_NAME,
                "upload": {"bandwidth_kbit": 1000},
                "download": {"bandwidth_kbit": 2000},
            },
        )
        post(
            "/api/devices",
            {
                "id": "fixture-laptop",
                "name": "Fixture laptop",
                "ipv4_address": IP_ADDRESS,
                "profile_id": "slow",
                "mac_address": MAC_ADDRESS,
            },
        )
        page.reload()
        wait_for_automatic_token_check(page)

        # nothing started yet
        expect(runtime).to_have_text(NOT_STARTED)
        expect(apply_button).to_have_text(START)
        expect(summary).to_have_text("Will run: 1 device with a profile")
        assert_old_wording_is_gone(page)
        shoot("not-started")

        # Reset network switches it off
        page.locator("#reset-button").click()
        expect(runtime).to_have_text(OFF)
        expect(apply_button).to_have_text(START)
        expect(summary).to_have_text("Will run: 1 device with a profile")
        assert_old_wording_is_gone(page)

        # the help text beside each button opens on hover and on keyboard focus
        apply_icon = page.locator("#apply-button + .help-icon")
        reset_icon = page.locator("#reset-button + .help-icon")
        expect(apply_icon).to_have_attribute("aria-label", "Start with saved settings help")
        assert not tip_of(page, apply_icon)["shown"]
        apply_icon.hover()
        tip = tip_of(page, apply_icon)
        assert tip["shown"] and tip["text"].startswith(APPLY_HELP), tip
        assert "Reset or Panic stops it." in tip["text"], tip
        page.wait_for_timeout(300)
        page.screenshot(path=f"test-results/apply-clarity-help-{width}.png")
        page.mouse.move(width // 2, 5)
        assert not tip_of(page, apply_icon)["shown"]
        page.locator("#reset-button").focus()
        page.keyboard.press("Tab")  # keyboard focus is what opens the tip
        expect(reset_icon).to_be_focused()
        tip = tip_of(page, reset_icon)
        assert tip["shown"] and tip["text"].startswith(RESET_HELP), tip
        assert "Choose Start with saved settings to run them again." in tip["text"], tip
        page.keyboard.press("Tab")
        assert not tip_of(page, reset_icon)["shown"]

        # Start with saved settings
        apply_button.click()
        expect(message).to_contain_text("Started: 1 device with a profile")
        expect(runtime).to_have_text(RUNNING)
        expect(apply_button).to_have_text(REAPPLY)
        expect(summary).to_have_text("Running: 1 device with a profile")
        assert_old_wording_is_gone(page)
        shoot("running")

        # applying again while it runs
        apply_button.click()
        expect(message).to_contain_text("Re-applied: 1 device with a profile")
        expect(runtime).to_have_text(RUNNING)
        expect(apply_button).to_have_text(REAPPLY)

        # a protected client is counted as untouched and no longer as a device
        post("/api/unaffected-devices", {"mac_address": MAC_ADDRESS, "display_name": "Mine"})
        page.reload()
        wait_for_automatic_token_check(page)
        expect(summary).to_have_text("Running: 1 protected device stays untouched")

        # a general profile is named instead of counting devices
        post("/api/general-profile", {"profile_id": "slow"})
        page.reload()
        wait_for_automatic_token_check(page)
        expect(summary).to_have_text(
            f'Running: Applies the general profile "{PROFILE_NAME}" to every device that is'
            " not protected · 1 protected device stays untouched"
        )
        expect(runtime).to_have_text(RUNNING)
        shoot("general-profile")

        # A saved change sets the state to unknown for about a second while the scheduler runs.
        # That second must read as a change being applied and not as a network that never started.
        state_body = page.evaluate("request('/api/state', 'GET')")
        assert state_body["state"]["running"] is True, state_body
        state_body["state"]["observed_state"] = "unknown"
        state_route = lambda route: route.fulfill(json=state_body)  # noqa: E731
        page.route("**/api/state", state_route)
        page.reload()
        wait_for_automatic_token_check(page)
        expect(runtime).to_have_text("Applying your latest change…")
        expect(apply_button).to_have_text(REAPPLY)
        expect(summary).to_contain_text("Running: Applies the general profile")
        page.unroute("**/api/state", state_route)
        page.reload()
        wait_for_automatic_token_check(page)
        expect(runtime).to_have_text(RUNNING)

        # Panic stops it again and says how to run it
        page.locator("#panic-button").click()
        expect(message).to_contain_text(
            "All profiles are off. Choose Start with saved settings to resume."
        )
        expect(runtime).to_have_text(OFF)
        expect(apply_button).to_have_text(START)
        expect(summary).to_contain_text("Will run: Applies the general profile")
        assert_old_wording_is_gone(page)
        shoot("off")

        # a locked dashboard keeps nothing private under the button
        page.locator("#open-admin-credentials").click()
        page.locator("#clear-admin-token").click()
        expect(summary).to_be_hidden()
        expect(summary).to_have_text("")
        expect(apply_button).to_have_text(START)

        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
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
            for width, height in ((1920, 1000), (390, 844)):
                check_width(browser, width, height)
            print("Apply clarity acceptance passed at desktop and phone widths.")
        finally:
            browser.close()


if __name__ == "__main__":
    run()
