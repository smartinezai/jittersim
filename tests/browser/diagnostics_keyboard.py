"""Read raw diagnostics with Tab and arrow keys at desktop and phone widths."""

import json

from playwright.sync_api import expect


def check_diagnostics_keyboard(page):
    viewport = page.viewport_size
    payload = {"diagnostic_keyboard_probe": "controlled-" + "x" * 600}
    probe = json.dumps(payload)
    writes = []

    def record_write(request):
        if request.method != "GET":
            writes.append(request.url)

    def diagnostics(route):
        assert route.request.headers.get("authorization") == "Bearer browser-test"
        route.fulfill(status=200, content_type="application/json", body=probe)

    page.on("request", record_write)
    page.route("**/api/diagnostics", diagnostics)
    original_font = page.evaluate("() => document.documentElement.style.fontSize")
    try:
        for width, height, enlarged in ((1440, 900, False), (375, 812, False), (375, 812, True)):
            page.set_viewport_size({"width": width, "height": height})
            if enlarged:
                page.evaluate("() => document.documentElement.style.fontSize = '20px'")
            page.locator('[data-page-link="diagnostics"]').click()
            refresh = page.locator("#refresh-diagnostics")
            refresh.click()
            raw = page.locator("#diagnostics-json")
            expect(raw).to_contain_text("diagnostic_keyboard_probe", timeout=20000)
            refresh.focus()
            page.keyboard.press("Tab")
            expect(raw).to_be_focused()
            expect(raw).to_have_accessible_name("Diagnostics")
            expect(raw).to_be_in_viewport(ratio=1)
            assert raw.evaluate("node => getComputedStyle(node).outlineStyle") == "solid"
            assert raw.evaluate("node => getComputedStyle(node).outlineWidth") == "2px"
            assert raw.evaluate("node => node.scrollWidth > node.clientWidth")
            page.keyboard.press("ArrowRight")
            page.wait_for_function("() => document.getElementById('diagnostics-json').scrollLeft > 0")
            assert raw.evaluate("node => node.getBoundingClientRect().bottom") <= height
            if width == 375:
                bar = page.locator(".safety-bar")
                expect(bar).to_be_visible()
                expect(page.locator("#panic-button")).to_be_in_viewport(ratio=1)
                expect(page.locator("#router-wifi-toggle")).to_be_in_viewport(ratio=1)
                assert raw.bounding_box()["y"] + raw.bounding_box()["height"] <= bar.bounding_box()["y"]
            page.screenshot(path=f"test-results/diagnostics-keyboard-{width}-{'large' if enlarged else 'normal'}.png")
            assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
        assert writes == [], writes
    finally:
        page.evaluate("font => document.documentElement.style.fontSize = font", original_font)
        page.unroute("**/api/diagnostics", diagnostics)
        page.remove_listener("request", record_write)
        page.set_viewport_size(viewport)
