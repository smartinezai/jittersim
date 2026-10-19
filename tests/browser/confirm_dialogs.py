"""The in-page question that replaces the browser's own confirm box, and what it must do.

`confirmWorkflow` opens a real <dialog>: Cancel has focus first, the other button names the
action, Escape answers no, and focus goes back to the control that asked. These helpers keep the
journeys short and make every converted site check the same things.
"""

import json
import re

from playwright.sync_api import expect


def _lines(value):
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def open_question(page, *, verb=None, title=None, text=None, absent=()):
    """Check the open question and return it. Nothing is answered yet."""

    box = page.get_by_role("dialog")
    expect(box).to_have_count(1)
    expect(box).to_be_visible()
    if title is not None:
        # The heading names the dialog, and the question is its description.
        expect(box.get_by_role("heading", level=2)).to_have_text(title)
        expect(page.get_by_role("dialog", name=title, exact=True)).to_be_visible()
        # The description must not say the heading again: a screen reader reads both.
        assert title not in box.locator("p").inner_text(), (title, box.locator("p").inner_text())
    for fragment in _lines(text):
        expect(box).to_contain_text(fragment)
    shown = box.inner_text()
    for fragment in _lines(absent):
        assert fragment not in shown, (fragment, shown)
    # Cancel is the safe first stop, and the other button says what it does.
    expect(box.get_by_role("button", name="Cancel", exact=True)).to_be_focused()
    if verb is not None:
        expect(box.get_by_role("button", name=verb, exact=True)).to_be_visible()
        expect(box.get_by_role("button")).to_have_count(2)
    return box


def answer_confirm(page, verb, **checks):
    """Check the open question, then press the button that names the action."""

    box = open_question(page, verb=verb, **checks)
    box.get_by_role("button", name=verb, exact=True).click()
    expect(page.get_by_role("dialog")).to_have_count(0)


def cancel_with_escape(page, opener, verb=None, **checks):
    """Check the open question, press Escape and expect focus back on the opener."""

    open_question(page, verb=verb, **checks)
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(opener).to_be_focused()


def cancel_with_button(page, opener, **checks):
    """Check the open question, press Cancel and expect focus back on the opener."""

    box = open_question(page, **checks)
    box.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(opener).to_be_focused()


def stub_writes(page, paths):
    """Answer the given write paths with an empty success and record what was sent."""

    calls = []

    def handle(route):
        request = route.request
        calls.append((request.url.split("/api/", 1)[1], request.post_data_json))
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"live_applied": False})
        )

    for path in paths:
        page.route("**" + path, handle)
    return calls


