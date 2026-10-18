"""Polled lists are quiet, results are said once, and a refresh does not take focus away.

A screen reader repeats every write to a live region, so a list that the page rebuilds every few
seconds must never sit in one. The page has two live regions, `#action-announcer`
(role="status") and `#action-alert` (role="alert"), and nothing else speaks on its own.

The page runs on a fake clock, so a minute of polling takes a moment.
"""

import json
import os
import selectors
import subprocess
import sys

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import expect, sync_playwright

from tests.browser.route_support import keep_interception_on, release_route, watched

TOKEN = "browser-test"
HEADERS = {"Authorization": "Bearer " + TOKEN}
DEVICE = "alice"
PAGES = ("devices", "profiles", "schedules", "router", "traffic", "access", "measurements",
         "activity", "diagnostics", "updates")

# Records every write below a live region. A write is a text change or a child added or removed.
# An element speaks when it, or the nearest ancestor with a say about it, is a live region.
# `hidden` and `aria-hidden` branches are not read, and aria-live="off" ends the search.
WATCH = """() => {
  const speaking = (node) => {
    for (let el = node.nodeType === 1 ? node : node.parentElement; el; el = el.parentElement) {
      if (el.hidden || el.getAttribute('aria-hidden') === 'true') return null;
      const live = el.getAttribute('aria-live');
      if (live === 'off') return null;
      if (live === 'polite' || live === 'assertive') return el;
      if (['status', 'alert', 'log'].includes(el.getAttribute('role'))) return el;
    }
    return null;
  };
  const name = (el) => el.id || (el.className && String(el.className).split(' ')[0]) || el.tagName;
  window.liveWrites = [];
  window.liveWatch && window.liveWatch.disconnect();
  window.liveWatch = new MutationObserver((records) => {
    for (const record of records) {
      if (!record.target.isConnected) continue;
      const region = speaking(record.target);
      if (region) window.liveWrites.push(name(region) + ' (' + record.type + ')');
    }
  });
  window.liveWatch.observe(document.body, {childList: true, characterData: true, subtree: true});
}"""


# The live regions that hold a text right now. A region inside a hidden or aria-hidden branch is
# not read, so it is not counted.
SPEAKERS = """(text) => [...document.querySelectorAll(
  '[role="status"], [role="alert"], [role="log"], [aria-live="polite"], [aria-live="assertive"]')]
  .filter((node) => !node.closest('[hidden], [aria-hidden="true"]')
                    && node.textContent.includes(text))
  .map((node) => node.id)"""


def speakers(page, text):
    return page.evaluate(SPEAKERS, text)


def start_fixture():
    env = {
        **os.environ,
        "JITTERSIM_BROWSER_TEST_SEED": "1",
        "JITTERSIM_BROWSER_TEST_SCHEDULER": "1",
        "JITTERSIM_BROWSER_TEST_OBSERVATIONS": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True, env=env
    )
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=15):
            process.terminate()
            raise RuntimeError("Live region dashboard fixture did not start")
        return process, int(process.stdout.readline().strip())


def unlock(page):
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill(TOKEN)
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    page.locator("#close-credentials").click()


def advance(page, seconds):
    """Move the page clock on a quarter of a second at a time, so that each answer has arrived
    before the next step and the pollers do not all fire in the same instant (the Pi's helper
    refuses a client scan that overlaps another read, and a real page never lines up so exactly)."""

    for _ in range(int(seconds) * 4):
        page.clock.run_for(250)
        page.wait_for_timeout(15)


def post(page, base, path, data):
    response = page.request.post(base + path, headers=HEADERS, data=data)
    assert response.ok, (path, response.status, response.text())
    return response.json()


def seed(page, base):
    """Give every polled list something to show, so rows are rebuilt and not only an empty text."""

    post(page, base, "/api/groups", {"id": "family", "name": "Family", "device_ids": [DEVICE]})
    post(page, base, "/api/schedules", {
        "id": "work", "device_id": DEVICE, "group_id": None, "everyone_else": False,
        "profile_id": "slow", "days_of_week": [0, 1, 2, 3, 4], "start_minute": 540,
        "end_minute": 1020, "enabled": True,
    })
    post(page, base, "/api/timers", {
        "device_id": DEVICE, "profile_id": "slow", "duration_minutes": 30,
    })
    post(page, base, "/api/data-limits", {
        "device_id": DEVICE, "profile_id": "slow", "limit_bytes": 2 * 1000 ** 3, "enabled": True,
        "reset_mode": "daily", "reset_interval_seconds": 0, "reset_times": [480],
    })
    post(page, base, "/api/service-quotas", {
        "id": "alice-icloud", "name": "iCloud", "device_id": DEVICE, "profile_id": "slow",
        "limit_bytes": 5 * 1000 ** 2, "domains": ["gateway.icloud.com"],
    })
    post(page, base, "/api/unaffected-devices", {
        "mac_address": "02:00:00:00:00:55", "display_name": "Mine",
    })
    page.evaluate("refresh()")


