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
        delete = page.locator("#device-table tr", has_text="Test phone").get_by_role(
            "button", name="Delete", exact=True)
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
