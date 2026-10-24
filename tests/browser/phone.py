"""Steps shared by the journeys for the phone layout of the dashboard.

Up to 48rem (768px) the sidebar footer (Active profile, Start, Reset, the status and message
lines, the copy buttons and the credential buttons) sits in the Controls sheet. Panic and the
router Wi-Fi status stay in the bar pinned to the bottom of the window. Wider windows show the
sidebar as before, so these steps do nothing there.
"""

import re

from playwright.sync_api import expect

PHONE_WIDTH = 768
TOGGLE = "#controls-toggle"
STRIP = "#status-strip"


def is_phone(page):
    return page.viewport_size["width"] <= PHONE_WIDTH


def _set(page, selector, expanded):
    toggle = page.locator(selector)
    if toggle.is_visible() and (toggle.get_attribute("aria-expanded") == "true") != expanded:
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true" if expanded else "false")


def open_controls(page):
    """Open the Controls sheet when the phone layout keeps the sidebar footer in it."""

    _set(page, TOGGLE, True)


def close_controls(page):
    """Close the Controls sheet so that it covers nothing of the page."""

    _set(page, TOGGLE, False)


def open_status(page):
    """Open the full status block under the one line status strip of a phone."""

    _set(page, STRIP, True)


def sidebar_control(page, selector):
    """A control of the sidebar footer. On a phone the Controls sheet is opened first."""

    control = page.locator(selector)
    if control.is_hidden():
        open_controls(page)
    return control


def assert_router_status_is_read_only(page):
    """The visible router value is text and cannot take keyboard focus."""

    value = page.locator("#router-wifi-toggle")
    assert value.evaluate("""node => {
        const before = document.activeElement;
        node.focus();
        return node.tagName === 'SPAN' && node.tabIndex === -1 &&
            !node.hasAttribute('role') && !node.hasAttribute('aria-checked') &&
            !node.hasAttribute('aria-label') && !node.hasAttribute('aria-labelledby') &&
            document.activeElement === before && document.activeElement !== node;
    }""")
    expect(value).to_have_text(re.compile(r"^(On|Off|Unknown)$"))

    label = page.locator("#router-wifi-label")
    expect(label).to_have_text("Router Wi-Fi")
    # Neither the label nor its explanation is cut off by the bottom bar.
    for selector in ("#router-wifi-label", "#router-wifi-status"):
        rect = page.locator(selector).bounding_box()
        assert rect and rect["y"] >= 0 and rect["y"] + rect["height"] <= page.viewport_size["height"], (
            selector, page.viewport_size, rect,
        )
