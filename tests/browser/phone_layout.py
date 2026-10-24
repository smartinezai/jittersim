"""Use the dashboard on phone-sized windows and check that laptop windows keep the sidebar.

On a phone the sidebar is a short header (the name, the mode, one row of page links and one line
of status). Panic and the router Wi-Fi status sit in a bar pinned to the bottom of the window and
the rest of the sidebar footer opens from that bar as the Controls sheet. Each check runs with the
normal fonts and with a wider face like the fonts of another machine. The journey prints what it
measured so that a run documents the layout.
"""

import os
import re
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.landscape_safety import check_landscape_safety
from tests.browser.phone import assert_router_status_is_read_only, close_controls, open_controls, open_status, sidebar_control

PHONES = ((390, 844), (360, 640), (320, 568))
DESKTOPS = ((1280, 800), (1920, 1000))
PAGES = (
    "devices", "profiles", "schedules", "router", "traffic", "measurements", "activity", "access",
    "diagnostics",
)
LONGEST_PAGE = "schedules"
HEADERS = {"Authorization": "Bearer browser-test"}
PROFILE = {"id": "slow", "name": "Phone slow", "upload": {"bandwidth_kbit": 1000},
           "download": {"bandwidth_kbit": 2000}}
TARGET = 43.5  # 44px less what a browser rounds away
WIDE_FONTS = (
    "html { font-size: 112.5% !important; }"
    " * { font-family: Verdana, 'DejaVu Sans', sans-serif !important; }"
)
STRESSES = (("normal fonts", ""), ("wide fonts", WIDE_FONTS))
SHEET_CONTROLS = (
    "#active-profile-select", "#active-profile-button", "#apply-button", "#reset-button",
    "#sync-router-ap", "#refresh-router-wifi", "#quick-reset-wifi-name",
    "#open-admin-credentials", "#open-router-credentials",
)


def start_fixture():
    # a scheduler like a real install so that the general profile shows as active
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
            raise RuntimeError("Phone layout dashboard fixture did not start")
        return process, int(process.stdout.readline().strip())


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


def box(page, selector):
    found = page.locator(selector).bounding_box()
    assert found is not None, (selector, "is not on the page")
    return found


def inside_window(page, selector, what):
    found = box(page, selector)
    size = page.viewport_size
    assert found["x"] >= -0.5 and found["y"] >= -0.5, (what, selector, "starts outside", found)
    assert found["x"] + found["width"] <= size["width"] + 0.5, (what, selector, "runs past the right", found)
    assert found["y"] + found["height"] <= size["height"] + 0.5, (what, selector, "is cut off", found, size)
    return found


def bar_top(page):
    return box(page, ".safety-bar")["y"]


def clear_of_the_bar(page, selector, what):
    """The control is in the window and the pinned bar does not cover it."""

    found = inside_window(page, selector, what)
    assert found["y"] + found["height"] <= bar_top(page) + 0.5, (what, selector, "is under the bar", found)
    return found


def no_sideways_overflow(page, what):
    widths = page.evaluate(
        """() => ({
            page: document.documentElement.scrollWidth, body: document.body.scrollWidth,
            window: window.innerWidth,
            wide: Array.from(document.querySelectorAll('body *'))
                .filter((node) => node.getClientRects().length)
                .filter((node) => getComputedStyle(node).position !== 'fixed')
                .filter((node) => {
                    // Content inside a box that scrolls or clips sideways never widens the page.
                    for (let up = node.parentElement; up && up !== document.body; up = up.parentElement) {
                        if (getComputedStyle(up).overflowX !== 'visible') return false;
                    }
                    return true;
                })
                .map((node) => [node.id || node.className || node.tagName,
                                Math.round(node.getBoundingClientRect().right)])
                .filter(([, right]) => right > window.innerWidth + 1)
                .slice(0, 8),
        })"""
    )
    assert widths["page"] <= widths["window"] + 1, (what, "the page scrolls sideways", widths)
    assert widths["body"] <= widths["window"] + 1, (what, "the page scrolls sideways", widths)
    assert not widths["wide"], (what, "runs past the right edge", widths)


