"""Check the round help icons next to field labels on every page that has them.

An icon is a small square box with round corners on the label line. It keeps a 44px touch target
on a phone and its tip opens on hover and focus and click and closes on Escape and on a click
outside. The tip stays inside the window. The journey runs at laptop and phone sizes with the
normal fonts and with a wider face like the fonts of another machine.
"""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.phone_layout import STRESSES, set_font_stress, sign_in

WINDOWS = ((1280, 800), (390, 844), (320, 568))
PAGES = ("profiles", "schedules", "router", "traffic", "access", "devices")
# The other pages have no field help today. They are measured so that a new icon is checked at once.
WITHOUT_ICONS = ("router", "traffic", "devices")
MAX_ICON_REM = 1.6
PHONE_TARGET = 43.5  # 44px less what a browser rounds away

MEASURE = """() => {
    document.querySelectorAll('[data-help-index]').forEach((node) => { delete node.dataset.helpIndex; });
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
    const page = document.querySelector('[data-page]:not([hidden])');
    return Array.from(page.querySelectorAll('.help-icon')).filter(
        (node) => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden'
    ).map((node, index) => {
        node.dataset.helpIndex = String(index);
        const box = node.getBoundingClientRect();
        const before = getComputedStyle(node, '::before');
        // the centre of the text lines in front of the icon in the same label or title
        const range = document.createRange();
        let text = null;
        for (const child of node.parentElement.childNodes) {
            if (child.nodeType === 3 && child.textContent.trim()) {
                range.selectNodeContents(child);
                const rect = range.getBoundingClientRect();
                text = text ? {top: Math.min(text.top, rect.top), bottom: Math.max(text.bottom, rect.bottom)}
                            : {top: rect.top, bottom: rect.bottom};
            }
        }
        return {
            index, tag: node.tagName, name: node.getAttribute('aria-label'),
            width: box.width, height: box.height, rem, minHeight: getComputedStyle(node).minHeight,
            hitWidth: parseFloat(before.width), hitHeight: parseFloat(before.height),
            iconMiddle: (box.top + box.bottom) / 2,
            textMiddle: text ? (text.top + text.bottom) / 2 : null,
            textTop: text ? text.top : null, textBottom: text ? text.bottom : null,
        };
    });
}"""


def start_fixture():
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
            raise RuntimeError("Help icon dashboard fixture did not start")
        return process, int(process.stdout.readline().strip())


def open_details(page):
    page.evaluate("document.querySelectorAll('details').forEach((node) => { node.open = true; })")


def open_page(page, name):
    page.evaluate(f"location.hash = '{name}'")
    expect(page.locator(f"#page-{name}")).to_be_visible()
    open_details(page)


def tip_inside_window(page, what):
    tip = page.locator(".field-tooltip:not([hidden])")
    expect(tip).to_have_count(1)
    found = tip.bounding_box()
    size = page.viewport_size
    assert found["x"] >= 0 and found["y"] >= 0, (what, "tip starts outside the window", found)
    assert found["x"] + found["width"] <= size["width"] + 0.5, (what, "tip runs past the right", found)
    assert found["y"] + found["height"] <= size["height"] + 0.5, (what, "tip runs past the bottom", found)
    return found


def check_geometry(page, items, what, phone):
    for item in items:
        label = (what, item["name"])
        assert abs(item["width"] - item["height"]) <= 0.5, (*label, "not square", item)
        assert item["height"] <= MAX_ICON_REM * item["rem"] + 0.5, (*label, "too tall", item)
        assert item["height"] >= item["rem"], (*label, "too small to read", item)
        if item["tag"] == "BUTTON":
            assert item["name"] and item["name"].endswith(" help"), (*label, "no accessible name")
        if phone:
            assert item["hitWidth"] >= PHONE_TARGET and item["hitHeight"] >= PHONE_TARGET, (
                *label, "touch target under 44px", item)
        if item["textMiddle"] is not None:
            # one line: the icon is centred on the text beside it and not pushed under it
            assert abs(item["iconMiddle"] - item["textMiddle"]) <= 3, (*label, "not on the label line", item)
            assert item["textTop"] - 2 <= item["iconMiddle"] <= item["textBottom"] + 2, (
                *label, "below or above the label", item)


