"""Keep the update notice readable inside the existing sidebar link."""

import os

from playwright.sync_api import expect, sync_playwright

from tests.browser.dashboard_navigation import unlock
from tests.browser.forms_support import served_dashboard


def run():
    with sync_playwright() as playwright, served_dashboard() as base:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
        )
        try:
            for width, height in ((1366, 768), (1024, 768), (844, 390), (375, 812)):
                page = browser.new_page(viewport={"width": width, "height": height})
                try:
                    page.goto(base)
                    unlock(page)
                    page.evaluate("""() => {
                        releaseState = {installed: 'v0.2.0', available: 'v0.2.1',
                            phase: 'ready', installable: true};
                        renderUpdates();
                    }""")
                    badge = page.locator("#update-available-badge")
                    expect(badge).to_have_text("Update available")
                    link = page.locator("#updates-tab-link")
                    expect(link).to_have_attribute("aria-label", "Updates, update available")
                    link.focus()
                    page.keyboard.press("Shift+Tab")
                    page.keyboard.press("Tab")
                    expect(link).to_be_focused()
                    assert link.evaluate(
                        "node => getComputedStyle(node).outlineStyle !== 'none'"
                    )
                    expect(link).to_be_in_viewport()
                    geometry = badge.evaluate("""node => {
                        const range = document.createRange();
                        range.selectNodeContents(node);
                        const text = [...range.getClientRects()];
                        const badge = node.getBoundingClientRect();
                        const link = node.closest('a').getBoundingClientRect();
                        return {lines: text.length, badgeRight: badge.right,
                            linkRight: link.right, textRight: text.at(-1).right};
                    }""")
                    assert geometry["lines"] == 1, geometry
                    assert geometry["textRight"] <= geometry["badgeRight"], geometry
                    assert geometry["badgeRight"] <= geometry["linkRight"], geometry
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    )
                    if width <= 768:
                        panic = page.locator("#panic-button")
                        expect(panic).to_be_in_viewport(ratio=1)
                        assert panic.bounding_box()["height"] >= 44
                finally:
                    page.close()
        finally:
            browser.close()
    print("Sidebar update badge fits at desktop, landscape and phone sizes.")


if __name__ == "__main__":
    run()