def small_targets(page):
    """Visible controls of the header, the bar, the sheet and the page smaller than 44px."""

    return page.evaluate(
        """(target) => {
            const selector = [
                '.sidebar button', '.sidebar a', '.sidebar select', '.sidebar summary',
                '.sidebar .help-icon',
                '.content button', '.content select', '.content summary', '.content .button-link',
                '.content input:not([type=checkbox]):not([type=radio]):not([type=hidden])',
                '.content .help-icon', '.toast button',
            ].join(', ');
            return Array.from(document.querySelectorAll(selector))
                .filter((node) => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden')
                .filter((node) => !(node.tagName === 'A' && getComputedStyle(node).display === 'inline'))
                .map((node) => {
                    let rect = node.getBoundingClientRect();
                    if (node.classList.contains('help-icon')) {
                        // A help icon is a small circle. Its invisible ::before is the touch target.
                        const hit = getComputedStyle(node, '::before');
                        rect = {width: parseFloat(hit.width), height: parseFloat(hit.height)};
                    }
                    const name = node.id || node.getAttribute('aria-label') || node.textContent.trim().slice(0, 30);
                    return {name, width: Math.round(rect.width * 10) / 10, height: Math.round(rect.height * 10) / 10,
                            tag: node.tagName};
                })
                .filter((item) => item.height < target || (item.tag !== 'INPUT' && item.width < target));
        }""",
        TARGET,
    )


def assert_targets(page, what):
    small = small_targets(page)
    assert not small, (what, "touch targets under 44px", small)


def measure(page):
    return page.evaluate(
        """() => {
            const rect = (selector) => {
                const node = document.querySelector(selector);
                if (!node || !node.getClientRects().length) return null;
                const box = node.getBoundingClientRect();
                return [Math.round(box.top), Math.round(box.bottom)];
            };
            const page = document.querySelector('[data-page]:not([hidden])');
            return {
                scrollY: Math.round(window.scrollY),
                header: rect('.sidebar'), page: page ? rect('#' + page.id) : null,
                bar: rect('.safety-bar'), panic: rect('#panic-button'),
                switch: rect('#router-wifi-toggle'), toggle: rect('#controls-toggle'),
            };
        }"""
    )


def sign_in(page, base):
    page.goto(base)
    expect(page.locator("#private-dashboard")).to_be_hidden()
    # Locked, the admin token is the only thing to do. Its button is in the header and the bar
    # and the Controls toggle are not shown.
    expect(page.locator("#open-admin-credentials")).to_be_visible()
    expect(page.locator("#open-admin-credentials")).to_be_in_viewport(ratio=1)
    expect(page.locator("#controls-toggle")).to_be_hidden()
    expect(page.locator("#panic-button")).to_be_hidden()
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill("browser-test")
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    page.locator("#remember-admin-token").check()
    page.locator("#close-credentials").click()
    expect(page.locator("#message")).to_contain_text("Token accepted. Diagnostics loaded.")


def check_first_screen(page, what):
    """Page content starts on the first screen and the bar with Panic is in view."""

    page.evaluate("window.scrollTo(0, 0)")
    first = page.locator("#page-devices > section:not([hidden])").first
    found = first.bounding_box()
    assert found and found["y"] + 40 < bar_top(page), (what, "no page content on the first screen", found)
    for selector in ("#panic-button", "#router-wifi-toggle", "#controls-toggle"):
        inside_window(page, selector, what)
    assert_router_status_is_read_only(page)
    return found


def check_header(page, what):
    expect(page.locator("#status-strip")).to_be_visible()
    expect(page.locator("#status-strip")).to_contain_text("Everyone: ")
    expect(page.locator("#sidebar-status")).to_be_hidden()
    open_status(page)
    expect(page.locator("#sidebar-status")).to_be_visible()
    expect(page.locator("#status-general")).to_be_visible()
    page.locator("#status-strip").click()
    expect(page.locator("#status-strip")).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#sidebar-status")).to_be_hidden()
    # Every page link is in the one row and the row never widens the page.
    for name in PAGES:
        expect(page.locator(f'[data-page-link="{name}"]')).to_be_visible()


def check_navigation(page, what):
    """A page link shows the start of that page and moves the keyboard focus to it."""

    for name in PAGES[1:] + PAGES[:1]:
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.locator(f'[data-page-link="{name}"]').click()
        expect(page.locator(f"#page-{name}")).to_be_visible()
        expect(page.locator(f"#page-{name}")).to_be_focused()
        expect(page.locator(f'[data-page-link="{name}"]')).to_have_attribute("aria-current", "page")
        assert page.url.endswith("#" + name), page.url
        start = box(page, f"#page-{name}")
        # The page starts at the top of the window unless it is too short to scroll that far.
        at_end = page.evaluate(
            "window.scrollY >= document.documentElement.scrollHeight - window.innerHeight - 1"
        )
        assert -1 <= start["y"] <= 9 or at_end, (what, name, "page start", start)
        assert start["y"] + 40 < bar_top(page), (what, name, "page start is under the bar", start)
        no_sideways_overflow(page, f"{what} on {name}")
    # The colliding ids are gone: a hash that names a page never scrolls to another element.
    page.goto(page.url.split("#")[0] + "#diagnostics")
    expect(page.locator("#page-diagnostics")).to_be_visible()
    page.go_back()
    expect(page.locator("#page-devices")).to_be_visible()


