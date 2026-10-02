"""Check that the Apply button says what it does and whether JitterSim is running."""

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
PROFILE_NAME = "Fixture slow"
HEADERS = {"Authorization": "Bearer browser-test"}
OLD_PHRASES = (
    "Apply current configuration",
    "Apply the configuration",
    "No network action has run yet",
    "Configuration applied successfully",
)

NOT_RUNNING = "JitterSim is not running. Choose Start with saved settings."
OFF = "Off. No traffic shaping is running."
RUNNING = "Running. Your saved settings are applied. New changes apply on their own."
CHANGE_PENDING = "Running. Your latest change applies on its own."
RESET_FAILED = "The last reset failed. The Pi may still be shaping. Check it."
NEEDS_HELPER = "Needs the Pi helper to run."
SAVED_DEVICE = "Saved: 1 device with a profile"
NOTHING_SAVED = "No device profiles or service lists saved yet"
NONE_TO_SHAPE = "No device profiles or service lists are saved yet."
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


def tip_box(icon):
    """Measure the help tip of an icon. The tip is a pseudo-element so its box comes from
    the computed style that a fixed element resolves to pixels in the window."""

    return icon.evaluate(
        """(icon) => {
            const style = getComputedStyle(icon, '::after');
            return {
                shown: style.display !== 'none' && style.visibility === 'visible',
                left: parseFloat(style.left),
                top: parseFloat(style.top),
                width: parseFloat(style.width),
                height: parseFloat(style.height),
            };
        }"""
    )


def assert_tip_inside_window(page, icon, what):
    box = tip_box(icon)
    size = page.viewport_size
    assert box["shown"], (what, box)
    assert box["left"] >= 0 and box["top"] >= 0, (what, "starts outside the window", box, size)
    assert box["left"] + box["width"] <= size["width"] + 0.5, (what, "runs past the right", box)
    assert box["top"] + box["height"] <= size["height"] + 0.5, (what, "runs past the bottom", box)
    sidebar = page.locator(".sidebar").bounding_box()
    if size["width"] > 768:
        # next to the sidebar and not over its buttons
        assert box["left"] >= sidebar["x"] + sidebar["width"], (what, box, sidebar)
    return box


def footer_geometry(page):
    """Where the buttons are so that a changed label can be checked for a jump."""

    return page.evaluate(
        """() => {
            const apply = document.getElementById('apply-button').getBoundingClientRect();
            const reset = document.getElementById('reset-button').getBoundingClientRect();
            // The footer sits at the bottom and the status text below it changes length. So the
            // jump of a label shows in the button height and in the gap to the next button.
            return {applyHeight: apply.height, gapToReset: reset.top - apply.top};
        }"""
    )


# A wider face and a larger root size stand in for the fonts of another machine. A layout that
# only works with the fonts of the machine it was written on fails under this.
WIDE_FONTS = (
    "html { font-size: 112.5% !important; }"
    " * { font-family: Verdana, 'DejaVu Sans', sans-serif !important; }"
)
STRESSES = (("normal fonts", ""), ("wide fonts", WIDE_FONTS))
DESKTOP_SIZES = ((1280, 700), (1280, 721), (1280, 760), (1280, 800), (1920, 1000))
LONG_MESSAGE = (
    "Started: 12 devices with a profile · 3 groups · 2 service lists · 4 protected devices stay"
    " untouched. Profiles were skipped for devices whose current Wi-Fi identity could not be"
    " verified: living room tv and kitchen tablet and garden camera."
)


def set_font_stress(page, css):
    page.evaluate(
        """(css) => {
            let node = document.getElementById('font-stress');
            if (!css) { if (node) node.remove(); return; }
            if (!node) {
                node = document.createElement('style');
                node.id = 'font-stress';
                document.head.appendChild(node);
            }
            node.textContent = css;
        }""",
        css,
    )


def footer_boxes(page):
    return page.evaluate(
        """() => {
            const box = (selector) => document.querySelector(selector).getBoundingClientRect();
            const sidebar = document.querySelector('.sidebar');
            const scroll = box('.sidebar-scroll');
            const footer = box('.sidebar-footer');
            const panic = box('#panic-button');
            return {
                windowHeight: window.innerHeight,
                panicTop: panic.top, panicBottom: panic.bottom,
                footerTop: footer.top, footerBottom: footer.bottom,
                scrollBottom: scroll.bottom, scrollHeight: scroll.height,
                sidebarOverflow: sidebar.scrollHeight - sidebar.clientHeight,
            };
        }"""
    )


