"""Full recovery messages wrap at narrow widths and enlarged font sizes."""

from playwright.sync_api import expect


def check_notification_wrapping(page):
    original_viewport = page.viewport_size
    original_font = page.evaluate("document.documentElement.style.fontSize")
    message = (
        "Could not save the profile for " + "device" * 45 + ". "
        + "Check the saved profile and retry when the helper is available. " * 35
        + "Final recovery step: open Diagnostics and copy the error."
    )
    page.evaluate("""() => {
        const source = document.createElement('button');
        source.id = 'notification-regression-source';
        source.textContent = 'Retry notification fixture';
        document.getElementById('toast').before(source);
    }""")
    try:
        for width, height in ((320, 640), (1440, 1000)):
            for font in ("100%", "200%"):
                page.set_viewport_size({"width": width, "height": height})
                page.evaluate("font => document.documentElement.style.fontSize = font", font)
                page.locator("#notification-regression-source").focus()
                page.evaluate("message => showToast(message, 'error')", message)
                text = page.locator("#toast-message")
                expect(text).to_have_text(message)
                expect(page.locator("#action-alert")).to_have_text(message)
                expect(page.locator("#action-announcer")).to_have_text("")
                metrics = text.evaluate("""node => ({
                    width: node.clientWidth, scrollWidth: node.scrollWidth,
                    height: node.clientHeight, scrollHeight: node.scrollHeight,
                    toastHeight: node.parentElement.clientHeight,
                    rows: getComputedStyle(node.parentElement).gridTemplateRows,
                    align: getComputedStyle(node).alignSelf
                })""")
                assert metrics["scrollWidth"] <= metrics["width"] + 1, (width, font, metrics)
                assert metrics["scrollHeight"] > metrics["height"], metrics
                toast = page.locator("#toast").bounding_box()
                assert toast["x"] >= 0 and toast["y"] >= 0, (width, font, toast)
                assert toast["x"] + toast["width"] <= width + 1, (width, font, toast)
                assert toast["height"] <= height * .4 + 1, (width, font, toast)
                if width == 320:
                    bar = page.locator(".safety-bar").bounding_box()
                    assert toast["y"] + toast["height"] <= bar["y"], (font, toast, bar)
                dismiss = page.locator("#toast-dismiss")
                expect(dismiss).to_be_in_viewport(ratio=1)
                page.screenshot(path=f"test-results/notification-{width}-{font[:-1]}.png")
                page.keyboard.press("Tab")
                expect(text).to_be_focused()
                page.keyboard.press("End")
                page.wait_for_function("""() => {
                    const node = document.getElementById('toast-message');
                    return node.scrollTop >= node.scrollHeight - node.clientHeight - 1;
                }""")
                expect(dismiss).to_be_in_viewport(ratio=1)
                page.keyboard.press("Tab")
                expect(dismiss).to_be_focused()
                page.keyboard.press("Enter")
                expect(page.locator("#toast")).to_be_hidden()
                expect(page.locator("#notification-regression-source")).to_be_focused()
                # A short result needs no scrolling and adds no message tab stop.
                page.locator("#notification-regression-source").focus()
                page.evaluate("showToast('Saved.', 'success')")
                short_box = page.locator("#toast").bounding_box()
                assert short_box["height"] < height * .4, (width, font, short_box)
                page.keyboard.press("Tab")
                expect(page.locator("#toast-dismiss")).to_be_focused()
                page.keyboard.press("Enter")
    finally:
        page.evaluate("document.getElementById('notification-regression-source').remove()")
        page.evaluate("font => document.documentElement.style.fontSize = font", original_font)
        page.set_viewport_size(original_viewport)