def check_longest_page(page, what):
    """At the end of the longest page Panic and the router status are still in view."""

    page.locator(f'[data-page-link="{LONGEST_PAGE}"]').click()
    for _ in range(3):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(50)
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    measured = measure(page)
    size = page.viewport_size
    panic = inside_window(page, "#panic-button", what)
    inside_window(page, "#router-wifi-toggle", what)
    inside_window(page, "#controls-toggle", what)
    bar = box(page, ".safety-bar")
    assert abs(bar["y"] + bar["height"] - size["height"]) <= 1, (what, "the bar left the bottom", bar)
    assert panic["y"] >= bar["y"] - 0.5, (what, "Panic is not in the bar", panic, bar)
    # The page ends above the bar so that its last control can be used.
    last = page.locator(f"#page-{LONGEST_PAGE} > section:not([hidden])").last.bounding_box()
    assert last["y"] + last["height"] <= bar["y"] + 0.5, (what, "the page ends under the bar", last, bar)
    no_sideways_overflow(page, f"{what} at the end of {LONGEST_PAGE}")
    page.locator('[data-page-link="devices"]').click()
    return measured


def check_sheet(page, what):
    """Every control of the sidebar footer is in the sheet, in the window and above the bar."""

    toggle = page.locator("#controls-toggle")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    for selector in SHEET_CONTROLS:
        expect(page.locator(selector)).to_be_hidden()
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    # The first control of the sheet that can be used takes the focus.
    focused = page.evaluate(
        """() => {
            const node = document.activeElement;
            return Boolean(node && node.closest('#sidebar-controls') && !node.closest('.safety-bar')
                && !node.disabled);
        }"""
    )
    assert focused, (what, "the sheet did not take the focus")
    expect(page.locator("#message")).to_be_hidden()
    expect(page.locator("#message")).to_have_attribute("aria-hidden", "true")
    for selector in SHEET_CONTROLS + ("#runtime-summary", "#apply-summary"):
        control = page.locator(selector)
        if selector == "#apply-summary" and control.is_hidden():
            continue
        control.scroll_into_view_if_needed()
        clear_of_the_bar(page, selector, what)
    sheet = box(page, "#sidebar-controls")
    assert sheet["y"] >= 0 and sheet["y"] + sheet["height"] <= bar_top(page) + 0.5, (what, sheet)
    assert_targets(page, f"{what} with the sheet open")
    no_sideways_overflow(page, f"{what} with the sheet open")
    page.screenshot(path=f"test-results/phone-sheet-{what.replace(' ', '-')}.png")
    page.keyboard.press("Escape")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(toggle).to_be_focused()
    expect(page.locator("#apply-button")).to_be_hidden()
    # A tap outside the sheet closes it too.
    open_controls(page)
    page.locator(".sidebar-brand h1").click()
    expect(toggle).to_have_attribute("aria-expanded", "false")


def check_router_status(page, what):
    sidebar_control(page, "#open-router-credentials").click()
    page.locator("#router-admin-password").fill("browser-router-password")
    page.locator("#check-router-password").click()
    expect(page.locator("#router-credential-state")).to_contain_text("Accepted")
    page.locator("#close-credentials").click()
    sidebar_control(page, "#refresh-router-wifi").click()
    # The password check a moment ago used the router budget, so the Pi does not ask the router
    # again yet and the page says how long to wait.
    expect(page.locator("#message")).to_contain_text("choose Check router Wi-Fi again")
    wait = int(re.search(r"Wait (\d+) second", page.locator("#message").text_content()).group(1))
    assert 1 <= wait <= 15, wait
    page.wait_for_timeout((wait + 1) * 1000)
    sidebar_control(page, "#refresh-router-wifi").click()
    close_controls(page)
    switch = page.locator("#router-wifi-toggle")
    # Router Wi-Fi switching is refused for recovery safety. The label only shows the state.
    expect(switch).to_have_text("On", timeout=10000)
    assert_router_status_is_read_only(page)
    inside_window(page, "#router-wifi-toggle", what)
    writes = []
    page.on("request", lambda request: writes.append(request)
            if request.url.endswith("/api/router/wifi") else None)
    switch.click(force=True)
    page.wait_for_timeout(300)
    assert not writes, writes
    expect(page.get_by_role("dialog")).to_have_count(0)


