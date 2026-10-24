"""Keep Panic reachable across compact and full-width dashboard layouts."""

import json
import os
import re
import traceback
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.dashboard_navigation import unlock
from tests.browser.forms_support import served_dashboard
from tests.browser.phone import assert_router_status_is_read_only

RESULTS = Path(os.environ.get("JITTERSIM_LANDSCAPE_RESULTS", "test-results/landscape-safety"))
CAPTURE = os.environ.get("JITTERSIM_LANDSCAPE_CAPTURE") == "1"
CASES = (
    (844, 390, False, 16),
    (844, 390, True, 16),
    (1024, 568, False, 16),
    (1366, 576, False, 16),
    (1366, 577, False, 16),
    (375, 812, True, 16),
    (1280, 800, False, 16),
    (1920, 1000, False, 16),
    (844, 390, True, 18),
)
CONTROLS = (
    "#active-profile-select",
    "#active-profile-button",
    "#apply-button",
    "#reset-button",
    "#sync-router-ap",
    "#refresh-router-wifi",
    "#quick-reset-wifi-name",
    "#open-admin-credentials",
    "#open-router-credentials",
)


def inside(page, selector, minimum=0):
    box = page.locator(selector).bounding_box()
    assert (
        box and box["y"] >= -0.5 and box["y"] + box["height"] <= page.viewport_size["height"] + 0.5
    ), (selector, box)
    assert box["height"] >= minimum - 0.5, (selector, box)
    return box


def panic_reachable(page):
    inside(page, "#panic-button", 44)
    assert page.locator("#panic-button").evaluate("""node => {
        const box = node.getBoundingClientRect();
        const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
        return hit === node || node.contains(hit);
    }"""), "Panic is covered"


def measure(page):
    return page.evaluate("""() => {
        const nav = document.querySelector('.sidebar-nav');
        const row = nav.querySelector('ul');
        const panic = document.querySelector('#panic-button').getBoundingClientRect();
        return {width: innerWidth, height: innerHeight, compact: phoneLayout.matches,
            touch: matchMedia('(pointer: coarse)').matches,
            panic: {top: panic.top, bottom: panic.bottom, height: panic.height},
            nav: {height: nav.getBoundingClientRect().height,
                minimum: getComputedStyle(nav).minHeight,
                mask: getComputedStyle(nav).maskImage, overflowY: getComputedStyle(nav).overflowY,
                rowHeight: row.getBoundingClientRect().height},
            pageWidth: document.documentElement.scrollWidth};
    }""")


def check_compact(page, name):
    toggle = page.locator("#controls-toggle")
    expect(toggle).to_be_visible()
    inside(page, "#controls-toggle", 44)
    panic_reachable(page)
    assert_router_status_is_read_only(page)
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    panic_reachable(page)
    page.locator('[data-page-link="schedules"]').click()
    expect(page.locator("#page-schedules")).to_be_focused()
    assert page.locator("#page-schedules").bounding_box()["y"] < 10
    panic_reachable(page)
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    assert page.evaluate("document.activeElement.closest('#sidebar-controls') !== null")
    for selector in CONTROLS:
        control = page.locator(selector)
        control.scroll_into_view_if_needed()
        box = inside(page, selector, 44)
        bar = page.locator(".safety-bar").bounding_box()
        assert box["y"] + box["height"] <= bar["y"] + 0.5, (selector, box, bar)
        panic_reachable(page)
    sheet = page.locator("#sidebar-controls").bounding_box()
    bar = page.locator(".safety-bar").bounding_box()
    assert sheet["y"] >= 0 and sheet["y"] + sheet["height"] <= bar["y"] + 0.5
    page.locator("#open-admin-credentials").click()
    panel = page.locator("#credentials-panel")
    expect(panel).to_be_visible()
    for selector in ("#admin-token", "#check-admin-token", "#close-credentials"):
        page.locator(selector).scroll_into_view_if_needed()
        inside(page, selector)
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(page.locator("#open-admin-credentials")).to_be_focused()
    page.keyboard.press("Escape")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(toggle).to_be_focused()
    toggle.click()
    page.locator("#brand-title").click()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    # Only the disposable fixture can receive this request, and the route answers it here.
    page.route("**/api/normal-access", lambda route: route.fulfill(json={"normal_access": True}))
    with page.expect_request(
        lambda request: request.url.endswith("/api/normal-access") and request.method == "POST"
    ):
        page.locator("#panic-button").click()
    panic_reachable(page)
    page.evaluate("window.scrollTo(0, 0)")
    nav = measure(page)["nav"]
    assert nav["height"] < 70, nav
    assert nav["mask"] == "none", nav
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    if CAPTURE and (name.startswith("844x390-fine") or name.startswith("375x812")):
        page.screenshot(path=str(RESULTS / (name + ".png")))