def check_pollers_are_quiet(page, base, warm_up=6, seconds=12):
    """Twelve seconds on each page is two reads of everything it polls and six traffic samples.

    The first reads after a page opens fill it in, and that is allowed to speak (the verdicts of
    the impact card, for one). The window starts after them. An answer with an error status is a
    real change that a live region may report, so a window that saw one is repeated.
    """

    # The helper refuses a client scan that overlaps another change, and a fake clock fires the
    # polls together, so the scan is answered from one good answer. What the page does with the
    # answer is still checked. The scan itself is not what is under test here.
    scan = None
    for _ in range(50):
        response = page.request.get(base + "/api/connected-devices", headers=HEADERS)
        if response.ok:
            scan = response.json()
            break
        page.wait_for_timeout(100)
    assert scan is not None, "the client scan never answered"
    page.route("**/api/connected-devices", lambda route: route.fulfill(json=scan))
    failures = []
    page.on("response", lambda response: failures.append(response.url)
            if "/api/" in response.url and response.status >= 400 else None)
    noisy = {}
    for name in PAGES:
        page.locator(f'[data-page-link="{name}"]').click()
        advance(page, warm_up)
        for attempt in range(3):
            del failures[:]
            page.evaluate(WATCH)
            advance(page, seconds)
            writes = page.evaluate("window.liveWrites")
            if not failures:
                break
            advance(page, warm_up)
        assert not failures, ("the Pi kept answering with errors", name, failures)
        if writes:
            noisy[name] = sorted(set(writes))
    page.evaluate("window.liveWatch.disconnect()")
    assert not noisy, json.dumps(noisy, indent=2)


# Rows with a control that a keyboard user may be standing on while the page refreshes the list.
# (page, list, control inside the list)
FOCUS_CASES = (
    ("devices", "#device-table", "button:text-is('Edit')"),
    ("schedules", "#schedule-list", "button:text-is('Edit')"),
    ("schedules", "#timer-list", "button:text-is('Cancel')"),
    ("schedules", "#data-limit-list", "button:text-is('Reset usage')"),
    ("schedules", "#service-quota-list", "button:text-is('Edit')"),
    ("traffic", "#observations", "button[aria-label^='Block globally mmx-ds']"),
)

PHONE_FOCUS_CASES = (
    ("devices", "#device-cards", "button:has-text('Open Test phone')"),
)

WHO_HAS_FOCUS = """(listSelector) => {
  const node = document.activeElement;
  const list = document.querySelector(listSelector);
  if (!node || !list || !list.contains(node)) return null;
  return [node.tagName, (node.getAttribute('aria-label') || node.textContent || '').trim(),
          Boolean(node.closest('[data-key]'))];
}"""


def check_focus_survives_refresh(page, cases=FOCUS_CASES, rounds=2):
    """A refresh rebuilds the rows, and the control a person stands on must still have focus."""

    for name, listing, control in cases:
        page.locator(f'[data-page-link="{name}"]').click()
        advance(page, 6)
        # A card that starts closed holds nothing a person can stand on.
        closed = page.locator(listing).locator("xpath=ancestor::section[1]").locator(
            "h2 .card-toggle[aria-expanded='false']")
        if closed.count():
            closed.first.click()
        button = page.locator(listing).locator(control).first
        expect(button).to_be_visible()
        button.focus()
        before = page.evaluate(WHO_HAS_FOCUS, listing)
        assert before, (listing, "the control did not take focus")
        for _ in range(rounds):
            advance(page, 6)
            after = page.evaluate(WHO_HAS_FOCUS, listing)
            assert after == before, (listing, before, after)


# Rows with a control that opens a question. (page, list, control inside the list)
QUESTION_CASES = (
    ("devices", "#device-table", "button:text-is('Delete')"),
    ("access", "#access-device-list", "button:has-text('Block ')"),
)