def check_destructive_confirms(page):
    """Delete profile, delete device, clear the log and import settings ask first.

    The Pi's answers are stubbed so nothing real is removed. Each action is cancelled with Escape,
    which sends nothing and returns focus, and then confirmed with the verb on its button.
    """

    paths = ("/api/profiles/delete", "/api/devices/delete", "/api/activity/clear", "/api/import")
    page.set_viewport_size({"width": 1440, "height": 1000})
    calls = stub_writes(page, paths)
    try:
        page.get_by_role("link", name="Profiles", exact=True).click()
        delete = page.locator(".saved-profile-row", has_text="Slow connection").get_by_role(
            "button", name="Delete Slow connection", exact=True)
        delete.click()
        cancel_with_escape(page, delete, verb="Delete profile", title="Delete this profile?",
                           text="Delete the profile Slow connection?")
        assert not calls, calls
        delete.click()
        answer_confirm(page, "Delete profile", title="Delete this profile?")
        expect(page.locator("#message")).to_contain_text("Profile deleted.")
        assert calls == [("profiles/delete", {"id": "slow"})], calls
        del calls[:]

        page.get_by_role("link", name=re.compile("^Devices")).click()
        # Every button of a row names the row. "Delete" alone does not say which device.
        phone_row = page.locator("#device-table tr", has_text="Test phone")
        for visible, name in (("Open controls", "Open controls for Test phone"),
                              ("Edit", "Edit Test phone"), ("Delete", "Delete Test phone")):
            expect(phone_row.get_by_role("button", name=name, exact=True)).to_have_text(visible)
        delete = page.locator("#device-table tr", has_text="Test phone").get_by_role(
            "button", name="Delete Test phone", exact=True)
        delete.click()
        cancel_with_escape(page, delete, verb="Delete device", title="Delete this device?",
                           text="Its schedules, timers, progressions")
        assert not calls, calls
        delete.click()
        answer_confirm(page, "Delete device", title="Delete this device?")
        expect(page.locator("#message")).to_contain_text("Device deleted.")
        assert [name for name, _ in calls] == ["devices/delete"], calls
        del calls[:]

        page.get_by_role("link", name="Activity", exact=True).click()
        clear = page.locator("#activity-clear")
        clear.click()
        cancel_with_escape(page, clear, verb="Clear all events", title="Clear the activity log?",
                           text="This can't be undone.")
        assert not calls, calls
        clear.click()
        answer_confirm(page, "Clear all events", title="Clear the activity log?")
        expect(page.locator("#message")).to_contain_text("Activity log cleared.")
        assert [name for name, _ in calls] == ["activity/clear"], calls
        del calls[:]

        page.get_by_role("link", name="Diagnostics", exact=True).click()
        toggle = page.locator("h2 .card-toggle", has_text="Backup and action history")
        if toggle.get_attribute("aria-expanded") == "false":
            toggle.click()
        importer = page.locator("#import-settings-button")
        chosen = {
            "name": "settings.json", "mimeType": "application/json", "buffer": b'{"marker": 1}',
        }
        with page.expect_file_chooser() as chooser:
            importer.click()
        chooser.value.set_files(chosen)
        cancel_with_escape(page, importer, verb="Replace settings", title="Replace all settings?",
                           text="with the settings in settings.json?")
        assert not calls, calls
        expect(importer).to_be_enabled()
        with page.expect_file_chooser() as chooser:
            importer.click()
        chooser.value.set_files(chosen)
        answer_confirm(page, "Replace settings", title="Replace all settings?")
        expect(page.locator("#message")).to_contain_text("Settings imported.")
        assert calls == [("import", {"marker": 1})], calls
        expect(importer).to_be_enabled()
    finally:
        for path in paths:
            page.unroute("**" + path)


MEASUREMENT = {
    "format": "jittersim-measurement", "version": 1, "label": "Confirm run", "target": "1.1.1.1",
    "summary": {"sent": 2, "loss_percent": 0, "rtt_avg_ms": 12}, "samples": [{"rtt_ms": 10}, {"rtt_ms": 14}],
}
CLEARED = "Add result files to compare runs."


def add_measurement(page, rows):
    page.locator("#measurement-files").set_input_files({
        "name": "run.json", "mimeType": "application/json", "buffer": json.dumps(MEASUREMENT).encode(),
    })
    expect(page.locator("#measurement-table tbody tr")).to_have_count(rows)


def record_live_regions(page):
    """Record every write into a live region from now on (status, alert, log, an <output> or an
    aria-live that is not off). `window.spoken` lists [id, text] in order. A screen reader says each
    of these writes, so a result that lands in two regions is said twice."""

    page.evaluate("""() => {
      window.spoken = [];
      const live = (node) => {
        for (let el = node; el && el.getAttribute; el = el.parentElement) {
          const explicit = el.getAttribute('aria-live');
          if (explicit) return explicit === 'off' ? null : el;
          if (['status', 'alert', 'log'].includes(el.getAttribute('role')) || el.tagName === 'OUTPUT') return el;
        }
        return null;
      };
      new MutationObserver((records) => {
        for (const record of records) {
          const target = record.target.nodeType === Node.TEXT_NODE ? record.target.parentElement : record.target;
          const region = live(target);
          if (region && region.textContent) window.spoken.push([region.id, region.textContent]);
        }
      }).observe(document.body, {childList: true, characterData: true, subtree: true});
    }""")


def regions_that_said(page, text):
    """The ids of the live regions that had exactly `text` written into them."""

    return sorted({region for region, said in page.evaluate("window.spoken") if said == text})


def stub_clipboard(page, works):
    """Make navigator.clipboard.writeText succeed, or fail as it does when the browser refuses."""

    page.evaluate("""(works) => {
      Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
        writeText: () => works ? Promise.resolve() : Promise.reject(new DOMException('Denied', 'NotAllowedError')),
      }});
    }""", works)