def assert_footer_pinned(page, what):
    """Panic is the control that must stay reachable so it may not hang below the window.

    The buttons that act on the network are pinned under the part of the sidebar that scrolls.
    So the sidebar itself never scrolls and nothing of the footer is out of the window.
    """

    box = footer_boxes(page)
    bottom = box["windowHeight"] + 0.5
    assert box["panicTop"] >= 0, (what, "Panic starts above the window", box)
    assert box["panicBottom"] <= bottom, (what, "Panic is cut off", box)
    assert box["footerBottom"] <= bottom, (what, "the footer is cut off", box)
    assert box["scrollBottom"] <= box["footerTop"] + 1, (what, "the scroll area runs under it", box)
    assert box["sidebarOverflow"] <= 1, (what, "the whole sidebar scrolls", box)
    assert box["scrollHeight"] >= 40, (what, "no room is left for the navigation", box)
    return box


def assert_footer_pinned_everywhere(page, state):
    """Check every window height with the normal fonts and with a wider face."""

    start = page.viewport_size
    for stress, css in STRESSES:
        set_font_stress(page, css)
        for width, height in DESKTOP_SIZES:
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(60)
            box = assert_footer_pinned(page, f"{state} with {stress} at {width}x{height}")
            if height == 700:
                print(f"pinned footer, {state}, {stress}, 1280x700: Panic bottom "
                      f"{box['panicBottom']:.1f} of 700, navigation area {box['scrollHeight']:.0f}")
    set_font_stress(page, "")
    page.set_viewport_size(start)


