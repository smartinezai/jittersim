"""Check that choosing Check token again starts a new 24 hours for a remembered token.

The page runs on a fake clock so the journey can move a day on in a moment.
"""

import json
import os
import re
import selectors
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

TOKEN = "browser-test"
KEY = "jittersim.remembered.admin-token"
CHOICE_KEY = "jittersim.remembered.duration"
MINUTE_MS = 60 * 1000
HOUR_MS = 60 * MINUTE_MS
DAY_MS = 24 * HOUR_MS
# A Friday morning so the day in the note changes as the clock moves on.
START = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
# The note the page should show for an end time, worked out in the page's own time zone.
UNTIL_NOTE = """(expires) => "Remembered until " + new Date(expires).toLocaleString(
  "en-GB", {weekday: "short", hour: "2-digit", minute: "2-digit"}) + "."
"""


def saved_entry(page):
    raw = page.evaluate("(key) => window.localStorage.getItem(key)", KEY)
    return None if raw is None else json.loads(raw)


def until_note(page, expires):
    return page.evaluate(UNTIL_NOTE, expires)


def check_token(page):
    """Type the token as a person would and choose Check token."""

    state = page.locator("#admin-credential-state")
    page.locator("#admin-token").fill(TOKEN)
    expect(state).to_have_text("Check needed")
    page.locator("#check-admin-token").click()
    expect(state).to_have_text("Accepted")
    expect(page.locator("#token-check-status")).to_contain_text("Token accepted")


def assert_a_day_from_now(page, entry):
    left = entry["expires"] - page.evaluate("Date.now()")
    assert DAY_MS - 2 * MINUTE_MS < left <= DAY_MS, left


def run_width(browser, base, width, height):
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.clock.install(time=START)
    page.goto(base)
    box = page.locator("#remember-admin-token")
    note = page.locator("#remember-admin-token-note")

    page.locator("#open-admin-credentials").click()
    # Every browser starts with 24 hours and a plain warning about shared computers.
    duration = page.locator("#remember-admin-duration")
    expect(duration).to_have_value("24")
    expect(duration.locator("option")).to_have_text(["24 hours", "7 days", "30 days"])
    expect(page.locator("#remember-length-note")).to_contain_text("Longer is less safe on a shared computer")
    box.check()
    expect(note).to_have_text("Will be remembered for 24 hours once accepted.")
    check_token(page)
    first = saved_entry(page)
    assert first is not None and first["value"] == TOKEN, first
    assert sorted(first) == ["expires", "value"], first
    assert_a_day_from_now(page, first)
    first_note = until_note(page, first["expires"])
    expect(note).to_have_text(first_note)

    # 20 hours later the same token is entered and checked again.
    page.clock.fast_forward("20:00:00")
    expect(box).to_be_checked()
    expect(note).to_have_text(first_note)
    check_token(page)
    second = saved_entry(page)
    assert second is not None and second["value"] == TOKEN, second
    assert second["expires"] - first["expires"] >= 20 * HOUR_MS - MINUTE_MS, (first, second)
    assert_a_day_from_now(page, second)
    second_note = until_note(page, second["expires"])
    assert second_note != first_note
    expect(note).to_have_text(second_note)
    page.screenshot(path=f"test-results/token-remember-{width}.png")

    # 25 hours after the first check: the first 24 hours are over but the new ones are not.
    page.clock.fast_forward("05:00:00")
    expect(box).to_be_checked()
    expect(note).to_have_text(second_note)
    assert saved_entry(page) == second

    # 45 hours after the first check and 25 after the second the token is forgotten.
    page.clock.fast_forward("20:00:00")
    expect(note).to_have_text("Forgot the saved token after 24 hours.")
    expect(box).not_to_be_checked()
    assert saved_entry(page) is None
    expect(page.locator("#admin-token")).to_have_value(TOKEN)

    # A longer time can be chosen. The choice belongs to this browser and the token keeps it.
    duration.select_option("168")
    assert page.evaluate("(key) => window.localStorage.getItem(key)", CHOICE_KEY) == "168"
    box.check()
    expect(note).to_contain_text("Remembered until")
    # A week away needs the date. A weekday alone could mean this week or next month.
    expect(note).to_contain_text(re.compile(r"Remembered until \w{3},? \d{1,2} \w{3}"))
    week = saved_entry(page)
    assert week["value"] == TOKEN and week["hours"] == 168, week
    assert 7 * DAY_MS - 2 * MINUTE_MS < week["expires"] - page.evaluate("Date.now()") <= 7 * DAY_MS, week
    page.clock.fast_forward(6 * DAY_MS)
    expect(box).to_be_checked()
    assert saved_entry(page) == week
    # Choosing 30 days starts the new time at once. Going back to 24 hours shortens it.
    duration.select_option("720")
    expect(note).to_contain_text("Remembered until")
    month = saved_entry(page)
    assert month["hours"] == 720, month
    duration.select_option("24")
    plain = saved_entry(page)
    assert "hours" not in plain and sorted(plain) == ["expires", "value"], plain
    assert_a_day_from_now(page, plain)
    page.clock.fast_forward(25 * HOUR_MS)
    expect(note).to_have_text("Forgot the saved token after 24 hours.")
    assert saved_entry(page) is None
    page.screenshot(path=f"test-results/token-remember-choice-{width}.png")

    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
    assert not errors, errors
    context.close()


def run():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Token memory dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                for width, height in ((1920, 1000), (390, 844)):
                    run_width(browser, f"http://127.0.0.1:{port}", width, height)
                print("Token memory acceptance passed at desktop and phone widths.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