def check_random_website_choices_say_the_last_answer_once(page):
    """Two choices are made before the first answer arrives and the answers come in the other
    order. The line under the table is no live region, so only the choice that is still the latest
    says its answer, once, and the older one says nothing (it must not read the loading note)."""

    page.locator('[data-page-link="traffic"]').click()
    advance(page, 3)
    held = []
    page.route("**/api/random-website-analytics*", lambda route: held.append(route))
    page.evaluate("""() => {
      window.said = [];
      for (const id of ['action-announcer', 'action-alert']) {
        const region = document.getElementById(id);
        new MutationObserver(() => { if (region.textContent) window.said.push(region.textContent); })
          .observe(region, {childList: true, characterData: true, subtree: true});
      }
    }""")
    days = page.locator("#random-website-days")
    # The card starts closed.
    closed = days.locator("xpath=ancestor::section[1]").locator("h2 .card-toggle[aria-expanded='false']")
    if closed.count():
        closed.first.click()
    expect(days).to_be_visible()
    for choice in ("7", "30"):
        days.select_option(choice)
        for _ in range(100):
            if len(held) == int(choice == "30") + 1:
                break
            page.wait_for_timeout(50)
    assert len(held) == 2, "both choices should be waiting for an answer"

    def answer(count):
        return {"rows": [{"device_id": "alice", "display_name": "Test phone", "domain": f"site{n}.example",
                          "kept_count": 1, "removed_count": 1, "days_with_events": 1} for n in range(count)],
                "oldest_available_day_utc": None, "truncated": False}

    held[1].fulfill(json=answer(1))
    expect(page.locator("#random-website-status")).to_contain_text("1 device and website totals")
    held[0].fulfill(json=answer(2))
    page.wait_for_timeout(400)
    said = [text for text in page.evaluate("window.said") if "randomized website" in text or "totals" in text]
    assert said and set(said) == {said[0]}, said
    assert said[0].startswith("1 device and website totals"), said
    assert len(said) == 1, said
    page.unroute("**/api/random-website-analytics*")


def check_a_question_returns_focus_after_its_list_is_drawn_again(page, cases=QUESTION_CASES):
    """A person takes a while to read a question, and the page polls meanwhile, so the list may be
    drawn again under the open question. Cancel and Escape must still give focus to the control of
    that row, which is a new element by then."""

    for name, listing, control in cases:
        page.locator(f'[data-page-link="{name}"]').click()
        advance(page, 6)
        button = page.locator(listing).locator(control).first
        expect(button).to_be_visible()
        label = (button.get_attribute("aria-label") or button.inner_text()).strip()
        button.focus()
        page.evaluate("() => { window.firstOpener = document.activeElement; }")
        button.click()
        expect(page.get_by_role("dialog")).to_have_count(1)
        for _ in range(4):
            advance(page, 6)
            if page.evaluate("!window.firstOpener.isConnected"):
                break
        assert page.evaluate("!window.firstOpener.isConnected"), (
            listing, "the list was not drawn again, so this check proves nothing")
        expect(page.get_by_role("dialog")).to_have_count(1)
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog")).to_have_count(0)
        # The question is answered a moment before it is taken out of the page and focus moves.
        try:
            page.wait_for_function(
                "(listing) => document.querySelector(listing).contains(document.activeElement)",
                arg=listing, timeout=3000)
        except PlaywrightTimeout:
            pass
        after = page.evaluate(WHO_HAS_FOCUS, listing)
        assert after and after[1] == label, (listing, label, after)


def check_a_row_that_left_the_page_has_no_anchor(page):
    """rowAnchor runs for every control that takes focus. A row whose parent is gone (a row that
    was taken out of the page, with the focus event still on its way) has no list to name, and
    asking for it used to throw."""

    anchor = page.evaluate("""() => {
      const row = document.createElement('div'); row.dataset.key = 'gone';
      const button = document.createElement('button'); button.textContent = 'Edit'; row.append(button);
      return rowAnchor(button);
    }""")
    assert anchor is None, anchor


def check_the_loading_note_is_never_announced(page):
    """A choice under the random website table starts a read. When a refresh makes that read stale
    the line still holds its loading note, and the announcer used to say "Loading randomized
    website choices..." as if it were the answer."""

    page.locator('[data-page-link="traffic"]').click()
    advance(page, 3)
    days = page.locator("#random-website-days")
    closed = days.locator("xpath=ancestor::section[1]").locator("h2 .card-toggle[aria-expanded='false']")
    if closed.count():
        closed.first.click()
    expect(days).to_be_visible()
    held = []
    handler = watched(lambda route: held.append(route))
    page.route("**/api/devices", handler)
    page.evaluate("""() => {
      window.said = [];
      for (const id of ['action-announcer', 'action-alert']) {
        const region = document.getElementById(id);
        new MutationObserver(() => { if (region.textContent) window.said.push(region.textContent); })
          .observe(region, {childList: true, characterData: true, subtree: true});
      }
    }""")
    days.select_option("7" if days.input_value() != "7" else "30")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "the choice did not start a read"
    status = page.locator("#random-website-status")
    expect(status).to_have_text("Loading randomized website choices...")
    # A refresh begins and the read that is waiting becomes stale.
    page.evaluate("invalidateAdminTools()")
    for route in held:
        route.continue_()
    page.wait_for_timeout(400)
    release_route(page, "**/api/devices", handler)
    expect(status).to_have_text("Loading randomized website choices...")
    said = page.evaluate("window.said")
    assert not [text for text in said if text.startswith("Loading")], said
    page.evaluate("refresh()")


