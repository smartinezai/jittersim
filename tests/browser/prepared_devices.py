"""Bounded prepared-profile UI acceptance using the synthetic helper fixture."""

import json
import os
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.a11y_axe import load_engine
from tests.browser.device_apply_start import start_fixture
from tests.browser.fixture import WIDE_FONTS_STYLE
from tests.browser.protected_clients import HEADERS, MAC_ADDRESS, sign_in


def check(browser, base, width, height):
    context = browser.new_context(viewport={"width": width, "height": height}, has_touch=width < 500)
    page = context.new_page()
    errors, writes, apply_writes = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: writes.append((request.url, request.post_data_json))
            if request.method == "POST" and "pending-devices" in request.url else None)
    page.on("request", lambda request: apply_writes.append(request.post_data_json)
            if request.method == "POST" and request.url.endswith("/api/connected-devices/apply-profile") else None)
    # Reset only synthetic fixture state, never real hardware.
    rows = page.request.get(base + "/api/pending-devices", headers=HEADERS).json()["pending_devices"]
    for row in rows:
        assert page.request.post(base + "/api/pending-devices/delete", headers=HEADERS,
                                 data={"mac_address": row["mac_address"], "expected_revision": row["revision"]}).ok
    assert page.request.post(base + "/api/profiles", headers=HEADERS,
                             data={"id": "prepared-test", "name": "Prepared test", "upload": {"delay_ms": 30}}).ok
    # Each viewport has an explicit clean Start after the previous viewport's Panic.
    assert page.request.post(base + "/api/apply", headers=HEADERS, data={}).ok
    sign_in(page, base)
    card = page.locator("#prepared-card")
    expect(card).to_be_visible()
    expect(page.locator("#prepared-profile option[value='prepared-test']")).to_have_count(1)
    if width == 320:
        page.add_style_tag(content=WIDE_FONTS_STYLE.decode()[7:-8])
        expect(page.locator("html")).to_have_css("font-size", "18px")
    page.locator("#prepared-mac").fill(MAC_ADDRESS)
    page.locator("#prepared-name").fill("Prepared phone")
    page.locator("#prepared-profile").select_option("prepared-test")
    page.locator("#prepared-save").click()
    row = card.locator("[data-prepared-mac]")
    expect(row).to_contain_text("Prepared phone")
    assert not apply_writes
    row.get_by_role("button", name=re.compile("^Edit")).click()
    expect(page.locator("#prepared-mac")).to_have_js_property("readOnly", True)
    page.locator("#prepared-name").fill("Prepared edited")
    page.locator("#prepared-save").click()
    expect(row).to_contain_text("Prepared edited")
    assert "expected_revision" in writes[-1][1]
    row.get_by_role("button", name=re.compile("^Review")).click()
    expect(page.locator("#prepared-review")).to_be_visible()
    expect(page.locator("#prepared-review-summary")).to_contain_text("configuration version")
    expect(page.locator("#prepared-review-summary")).to_contain_text("pending revision")
    expect(page.locator("#prepared-review-title")).to_be_focused()
    page.locator("#prepared-use").click()
    dialog = page.locator("dialog[open]")
    expect(dialog).to_contain_text(MAC_ADDRESS)
    expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(page.locator("#prepared-use")).to_be_focused()
    uses = []
    def refuse(route):
        uses.append(route.request.post_data_json)
        route.fulfill(status=409, content_type="application/json", body=json.dumps({"error": "Review changed"}))
    page.route("**/api/connected-devices/apply-profile", refuse)
    page.locator("#prepared-use").click()
    page.locator("dialog[open]").get_by_role("button", name="Use profile", exact=True).click()
    expect(page.locator("#prepared-note")).to_contain_text("Review changed")
    expect(page.locator("#prepared-review")).to_be_hidden()
    assert len(uses) == 1 and set(uses[0]) == {"pending_mac", "expected_pending_revision", "expected_configuration_version"}
    assert uses[0]["pending_mac"] == MAC_ADDRESS
    page.unroute("**/api/connected-devices/apply-profile", refuse)
    # Review navigation and lock cannot leave private data or a usable stale review.
    row.get_by_role("button", name=re.compile("^Review")).click()
    expect(page.locator("#prepared-review")).to_be_visible()
    page.locator('[data-page-link="profiles"]').click()
    page.locator('[data-page-link="devices"]').click()
    expect(page.locator("#prepared-review")).to_be_hidden()
    expect(page.locator("#prepared-settings")).to_be_empty()
    # Explicit successful Use reaches the real reviewed fake-helper API path.
    row.get_by_role("button", name=re.compile("^Review")).click()
    page.locator("#prepared-use").click()
    page.locator("dialog[open]").get_by_role("button", name="Use profile", exact=True).click()
    expect(page.locator("#prepared-note")).to_contain_text("Prepared choice retained", timeout=20000)
    assert page.request.get(base + "/api/pending-devices", headers=HEADERS).json()["pending_devices"]
    row.get_by_role("button", name=re.compile("^Review")).click()
    page.locator("#prepared-review details summary").click()
    expect(page.locator("#prepared-settings")).to_contain_text('"delay_ms": 30')
    card.scroll_into_view_if_needed()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    for button in card.locator("button:visible").all():
        box = button.bounding_box()
        assert box and box["height"] >= 44, box
    engine, _ = load_engine()
    page.evaluate(engine)
    violations = page.evaluate("async () => (await axe.run(document.querySelector('#prepared-card'), {runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})).violations")
    assert not violations, violations
    page.locator("#prepared-review details summary").click()
    card.scroll_into_view_if_needed()
    page.screenshot(path=f"test-results/prepared-profiles/prepared-{width}.png")
    if width == 1440:
        # A pending Review may be overtaken without leaving its row disabled.
        held = []
        def delay_review(route):
            held.append(route)
        page.route("**/api/pending-devices/review?*", delay_review)
        review_button = row.get_by_role("button", name=re.compile("^Review"))
        review_button.click()
        expect(review_button).to_be_disabled()
        page.locator("#panic-button").click()
        expect(page.locator("#panic-button")).to_have_attribute("aria-busy", "false")
        assert len(held) == 1
        held.pop().fulfill(content_type="application/json", body=json.dumps({"profile": {"name": "STALE PRIVATE"}}))
        expect(review_button).to_be_enabled()
        expect(page.locator("#prepared-review")).to_be_hidden()
        expect(page.locator("#prepared-settings")).to_be_empty()
        # Same-token admission loss must erase drafts and reject a late Review.
        page.locator("#prepared-mac").fill(MAC_ADDRESS)
        page.locator("#prepared-name").fill("PRIVATE DRAFT")
        page.locator("#prepared-profile").select_option("prepared-test")
        review_button.click()
        def lose_access(route):
            route.fulfill(status=403, content_type="application/json", body=json.dumps({
                "registration_required": True, "error": "Fixture access lost"}))
        page.route("**/api/pending-devices", lose_access)
        page.locator("#prepared-refresh").click()
        expect(page.locator("#private-dashboard")).to_be_hidden()
        assert len(held) == 1
        held.pop().fulfill(content_type="application/json", body=json.dumps({"profile": {"name": "STALE PRIVATE"}}))
        page.unroute("**/api/pending-devices", lose_access)
        page.unroute("**/api/pending-devices/review?*", delay_review)
        expect(page.locator("#prepared-mac")).to_have_value("")
        expect(page.locator("#prepared-name")).to_have_value("")
        expect(page.locator("#prepared-profile")).to_have_value("")
        page.locator("#open-admin-credentials").click()
        page.locator("#check-admin-token").click()
        expect(page.locator("#private-dashboard")).to_be_visible()
        page.locator("#close-credentials").click()
        expect(page.locator("#prepared-mac")).to_have_value("")
        expect(page.locator("#prepared-name")).to_have_value("")
        expect(page.locator("#prepared-review")).to_be_hidden()
    row.get_by_role("button", name=re.compile("^Delete")).click()
    page.locator("dialog[open]").get_by_role("button", name="Delete choice").click()
    expect(card.locator("[data-prepared-mac]")).to_have_count(0)
    expect(page.locator("#prepared-title")).to_be_focused()
    if width == 1440:
        # The list is bounded without creating 256 records or running another suite.
        choices = [{"mac_address": f"02:00:00:01:00:{index:02x}", "action": "protect", "revision": index + 1,
                    "display_name": f"Prepared {index}"} for index in range(21)]
        def list_page(route):
            route.fulfill(content_type="application/json", body=json.dumps({"pending_devices": choices}))
        page.route("**/api/pending-devices", list_page)
        page.locator("#prepared-refresh").click()
        expect(card.locator("[data-prepared-mac]")).to_have_count(20)
        expect(card).to_contain_text("Prepared protection — use unavailable")
        page.locator("#prepared-next").click()
        expect(card.locator("[data-prepared-mac]")).to_have_count(1)
        expect(page.locator("#prepared-note")).to_contain_text("21–21 of 21")
        page.unroute("**/api/pending-devices", list_page)
    assert not errors, errors
    context.close()


def run():
    process, port = start_fixture()
    try:
        Path("test-results/prepared-profiles").mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"))
            try:
                for width, height in ((1440, 1000), (320, 568), (375, 812), (390, 844)):
                    check(browser, f"http://127.0.0.1:{port}", width, height)
            finally:
                browser.close()
        print("Prepared profile UI passed at desktop and 320/375/390 widths, including large text, focus and axe.")
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