def check_active_profile(page, base, what):
    """Pick a saved profile in the sheet, apply it to every device and stop it again."""

    response = page.request.post(base + "/api/profiles", headers=HEADERS, data=PROFILE)
    assert response.ok, response.text()
    page.reload()
    expect(page.locator("#message")).to_contain_text("Token accepted. Diagnostics loaded.")
    select = sidebar_control(page, "#active-profile-select")
    button = page.locator("#active-profile-button")
    expect(select).to_be_enabled()
    expect(button).to_have_text("Apply to all devices")
    expect(button).to_be_disabled()
    expect(page.locator("#active-profile-note")).to_have_text("Protected devices stay untouched.")
    select.select_option("slow")
    expect(button).to_be_enabled()
    button.click()
    expect(page.locator("#message")).to_contain_text(
        "Profile applied for everyone. Protected devices keep normal access."
    )
    expect(button).to_have_text("Stop")
    expect(select).to_have_value("slow")
    close_controls(page)
    expect(page.locator("#status-strip")).to_contain_text(f"Everyone: {PROFILE['name']} · active")
    open_controls(page)
    page.locator("#active-profile-button").click()
    expect(page.locator("#message")).to_contain_text(
        "Stopped the profile for everyone. Saved device rules resumed."
    )
    expect(button).to_have_text("Apply to all devices")
    expect(select).to_have_value("")
    close_controls(page)
    expect(page.locator("#status-strip")).to_contain_text("Everyone: none")


def check_phone(page, base, width, height, stress, css, first):
    page.set_viewport_size({"width": width, "height": height})
    set_font_stress(page, css)
    what = f"{width}x{height} with {stress}"
    page.locator('[data-page-link="devices"]').click()
    found = check_first_screen(page, what)
    start = measure(page)
    no_sideways_overflow(page, what)
    assert_targets(page, what)
    check_header(page, what)
    check_navigation(page, what)
    end = check_longest_page(page, what)
    check_sheet(page, what)
    if first:
        page.screenshot(path=f"test-results/phone-{width}x{height}.png")
    print(f"{what}: first card at {round(found['y'])}px, top of the window {start},"
          f" end of {LONGEST_PAGE} {end}")


def check_desktop(page, width, height):
    """A laptop or desktop window keeps the sidebar with nothing of the phone layout."""

    page.set_viewport_size({"width": width, "height": height})
    page.locator('[data-page-link="devices"]').click()
    page.evaluate("document.querySelector('.sidebar').scrollTo(0, 0)")
    for selector in ("#controls-toggle", "#status-strip"):
        expect(page.locator(selector)).to_be_hidden()
    pinned = page.evaluate(
        """() => Array.from(document.querySelectorAll('body *'))
            .filter((node) => ['fixed', 'sticky'].includes(getComputedStyle(node).position))
            .filter((node) => !node.closest('#toast, #credentials-panel'))
            .filter((node) => !node.hidden && node.getClientRects().length)
            .map((node) => node.className || node.id || node.tagName)"""
    )
    assert pinned == ["sidebar"], pinned
    sidebar = box(page, ".sidebar")
    assert sidebar["x"] == 0 and sidebar["width"] < 300, sidebar
    for selector in ("#active-profile-select", "#apply-button", "#reset-button", "#apply-summary"):
        expect(page.locator(selector)).to_be_visible()
    order = [box(page, selector)["y"] for selector in
             ("#active-profile-select", "#active-profile-button", "#apply-button", "#reset-button",
              "#panic-button", "#router-wifi-toggle", "#sync-router-ap")]
    assert order == sorted(order), order
    # The whole status, Panic and copy button on a laptop screen, not a sliver of them.
    for selector in ("#router-wifi-toggle", "#panic-button", "#sync-router-ap"):
        expect(page.locator(selector)).to_be_in_viewport(ratio=1)
    scrolled = page.evaluate("document.querySelector('.sidebar').scrollTop")
    assert scrolled == 0, scrolled
    page.screenshot(path=f"test-results/phone-layout-desktop-{width}x{height}.png")
    print(f"{width}x{height}: switch {measure(page)['switch']} panic {measure(page)['panic']}")


def run():
    process, port = start_fixture()
    base = f"http://127.0.0.1:{port}"
    try:
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                context = browser.new_context(viewport={"width": 390, "height": 844})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                sign_in(page, base)
                for index, (stress, css) in enumerate(STRESSES):
                    for width, height in PHONES:
                        check_phone(page, base, width, height, stress, css, index == 0)
                set_font_stress(page, "")
                page.set_viewport_size({"width": 360, "height": 640})
                check_router_status(page, "360x640")
                check_active_profile(page, base, "360x640")
                page.emulate_media(reduced_motion="reduce")
                assert page.evaluate("scrollBehavior()") == "auto"
                page.emulate_media(reduced_motion="no-preference")
                assert page.evaluate("scrollBehavior()") == "smooth"
                for width, height in DESKTOPS:
                    check_desktop(page, width, height)
                assert not errors, errors
                context.close()
                check_landscape_safety(browser, base)
                print("Phone layout acceptance passed at 390x844, 360x640 and 320x568"
                      " with desktop windows unchanged.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
