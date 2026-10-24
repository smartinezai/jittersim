"""Navigate dashboard pages and keep emergency actions reachable in every layout."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.diagnostics_keyboard import check_diagnostics_keyboard
from tests.browser.draft_navigation import check_draft_navigation
from tests.browser.phone import assert_router_status_is_read_only, close_controls, open_controls

PAGES = (
    "devices", "profiles", "schedules", "router", "traffic", "measurements", "activity",
    "access", "diagnostics", "updates",
)


def unlock(page):
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill("browser-test")
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    page.locator("#remember-admin-token").check()
    # The token is stored once it is accepted. A reload before that forgets it.
    expect(page.locator("#remember-admin-token-note")).to_contain_text(
        "Remembered until", timeout=20000
    )
    page.locator("#close-credentials").click()
    assert page.evaluate("""() => {
        const active = document.activeElement;
        return !!active && !!active.getClientRects().length && !active.disabled;
    }"""), "Closing initial credentials must return focus to a visible control"
    if page.locator("#open-admin-credentials").is_hidden():
        expect(page.locator("#controls-toggle")).to_be_focused()


def wait_for_remembered_token(page):
    """After a reload the page checks the remembered token again before it unlocks."""

    expect(page.locator("#admin-credential-state")).to_have_text("Accepted", timeout=20000)
    expect(page.locator("#private-dashboard")).to_be_visible()


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
        assert page.evaluate("window.scrollY") <= 1
        assert page.url.endswith("#" + name)
    page.go_back()
    only_visible(page, "diagnostics")
    assert page.evaluate("window.scrollY") <= 1
    page.go_forward()
    only_visible(page, "updates")
    assert page.evaluate("window.scrollY") <= 1
    page.goto(page.url.split("#")[0] + "#router")
    only_visible(page, "router")
    page.goto(page.url.split("#")[0] + "#nonsense")
    only_visible(page, "devices")


def check_emergency_controls(page):
    """The header scrolls away while Panic, status and the utility opener stay usable."""

    close_controls(page)
    pinned = page.evaluate(
        """() => Array.from(document.querySelectorAll('body *'))
            .filter((node) => ['fixed', 'sticky'].includes(getComputedStyle(node).position))
            .filter((node) => !node.closest('#toast, #credentials-panel'))
            .filter((node) => !node.hidden && node.getClientRects().length)
            .map((node) => node.className || node.id || node.tagName)"""
    )
    assert set(pinned) == {"safety-bar", "controls-toggle"}, pinned
    page.locator('[data-page-link="schedules"]').click()
    expect(page.locator("#page-schedules")).to_be_focused()
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    for control in ("#panic-button", "#router-wifi-toggle", "#controls-toggle"):
        expect(page.locator(control)).to_be_in_viewport(ratio=1)
    assert_router_status_is_read_only(page)
    last = page.locator("#page-schedules > section:not([hidden])").last.bounding_box()
    bar = page.locator(".safety-bar").bounding_box()
    assert last["y"] + last["height"] <= bar["y"] + 0.5, (last, bar)

    # Utilities open from an explicit control, accept keyboard focus and close predictably.
    open_controls(page)
    utilities = page.locator(".control-utilities")
    expect(utilities).to_be_visible()
    assert page.evaluate("""() => {
        const active = document.activeElement;
        return !!active && !active.disabled && !!active.closest('.control-utilities');
    }""")
    for selector in (
        "#open-admin-credentials", "#open-router-credentials", "#quick-reset-wifi-name",
    ):
        page.locator(selector).scroll_into_view_if_needed()
        expect(page.locator(selector)).to_be_in_viewport(ratio=1)
    expect(page.locator("#panic-button")).to_be_in_viewport(ratio=1)
    page.screenshot(path=f"test-results/operate-utilities-{page.viewport_size['width']}.png")
    page.keyboard.press("Escape")
    expect(utilities).to_be_hidden()
    expect(page.locator("#controls-toggle")).to_be_focused()
    open_controls(page)
    page.locator("#brand-title").click()
    expect(utilities).to_be_hidden()
    expect(page.locator("#controls-toggle")).to_have_attribute("aria-expanded", "false")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def check_skip_link(page):
    skip = page.get_by_role("link", name="Skip to main content")
    assert skip.bounding_box()["y"] + skip.bounding_box()["height"] <= 0
    skip.focus()
    expect(skip).to_be_focused()
    expect(skip).to_be_in_viewport(ratio=1)
    page.keyboard.press("Enter")
    expect(page.locator("#content")).to_be_focused()
    page.locator('[data-page-link="devices"]').click()
    expect(page.locator("#page-devices")).to_be_focused()
    assert skip.bounding_box()["y"] + skip.bounding_box()["height"] <= 0


def check_keyboard_navigation(page):
    # Establish keyboard modality before checking the :focus-visible treatment.
    page.keyboard.press("Tab")
    link = page.locator('[data-page-link="profiles"]')
    link.focus()
    expect(link).to_be_focused()
    assert link.evaluate("node => getComputedStyle(node).outlineStyle !== 'none'")
    page.keyboard.press("Enter")
    only_visible(page, "profiles")
    expect(page.locator("#page-profiles")).to_be_focused()
    offset = page.evaluate("window.scrollY")
    if offset > 1:
        page.screenshot(path=f"test-results/tab-scroll-regression-{page.viewport_size['width']}.png")
    assert offset <= 1, f"Tab activation unexpectedly scrolled down by {offset}px"
    expect(page.locator("#page-heading")).to_have_text("Profiles")
    expect(page.locator("#page-heading")).to_be_in_viewport(ratio=1)
    page.go_back()
    only_visible(page, "devices")
    expect(page.locator("#page-devices")).to_be_focused()
    assert page.evaluate("window.scrollY") <= 1
    page.go_forward()
    only_visible(page, "profiles")
    expect(page.locator("#page-profiles")).to_be_focused()
    assert page.evaluate("window.scrollY") <= 1
    page.go_back()
    only_visible(page, "devices")


def check_collapsing(page):
    page.locator('[data-page-link="schedules"]').click()
    timer = page.get_by_role("button", name="Run a profile timer")
    expect(timer).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#timer-list")).to_be_hidden()
    timer.click()
    expect(timer).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#timer-list")).to_be_visible()
    page.reload()
    wait_for_remembered_token(page)
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
    # A phone keeps every page link in one row and Panic and the router switch in the bar at
    # the bottom of the window, also at the end of the longest page. A page link shows the page.
    for width, height in ((320, 568), (360, 640), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
        for name in PAGES:
            expect(page.locator(f'[data-page-link="{name}"]')).to_be_visible()
        page.locator('[data-page-link="schedules"]').click()
        expect(page.locator("#page-schedules")).to_be_focused()
        assert page.evaluate("window.scrollY") <= 1
        expect(page.locator("#page-schedules")).to_be_in_viewport()
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        for control in ("#panic-button", "#router-wifi-toggle"):
            expect(page.locator(control)).to_be_in_viewport(ratio=1)
        page.locator('[data-page-link="devices"]').click()
        expect(page.locator("#page-devices")).to_be_in_viewport()


def check_action_help(page):
    open_controls(page)
    tip = page.locator("#sidebar-action-help")
    for name in ("Reset network help", "Start help"):
        trigger = page.get_by_role("img", name=name, exact=True)
        trigger.hover()
        action = "reset" if name.startswith("Reset") else "start"
        page.screenshot(path=f"test-results/action-help-{action}-{page.viewport_size['width']}.png")
        expect(tip).to_be_visible()
        expect(trigger).to_have_attribute("aria-describedby", "sidebar-action-help")
        assert tip.inner_text() == trigger.get_attribute("data-help")
        assert trigger.evaluate("node => getComputedStyle(node, '::after').display") == "none"
        anchor = trigger.bounding_box()
        box = tip.bounding_box()
        width = page.viewport_size["width"]
        height = page.viewport_size["height"]
        assert 0 <= box["x"] and box["x"] + box["width"] <= width
        assert 0 <= box["y"] and box["y"] + box["height"] <= height
        # A header action near the right edge places help below or above it.
        gaps = (
            box["x"] - anchor["x"] - anchor["width"],
            anchor["x"] - box["x"] - box["width"],
            box["y"] - anchor["y"] - anchor["height"],
            anchor["y"] - box["y"] - box["height"],
        )
        assert any(0 <= gap <= 16 for gap in gaps), (box, anchor)
        tip.hover()
        expect(tip).to_be_visible()
        page.mouse.move(0, 0)
        expect(tip).to_be_hidden()
        trigger.focus()
        expect(tip).to_be_visible()
        page.keyboard.press("Escape")
        expect(tip).to_be_hidden()
        # Escape also closes the phone Controls sheet. Reopen it for the next check.
        open_controls(page)
        page.locator("#reset-button").focus()
        trigger.focus()
        expect(tip).to_be_visible()
        page.locator("#reset-button").focus()
        expect(tip).to_be_hidden()
        trigger.hover()
        expect(tip).to_be_visible()
        page.set_viewport_size(page.viewport_size)
        page.evaluate("window.dispatchEvent(new Event('resize'))")
        expect(tip).to_be_hidden()
        page.mouse.move(0, 0)
        trigger.hover()
        expect(tip).to_be_visible()
        page.evaluate("window.dispatchEvent(new Event('scroll'))")
        expect(tip).to_be_visible()
        page.mouse.move(0, 0)
        expect(tip).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth") <= page.viewport_size["width"]
    close_controls(page)


def run():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True,
        env={**os.environ, "JITTERSIM_BROWSER_TEST_SEED": "1"},
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
                    check_action_help(page)
                    check_skip_link(page)
                    check_keyboard_navigation(page)
                    check_sections(page)
                    check_emergency_controls(page)
                    check_collapsing(page)
                    check_reveal(page)
                    if width == 1440:
                        check_draft_navigation(page)
                        check_diagnostics_keyboard(page)
                    page.locator('[data-page-link="devices"]').click()
                    page.screenshot(path=f"test-results/dashboard-devices-{width}.png")
                    if width == 1280:
                        check_narrow(page)
                        check_action_help(page)
                        page.screenshot(path="test-results/dashboard-devices-390.png")
                        page.set_viewport_size({"width": 900, "height": 360})
                        check_action_help(page)
                        open_controls(page)
                        reset_help = page.get_by_role("img", name="Reset network help", exact=True)
                        reset_help.focus()
                        expect(page.locator("#sidebar-action-help")).to_be_visible()
                        page.evaluate("window.dispatchEvent(new Event('resize'))")
                        expect(page.locator("#sidebar-action-help")).to_be_hidden()
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