def check_tips(browser):
    """Hover and focus both help icons on short and tall windows and on a phone."""

    process, port = start_fixture()
    try:
        base = f"http://127.0.0.1:{port}"
        context = browser.new_context(viewport={"width": 1280, "height": 560})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base)
        page.locator("#open-admin-credentials").click()
        page.locator("#admin-token").fill("browser-test")
        page.locator("#check-admin-token").click()
        expect(page.locator("#private-dashboard")).to_be_visible()
        page.locator("#remember-admin-token").check()
        page.locator("#close-credentials").click()
        apply_icon = page.locator("#apply-button + .help-icon")
        reset_icon = page.locator("#reset-button + .help-icon")
        sizes = ((1280, 560), (1280, 700), (1280, 721), (1280, 800), (390, 844), (1920, 1000))
        for width, height in sizes:
            page.set_viewport_size({"width": width, "height": height})
            page.reload()
            wait_for_automatic_token_check(page)
            expect(page.locator("#apply-summary")).to_have_text(NOTHING_SAVED)
            apply_icon.hover()
            box = assert_tip_inside_window(page, apply_icon, f"apply tip at {width}x{height}")
            page.wait_for_timeout(200)
            page.screenshot(path=f"test-results/apply-clarity-tip-{width}x{height}.png")
            page.mouse.move(width // 2, 5)
            page.locator("#reset-button").focus()
            page.keyboard.press("Tab")
            expect(reset_icon).to_be_focused()
            assert_tip_inside_window(page, reset_icon, f"reset tip at {width}x{height}")
            print(f"tips at {width}x{height}: apply tip {box}")

        # A start with nothing to shape must not list what is not there.
        page.set_viewport_size({"width": 1280, "height": 800})
        page.reload()
        wait_for_automatic_token_check(page)
        apply_button = page.locator("#apply-button")
        message = page.locator("#message")
        summary = page.locator("#apply-summary")

        def post(path, data):
            response = page.request.post(base + path, headers=HEADERS, data=data)
            assert response.ok, (path, response.status, response.text())

        apply_button.click()
        expect(message).to_have_text("Started. " + NONE_TO_SHAPE)
        # Start also enforces the access rules so the words and the line mention them.
        post("/api/policies", {"websites": {"mode": "unrestricted",
                                            "blocked_domains": ["blocked.invalid"]}})
        page.reload()
        wait_for_automatic_token_check(page)
        expect(summary).to_have_text(NOTHING_SAVED + ". Access rules are saved too.")
        expect(apply_button).to_have_text(REAPPLY)
        apply_button.click()
        expect(message).to_have_text(
            "Re-applied. " + NONE_TO_SHAPE + " Access rules apply."
        )
        # a protected device alone is not something that is shaped
        post("/api/unaffected-devices", {"mac_address": MAC_ADDRESS, "display_name": "Mine"})
        page.reload()
        wait_for_automatic_token_check(page)
        expect(summary).to_have_text("Saved: 1 protected device stays untouched")
        apply_button.click()
        expect(message).to_have_text(
            "Re-applied. 1 protected device stays untouched. " + NONE_TO_SHAPE
            + " Access rules apply."
        )
        assert not errors, errors
        context.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def check_pinned_footer(browser):
    """Panic stays fully in the window whatever the text and the fonts are.

    The states cover a long and a short label on the Apply button and a long and a short
    status and message. Each is measured with the normal fonts and with a wider face.
    """

    process, port = start_fixture()
    try:
        base = f"http://127.0.0.1:{port}"
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        runtime = page.locator("#runtime-summary")

        def post(path, data):
            response = page.request.post(base + path, headers=HEADERS, data=data)
            assert response.ok, (path, response.status, response.text())

        page.goto(base)
        page.locator("#open-admin-credentials").click()
        page.locator("#admin-token").fill("browser-test")
        page.locator("#check-admin-token").click()
        expect(page.locator("#private-dashboard")).to_be_visible()
        page.locator("#remember-admin-token").check()
        page.locator("#close-credentials").click()

        # Not running: the long two line label on the button and a longer status.
        expect(runtime).to_have_text(NOT_RUNNING)
        expect(page.locator("#apply-button")).to_have_text(START)
        assert_footer_pinned_everywhere(page, "not running")

        # Off after a reset: the shortest status and message.
        page.locator("#reset-button").click()
        expect(runtime).to_have_text(OFF)
        assert_footer_pinned_everywhere(page, "off")

        # Running with a lot saved: the short label and the longest status and summary.
        post("/api/profiles", {"id": "slow", "name": PROFILE_NAME, "upload": {}, "download": {}})
        post("/api/devices", {"id": "fixture-laptop", "name": "Fixture laptop",
                              "ipv4_address": IP_ADDRESS, "profile_id": "slow",
                              "mac_address": MAC_ADDRESS})
        post("/api/general-profile", {"profile_id": "slow"})
        post("/api/unaffected-devices", {"mac_address": MAC_ADDRESS, "display_name": "Mine"})
        post("/api/policies", {"websites": {"mode": "unrestricted",
                                            "blocked_domains": ["blocked.invalid"]}})
        page.reload()
        wait_for_automatic_token_check(page)
        page.locator("#apply-button").click()
        expect(runtime).to_have_text(RUNNING)
        expect(page.locator("#apply-button")).to_have_text(REAPPLY)
        expect(page.locator("#apply-summary")).to_contain_text("Saved: General profile")
        assert_footer_pinned_everywhere(page, "running")

        # The same with a message that fills several lines.
        page.evaluate("(text) => showMessage(text, 'success')", LONG_MESSAGE)
        assert_footer_pinned_everywhere(page, "running with a long message")

        # A failed reset has the longest status text.
        failed = page.request.get(base + "/api/state", headers=HEADERS).json()
        failed["state"].update(observed_state="error", running=False, last_action="reset")
        failed_route = lambda route: route.fulfill(json=failed)  # noqa: E731
        page.route("**/api/state", failed_route)
        page.reload()
        wait_for_automatic_token_check(page)
        expect(runtime).to_have_text(RESET_FAILED)
        page.evaluate("(text) => showMessage(text, 'error')", LONG_MESSAGE)
        assert_footer_pinned_everywhere(page, "failed reset with a long message")
        page.unroute("**/api/state", failed_route)

        # The navigation still works when only a few links fit. The last link scrolls into
        # view inside its own area and not under the buttons.
        page.reload()
        wait_for_automatic_token_check(page)
        set_font_stress(page, WIDE_FONTS)
        page.set_viewport_size({"width": 1280, "height": 700})
        page.locator("a[data-page-link='diagnostics']").click()
        expect(page).to_have_url(re.compile(r"#diagnostics$"))
        link = page.locator("a[data-page-link='diagnostics']").bounding_box()
        footer = footer_boxes(page)
        assert link["y"] + link["height"] <= footer["footerTop"] + 1, (link, footer)
        assert not errors, errors
        context.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


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
        expect(runtime).to_have_text(NOT_RUNNING)
        expect(apply_button).to_have_text(START)
        expect(summary).to_have_text(SAVED_DEVICE)
        assert_old_wording_is_gone(page)
        shoot("not-started")

        # Reset network switches it off
        page.locator("#reset-button").click()
        expect(runtime).to_have_text(OFF)
        expect(apply_button).to_have_text(START)
        expect(summary).to_have_text(SAVED_DEVICE)
        assert_old_wording_is_gone(page)
        stopped_geometry = footer_geometry(page)

        # the help text beside each button opens on hover and on keyboard focus
        apply_icon = page.locator("#apply-button + .help-icon")
        reset_icon = page.locator("#reset-button + .help-icon")
        expect(apply_icon).to_have_attribute("aria-label", "Start or re-apply saved settings help")
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
        expect(message).to_contain_text("Started: 1 device with a profile.")
        expect(runtime).to_have_text(RUNNING)
        expect(apply_button).to_have_text(REAPPLY)
        expect(summary).to_have_text(SAVED_DEVICE)
        assert_old_wording_is_gone(page)
        shoot("running")

        # The label changes between one and two lines in the narrow sidebar. The button and the
        # buttons below it must keep their size and spacing so that the footer does not jump.
        running_geometry = footer_geometry(page)
        assert running_geometry == stopped_geometry, (stopped_geometry, running_geometry)

        # applying again while it runs
        apply_button.click()
        expect(message).to_contain_text("Re-applied: 1 device with a profile.")
        expect(runtime).to_have_text(RUNNING)
        expect(apply_button).to_have_text(REAPPLY)

        # A saved change that leaves the plan as it was never writes "applied" again. The
        # scheduler really runs here so this is what a person sees for as long as it runs.
        post("/api/profiles", {"id": "unused", "name": "Unused", "upload": {}, "download": {}})
        page.wait_for_timeout(2500)  # more than two scheduler ticks
        state = page.request.get(base + "/api/state", headers=HEADERS).json()["state"]
        assert state["observed_state"] == "unknown" and state["running"] is True, state
        page.reload()
        wait_for_automatic_token_check(page)
        expect(runtime).to_have_text(CHANGE_PENDING)
        expect(apply_button).to_have_text(REAPPLY)
        expect(summary).to_have_text(SAVED_DEVICE)
        # A change that alters the plan is applied by the scheduler on its own.
        post(
            "/api/profiles",
            {
                "id": "slow",
                "name": PROFILE_NAME,
                "upload": {"bandwidth_kbit": 1500},
                "download": {"bandwidth_kbit": 2500},
            },
        )
        expect(runtime).to_have_text(RUNNING, timeout=15000)

        # a protected client is counted as untouched and no longer as a device
        post("/api/unaffected-devices", {"mac_address": MAC_ADDRESS, "display_name": "Mine"})
        page.reload()
        wait_for_automatic_token_check(page)
        expect(summary).to_have_text("Saved: 1 protected device stays untouched")

        # a general profile is named instead of counting devices
        post("/api/general-profile", {"profile_id": "slow"})
        page.reload()
        wait_for_automatic_token_check(page)
        expect(summary).to_have_text(
            f'Saved: General profile "{PROFILE_NAME}" for every unprotected device'
            " · 1 protected device stays untouched"
        )
        expect(runtime).to_have_text(RUNNING)
        shoot("general-profile")

        # The live region is read out each time its text is written. A poll that finds
        # nothing new must not write it again.
        page.evaluate(
            """() => {
                window.writes = [];
                const watch = new MutationObserver((records) => {
                    records.forEach((record) => window.writes.push(record.target.id || '?'));
                });
                for (const id of ['apply-summary', 'apply-button', 'runtime-summary']) {
                    watch.observe(document.getElementById(id), {
                        childList: true, characterData: true, subtree: true, attributes: true,
                    });
                }
            }"""
        )
        page.wait_for_timeout(5600)  # one poll of the live state
        assert page.evaluate("window.writes") == [], page.evaluate("window.writes")

        # A failed Reset or Panic pauses the scheduler first and then asks the Pi. The fixture
        # cannot make the Pi refuse a reset so the answer of the state is faked for this one.
        failed = page.request.get(base + "/api/state", headers=HEADERS).json()
        failed["state"].update(observed_state="error", running=False, last_action="reset")
        failed_route = lambda route: route.fulfill(json=failed)  # noqa: E731
        page.route("**/api/state", failed_route)
        page.reload()
        wait_for_automatic_token_check(page)
        expect(runtime).to_have_text(RESET_FAILED)
        expect(apply_button).to_have_text(START)
        expect(page.locator("#status-devices-plain")).to_have_text(
            "Last reset failed. The Pi may still be shaping."
        )
        page.unroute("**/api/state", failed_route)

        # Without the Pi helper nothing can run. The health answer is faked because the
        # fixture always has a helper.
        no_helper = {
            "status": "ok", "mode": "configuration-only",
            "helper_configured": False, "helper_reachable": False,
        }
        helper_route = lambda route: route.fulfill(json=no_helper)  # noqa: E731
        page.route("**/api/health", helper_route)
        page.reload()
        wait_for_automatic_token_check(page)
        expect(runtime).to_have_text(NEEDS_HELPER)
        expect(apply_button).to_be_disabled()
        expect(summary).to_contain_text("Saved: ")
        page.unroute("**/api/health", helper_route)
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
        expect(summary).to_contain_text('Saved: General profile "')
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
            check_tips(browser)
            check_pinned_footer(browser)
            for width, height in ((1920, 1000), (390, 844)):
                check_width(browser, width, height)
            print("Apply clarity acceptance passed at desktop and phone widths.")
        finally:
            browser.close()


if __name__ == "__main__":
    run()