def check_revoke_and_clear_confirms(page):
    """Revoke all homepage pairings and Clear on the Measurements page ask first and say the result.

    The Pi's answer is stubbed so nothing real is revoked. Revoke says its result once: the
    <output> that shows a pairing code is a live region of its own and must not get it as well.
    Clear asks only about runs that exist in the page alone: a table that was copied is kept, so
    it clears without a question. A copy that the browser refused keeps nothing.
    """

    page.set_viewport_size({"width": 1440, "height": 1000})
    calls = stub_writes(page, ("/api/homepage/revoke",))
    try:
        page.get_by_role("link", name="Profiles", exact=True).click()
        page.locator("summary", has_text="Pair homepage test extension").click()
        revoke = page.locator("#homepage-revoke")
        revoke.click()
        cancel_with_escape(page, revoke, verb="Revoke all pairings",
                           title="Revoke all homepage pairings?", text="until it is paired again")
        assert not calls, calls
        expect(revoke).to_be_enabled()
        revoke.click()
        cancel_with_button(page, revoke, title="Revoke all homepage pairings?")
        assert not calls, calls
        revoke.click()
        record_live_regions(page)
        answer_confirm(page, "Revoke all pairings", title="Revoke all homepage pairings?")
        # The result is said once through the one announcer, and the button is ready again.
        expect(page.locator("#action-announcer")).to_have_text("All homepage pairings revoked.")
        assert regions_that_said(page, "All homepage pairings revoked.") == ["action-announcer"], \
            page.evaluate("window.spoken")
        expect(page.locator("#homepage-code")).to_have_text("")
        assert calls == [("homepage/revoke", {})], calls
        expect(revoke).to_be_enabled()
        expect(revoke).to_be_focused()
    finally:
        page.unroute("**/api/homepage/revoke")

    page.get_by_role("link", name="Measurements", exact=True).click()
    page.evaluate("() => { measurementRuns = []; keptMeasurementRuns = measurementRuns; renderMeasurements(); }")
    clear = page.locator("#clear-measurements")
    expect(clear).to_be_disabled()
    add_measurement(page, 1)
    expect(clear).to_be_enabled()
    clear.click()
    cancel_with_escape(page, clear, verb="Clear table", title="Clear the measurement table?",
                       text=["1 run that exist only in this page", "stay on your computer"])
    expect(page.locator("#measurement-table tbody tr")).to_have_count(1)
    clear.click()
    cancel_with_button(page, clear, title="Clear the measurement table?")
    expect(page.locator("#measurement-table tbody tr")).to_have_count(1)
    clear.click()
    answer_confirm(page, "Clear table", title="Clear the measurement table?")
    expect(page.locator("#measurement-table")).to_have_text(CLEARED)
    expect(page.locator("#action-announcer")).to_have_text("Measurement table cleared.")
    expect(clear).to_be_disabled()
    # Runs that were copied are kept, so there is nothing to lose.
    stub_clipboard(page, works=True)
    add_measurement(page, 1)
    page.locator("#copy-measurements").click()
    expect(page.locator("#message")).to_have_text("Measurement table copied.")
    clear.click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.locator("#measurement-table")).to_have_text(CLEARED)
    # A new run after that is not kept again.
    add_measurement(page, 1)
    clear.click()
    open_question(page, verb="Clear table", title="Clear the measurement table?")
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    # A copy that the browser refused is not a copy. The text is offered to select instead, and
    # the runs still exist only in the page, so Clear asks and a no keeps them.
    stub_clipboard(page, works=False)
    page.locator("#copy-measurements").click()
    expect(page.locator("#message")).to_contain_text("Clipboard access is unavailable")
    expect(page.locator("#report-copy-fallback")).to_be_visible()
    clear.click()
    cancel_with_escape(page, clear, verb="Clear table", title="Clear the measurement table?",
                       text="1 run that exist only in this page")
    expect(page.locator("#measurement-table tbody tr")).to_have_count(1)
    page.evaluate("""() => {
      measurementRuns = []; keptMeasurementRuns = measurementRuns; renderMeasurements();
      delete navigator.clipboard; document.getElementById('report-copy-fallback').hidden = true;
    }""")