def check_resize(page):
    page.set_viewport_size({"width": 844, "height": 390})
    toggle = page.locator("#controls-toggle")
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    page.locator("#open-admin-credentials").focus()
    expect(page.locator("#open-admin-credentials")).to_be_focused()
    page.set_viewport_size({"width": 1280, "height": 800})
    expect(toggle).to_be_visible()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(page.locator(".control-utilities")).to_be_hidden()
    expect(toggle).to_be_focused()
    expect(page.locator("body")).not_to_have_class(re.compile(r"\bphone-controls-open\b"))
    page.locator("#apply-button").focus()
    page.set_viewport_size({"width": 844, "height": 390})
    expect(toggle).to_be_visible()
    page.evaluate(
        "() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))"
    )
    focused = page.evaluate("""() => {
        const node = document.activeElement;
        return {id: node && node.id, visible: Boolean(node && node.getClientRects().length)};
    }""")
    assert focused["visible"] and focused["id"] in ("apply-button", "controls-toggle"), focused
    panic_reachable(page)


def check_landscape_safety(browser, base):
    """Run this size matrix once, outside the parent journey's viewport loops."""

    RESULTS.mkdir(parents=True, exist_ok=True)
    results = []
    for width, height, touch, font in CASES:
        name = f"{width}x{height}-{'coarse' if touch else 'fine'}-font{font}"
        context = browser.new_context(viewport={"width": width, "height": height}, has_touch=touch)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        record = {"name": name}
        try:
            page.goto(base)
            unlock(page)
            page.evaluate("dismissToast()")
            if font != 16:
                page.evaluate(
                    "font => document.documentElement.style.setProperty("
                    "'font-size', font + 'px', 'important')",
                    font,
                )
                assert (
                    page.evaluate("getComputedStyle(document.documentElement).fontSize")
                    == f"{font}px"
                )
            record["initial"] = measure(page)
            expected = width <= 768 or height <= 576
            assert record["initial"]["compact"] == expected, record
            assert record["initial"]["touch"] == touch, record
            if expected:
                check_compact(page, name)
            else:
                toggle = page.locator("#controls-toggle")
                expect(toggle).to_be_visible()
                expect(toggle).to_have_attribute("aria-expanded", "false")
                expect(page.locator(".control-utilities")).to_be_hidden()
                header = page.locator(".sidebar").bounding_box()
                assert header and abs(header["width"] - width) <= 1, header
                assert (
                    page.locator(".sidebar").evaluate("node => getComputedStyle(node).position")
                    == "static"
                )
                panic_reachable(page)
                assert_router_status_is_read_only(page)
            if (width, height, touch, font) == (1280, 800, False, 16):
                check_resize(page)
            assert not errors, errors
            record["passed"] = True
        except Exception as error:
            record["passed"] = False
            record["error"] = str(error)
            record["traceback"] = traceback.format_exc()
            if CAPTURE:
                page.screenshot(path=str(RESULTS / (name + "-failed.png")))
        finally:
            results.append(record)
            context.close()
    (RESULTS / "geometry.json").write_text(json.dumps(results, indent=2) + "\n")
    failures = [record for record in results if not record["passed"]]
    assert not failures, json.dumps(failures, indent=2)
    print(f"Short-window safety passed in {len(results)} pointer, size and font cases.")


def run():
    with sync_playwright() as playwright, served_dashboard() as base:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
        )
        try:
            check_landscape_safety(browser, base)
        finally:
            browser.close()


if __name__ == "__main__":
    run()