def check_focus_goes_to_the_list_when_its_row_is_gone(page, base):
    """Somebody else removes a device while the question about it is open. The control that asked
    is gone and so is its twin. Focus goes to the list that held it instead of falling to the top of
    the page."""

    page.locator('[data-page-link="devices"]').click()
    advance(page, 6)
    post(page, base, "/api/devices", {
        "id": "spare", "name": "Spare laptop", "ipv4_address": "192.168.50.99", "profile_id": "slow",
        "mac_address": None,
    })
    page.evaluate("refresh()")
    button = page.locator("#device-table").get_by_role("button", name="Delete Spare laptop", exact=True)
    expect(button).to_be_visible()
    button.focus()
    page.evaluate("() => { window.firstOpener = document.activeElement; }")
    button.click()
    expect(page.get_by_role("dialog")).to_have_count(1)
    assert page.request.post(base + "/api/devices/delete", headers=HEADERS, data={"id": "spare"}).ok
    for _ in range(4):
        advance(page, 6)
        if page.evaluate("!window.firstOpener.isConnected"):
            break
    assert page.evaluate("!window.firstOpener.isConnected"), "the list was not drawn again"
    expect(page.locator("#device-table").get_by_role("button", name="Delete Spare laptop")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    page.wait_for_function(
        "() => document.activeElement && document.activeElement.id === 'device-table'", timeout=3000)


def check_a_full_refresh_is_quiet(page):
    """A save, a delete or Apply makes the page read everything again. That read is not an answer
    to anything the person asked, so no line of the page may speak because of it. The result of the
    action itself goes through the one announcer and is checked by the journeys of each action."""

    noisy = {}
    for name in PAGES:
        page.locator(f'[data-page-link="{name}"]').click()
        advance(page, 3)
        page.evaluate(WATCH)
        page.evaluate("refresh()")
        advance(page, 3)
        writes = page.evaluate("window.liveWrites")
        if writes:
            noisy[name] = sorted(set(writes))
    page.evaluate("window.liveWatch.disconnect()")
    assert not noisy, json.dumps(noisy, indent=2)


def check_long_idle_is_quiet(page, seconds=130):
    """Two minutes with nothing touched. Timers that run every minute write nothing new either,
    for example the note under Remember, which says the same thing each time it is checked."""

    page.set_viewport_size({"width": 1440, "height": 1000})
    page.locator('[data-page-link="devices"]').click()
    page.locator("#open-admin-credentials").click()
    page.locator("#remember-admin-token").check()
    expect(page.locator("#remember-admin-token-note")).to_contain_text("Remembered until")
    page.locator("#close-credentials").click()
    advance(page, 6)
    page.evaluate(WATCH)
    advance(page, seconds)
    writes = page.evaluate("window.liveWrites")
    page.evaluate("window.liveWatch.disconnect()")
    assert not writes, sorted(set(writes))


def run():
    process, port = start_fixture()
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            keep_interception_on(page)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.clock.install()
                page.goto(base)
                unlock(page)
                seed(page, base)
                advance(page, 6)
                check_pollers_are_quiet(page, base)
                check_a_full_refresh_is_quiet(page)
                check_random_website_choices_say_the_last_answer_once(page)
                check_a_question_returns_focus_after_its_list_is_drawn_again(page)
                check_focus_goes_to_the_list_when_its_row_is_gone(page, base)
                check_a_row_that_left_the_page_has_no_anchor(page)
                check_the_loading_note_is_never_announced(page)
                check_long_idle_is_quiet(page)
                check_focus_survives_refresh(page)
                # On a phone the saved devices are cards, and the sidebar is a bar.
                page.set_viewport_size({"width": 390, "height": 844})
                check_focus_survives_refresh(page, PHONE_FOCUS_CASES)
                check_pollers_are_quiet(page, base)
                assert not errors, errors
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
    print("Live region acceptance passed.")


if __name__ == "__main__":
    run()