def check_touch_target(page, index, what, phone):
    """Taps just outside the visible circle still reach the button: 20px on a phone (a 44px target)."""

    reach = 20 if phone else 14

    icon = page.locator(f'[data-help-index="{index}"]')
    icon.scroll_into_view_if_needed()
    found = icon.bounding_box()
    centre = (found["x"] + found["width"] / 2, found["y"] + found["height"] / 2)
    for dx, dy in ((-reach, 0), (reach, 0), (0, -reach), (0, reach)):
        hit = page.evaluate(
            """([x, y, index]) => {
                const node = document.elementFromPoint(x, y);
                const icon = document.querySelector('[data-help-index="' + index + '"]');
                return !!node && (node === icon || icon.contains(node));
            }""",
            [centre[0] + dx, centre[1] + dy, index],
        )
        assert hit, (what, "a tap beside the icon misses it", dx, dy)


def check_tip(page, index, what, phone):
    icon = page.locator(f'[data-help-index="{index}"]')
    icon.scroll_into_view_if_needed()
    if phone:
        icon.click()
    else:
        icon.hover()
    expect(page.locator(".field-tooltip:not([hidden])")).to_have_count(1)
    tip_inside_window(page, what)
    page.keyboard.press("Escape")
    expect(page.locator(".field-tooltip:not([hidden])")).to_have_count(0)
    # keyboard focus opens the tip too and a click on the page closes it. The tip waits 120ms
    # before it closes after the pointer leaves so the journey lets that pass first.
    page.mouse.move(2, 2)
    page.wait_for_timeout(250)
    icon.evaluate("(node) => node.blur()")
    icon.focus()
    expect(page.locator(".field-tooltip:not([hidden])")).to_have_count(1)
    tip_inside_window(page, what)
    page.keyboard.press("Escape")
    expect(page.locator(".field-tooltip:not([hidden])")).to_have_count(0)
    icon.click()
    expect(page.locator(".field-tooltip:not([hidden])")).to_have_count(1)
    page.mouse.click(2, 2)
    expect(page.locator(".field-tooltip:not([hidden])")).to_have_count(0)


def check_sidebar(page, phone, what):
    """The sidebar action buttons keep their icons and the small circle and the target."""

    if phone:
        page.locator("#controls-toggle").click()
        expect(page.locator("#controls-toggle")).to_have_attribute("aria-expanded", "true")
    icons = page.locator(".sidebar .sidebar-action-row .help-icon")
    assert icons.count() == 2, (what, "sidebar help icons")
    for position in range(2):
        found = icons.nth(position).evaluate(
            """(node) => {
                const box = node.getBoundingClientRect();
                const before = getComputedStyle(node, '::before');
                return {width: box.width, height: box.height, hit: parseFloat(before.width),
                        rem: parseFloat(getComputedStyle(document.documentElement).fontSize)};
            }"""
        )
        assert abs(found["width"] - found["height"]) <= 0.5, (what, "sidebar icon not square", found)
        assert found["height"] <= MAX_ICON_REM * found["rem"] + 0.5, (what, "sidebar icon too tall", found)
        if phone:
            assert found["hit"] >= PHONE_TARGET, (what, "sidebar target under 44px", found)
    if phone:
        page.locator("#controls-toggle").click()
        expect(page.locator("#controls-toggle")).to_have_attribute("aria-expanded", "false")


def run():
    process, port = start_fixture()
    base = f"http://127.0.0.1:{port}"
    seen = {}
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
                for stress, css in STRESSES:
                    set_font_stress(page, css)
                    for width, height in WINDOWS:
                        page.set_viewport_size({"width": width, "height": height})
                        phone = width <= 768
                        for name in PAGES:
                            what = f"{name} {width}x{height} {stress}"
                            open_page(page, name)
                            items = page.evaluate(MEASURE)
                            if not items and name in WITHOUT_ICONS:
                                continue
                            assert items, (what, "no help icons found")
                            check_geometry(page, items, what, phone)
                            seen[what] = (len(items), round(items[0]["width"], 1), round(items[0]["height"], 1))
                            check_touch_target(page, items[0]["index"], what, phone)
                            for item in items[:: max(1, len(items) // 6)] + items[-1:]:
                                check_tip(page, item["index"], f"{what} {item['name']}", phone)
                            if name == "profiles":
                                page.screenshot(path=f"test-results/help-icons-{width}-{stress.split()[0]}.png")
                        check_sidebar(page, phone, f"sidebar {width}x{height} {stress}")
                assert not errors, errors
                context.close()
            finally:
                browser.close()
        for what, (count, width, height) in seen.items():
            print(f"{what}: {count} icons, first {width}x{height}px")
        print("Help icon acceptance passed.")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
