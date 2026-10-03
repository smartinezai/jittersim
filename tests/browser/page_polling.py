"""Count what the dashboard asks the Pi for and check the page copes with a slow or busy Pi.

The page runs on a fake clock so a minute of polling takes a moment. The numbers printed here
are requests per simulated minute for each page, with the tab visible and with it hidden.
"""

import collections
import json
import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

TOKEN = "browser-test"
PAGES = ("devices", "profiles", "schedules", "router", "traffic", "access", "measurements",
         "activity", "diagnostics")
# The most requests per simulated minute each page may make while it is shown and nothing is
# being edited. These sit a little above what the page makes now, so they catch a poller that
# starts asking more, not a small change in timing.
BUDGET = {"devices": 120, "profiles": 80, "schedules": 195, "router": 105, "traffic": 130,
          "access": 110, "measurements": 95, "activity": 95, "diagnostics": 100}


def start_fixture():
    return subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True
    )


def read_port(process):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=15):
            raise RuntimeError("Polling dashboard fixture did not start")
        return int(process.stdout.readline().strip())


def unlock(page):
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill(TOKEN)
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    page.locator("#close-credentials").click()


OPEN = collections.Counter()


def advance(page, seconds, patient=True):
    """Move the page clock on a second at a time. When patient, each answer has arrived before
    the next second starts, so the counts do not depend on how fast this machine is."""

    for _ in range(int(seconds)):
        page.clock.run_for(1000)
        page.wait_for_timeout(5)
        if not patient:
            page.wait_for_timeout(30)
            continue
        for _ in range(100):
            if OPEN["now"] <= 0:
                break
            page.wait_for_timeout(10)


def settle(page):
    """Let the requests that follow a page change finish."""

    page.wait_for_timeout(700)


def watch(page):
    seen = collections.Counter()

    def on_request(request):
        path = request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        seen["/" + path] += 1

    def opened(request):
        OPEN["now"] += 1

    def closed(request):
        OPEN["now"] -= 1

    page.on("request", on_request)
    page.on("request", opened)
    page.on("requestfinished", closed)
    page.on("requestfailed", closed)
    return seen


def go_to(page, name):
    page.locator(f'[data-page-link="{name}"]').first.click()
    settle(page)


def requests_per_minute(page, seen, name, hidden=False):
    go_to(page, name)
    if hidden:
        set_hidden(page, True)
    advance(page, 3)
    seen.clear()
    advance(page, 60)
    total = sum(seen.values())
    top = seen.most_common(12)
    if hidden:
        set_hidden(page, False)
    return total, top


def set_hidden(page, hidden):
    page.evaluate(
        """(hidden) => {
          Object.defineProperty(document, "hidden", {configurable: true, get: () => hidden});
          Object.defineProperty(document, "visibilityState",
            {configurable: true, get: () => hidden ? "hidden" : "visible"});
          document.dispatchEvent(new Event("visibilitychange"));
        }""",
        hidden,
    )


def measure(page, seen):
    table = {}
    for name in PAGES:
        table[name] = requests_per_minute(page, seen, name)
    go_to(page, "devices")
    table["hidden"] = requests_per_minute(page, seen, "devices", hidden=True)
    return table



def json_reply(route, status, body):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def check_hidden_tab(page, seen):
    """A tab nobody can see asks for nothing, and asks again as soon as it is shown."""

    go_to(page, "devices")
    set_hidden(page, True)
    advance(page, 2)
    seen.clear()
    advance(page, 30)
    assert sum(seen.values()) == 0, dict(seen)
    set_hidden(page, False)
    page.wait_for_timeout(800)
    assert seen["/api/connected-devices"] >= 1, dict(seen)
    assert seen["/api/health"] >= 1, dict(seen)


def check_traffic_only_on_traffic_page(page, seen):
    """The counters are only sampled while the Traffic page is shown."""

    go_to(page, "devices")
    advance(page, 3)
    seen.clear()
    advance(page, 20)
    assert seen["/api/traffic"] == 0, dict(seen)
    go_to(page, "traffic")
    advance(page, 3)
    seen.clear()
    advance(page, 20)
    assert 6 <= seen["/api/traffic"] <= 11, dict(seen)
    go_to(page, "schedules")
    advance(page, 3)
    seen.clear()
    advance(page, 20)
    assert seen["/api/traffic"] == 0, dict(seen)


def check_busy_helper_keeps_the_list(page, seen):
    """A busy helper answers 502. The devices stay on screen until three answers in a row fail."""

    go_to(page, "devices")
    advance(page, 6)
    rows = page.locator("#connected-devices .connected-row")
    before = rows.count()
    assert before >= 1, "the fixture shows no connected devices"
    page.evaluate("document.getElementById('connected-devices').firstElementChild.dataset.kept = 'yes'")
    busy = {"on": True}

    def answer(route):
        if busy["on"]:
            json_reply(route, 502, {"error": "network helper is busy"})
        else:
            route.continue_()

    page.route("**/api/connected-devices", answer)
    advance(page, 6)
    assert rows.count() == before
    assert page.locator("#connected-devices .connected-row").first.get_attribute("data-kept") == "yes"
    expect(page.locator("#connected-refresh-status")).to_contain_text("Updating")
    assert page.locator("#connected-count").inner_text() != "Unavailable"
    advance(page, 15)
    expect(page.locator("#connected-count")).to_have_text("Unavailable")
    busy["on"] = False
    advance(page, 6)
    expect(page.locator("#connected-count")).not_to_have_text("Unavailable")
    page.unroute("**/api/connected-devices")


def check_busy_helper_keeps_the_graph(page, seen):
    """One failed sample does not wipe the traffic graph."""

    counter = {"n": 0, "fail": False}

    def answer(route):
        if counter["fail"]:
            json_reply(route, 502, {"error": "live traffic counters are unavailable"})
            return
        counter["n"] += 1
        step = counter["n"] * 250000
        totals = {"upload_bytes": step, "download_bytes": step * 2,
                  "upload_packets": counter["n"] * 100, "download_packets": counter["n"] * 200}
        json_reply(route, 200, {
            "available": True, "sampled_at": counter["n"] * 2.0, "devices": {"mock": totals},
            "queues": {"mock": {"dropped": 0, "queued": 0}}, "isp_limits": {},
            "profile_limits_available": False, "profile_limits": {},
        })

    page.route("**/api/traffic", answer)
    go_to(page, "traffic")
    advance(page, 10)
    rate = page.locator("#traffic-upload-rate")
    assert rate.inner_text() != "—", rate.inner_text()
    shown = rate.inner_text()
    counter["fail"] = True
    advance(page, 3)
    assert rate.inner_text() == shown, rate.inner_text()
    expect(page.locator("#traffic-status")).to_contain_text("Updating")
    counter["fail"] = False
    advance(page, 3)
    page.unroute("**/api/traffic")
    go_to(page, "devices")


def check_hung_request_does_not_freeze(page, seen):
    """A request the Pi never answers is given up after 15 seconds and asked for again."""

    go_to(page, "devices")
    advance(page, 6)
    page.route("**/api/general-profile", lambda route: None)
    seen.clear()
    advance(page, 60, patient=False)
    asked = seen["/api/general-profile"]
    assert 3 <= asked <= 6, asked
    assert seen["/api/connected-devices"] >= 8, dict(seen)
    expect(page.locator("#runtime-summary")).to_contain_text("Pi not answering, retrying")
    page.unroute("**/api/general-profile")
    advance(page, 7)
    expect(page.locator("#runtime-summary")).not_to_contain_text("Pi not answering")


def check_focus_survives_polling(page, seen):
    """A poll never takes focus away from a button or swallows a click."""

    go_to(page, "devices")
    advance(page, 6)
    first = page.locator("#connected-devices .connected-row").first
    first.evaluate("(row) => { row.dataset.stable = 'yes'; }")
    advance(page, 12)
    assert page.locator("#connected-devices .connected-row").first.get_attribute("data-stable") == "yes"
    target = first.locator("button:not([disabled]), summary, select:not([disabled]), input:not([disabled])")
    assert target.count() >= 1, first.inner_html()
    target.first.focus()
    advance(page, 12)
    assert page.evaluate(
        "document.querySelector('#connected-devices').contains(document.activeElement)"), "focus was lost"
    assert page.locator("#connected-devices .connected-row").first.get_attribute("data-stable") == "yes"
    page.evaluate("document.activeElement.blur()")


def check_panic_stays_enabled(page, seen):
    """Panic works while an apply is running."""

    go_to(page, "devices")
    held = []
    page.route("**/api/apply", lambda route: held.append(route))
    page.locator("#apply-button").click()
    page.wait_for_timeout(300)
    assert held, "the apply request was not sent"
    assert page.locator("#apply-button").is_disabled()
    expect(page.locator("#panic-button")).to_be_enabled()
    seen.clear()
    page.locator("#panic-button").click()
    expect(page.locator("#message")).to_contain_text("Panic is queued and runs as soon as the current change finishes")
    page.wait_for_timeout(300)
    assert seen["/api/normal-access"] == 1, dict(seen)
    expect(page.locator("#panic-button")).to_be_enabled()
    for route in held:
        route.continue_()
    page.unroute("**/api/apply")
    expect(page.locator("#apply-button")).to_be_enabled(timeout=15000)
    expect(page.locator("#panic-button")).to_be_enabled()


def check_token_form(page):
    """The token sits in a form with a hidden name so the Keychain can store it."""

    page.locator("#open-admin-credentials").click()
    form = page.locator("form#admin-token-form")
    expect(form).to_have_count(1)
    token = form.locator("#admin-token")
    assert token.get_attribute("autocomplete") == "current-password"
    name = form.locator("input[autocomplete='username']")
    expect(name).to_have_count(1)
    page.locator("#close-credentials").click()


def check_one_save_is_small(page, seen):
    """Saving one thing reads back far less than the whole dashboard."""

    go_to(page, "profiles")
    advance(page, 2)
    page.locator("#profile-name").fill("Slow bus")
    page.locator("#profile-id").fill("slow-bus")
    seen.clear()
    page.locator("#profile-form button[type='submit']").click()
    page.wait_for_timeout(2500)
    reads = sum(count for path, count in seen.items() if path.startswith("/api/") and
                path != "/api/profiles")
    assert 0 < seen["/api/profiles"] <= 3, dict(seen)
    assert reads <= 14, (reads, dict(seen))


def run():
    process = start_fixture()
    try:
        port = read_port(process)
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.clock.install()
                page.goto(f"http://127.0.0.1:{port}")
                unlock(page)
                settle(page)
                seen = watch(page)
                table = measure(page, seen)
                for key, (total, top) in table.items():
                    names = ", ".join(f"{n.split('/')[-1]} {c}" for n, c in top)
                    print(f"{key:13} {total:4} per minute  {names}")
                    if key in BUDGET and not os.environ.get("POLL_NO_BUDGET"):
                        assert total <= BUDGET[key], (key, total, top)
                assert table["hidden"][0] == 0, table["hidden"]
                check_token_form(page)
                for check in (check_hidden_tab, check_traffic_only_on_traffic_page,
                              check_busy_helper_keeps_the_list, check_busy_helper_keeps_the_graph,
                              check_hung_request_does_not_freeze, check_focus_survives_polling,
                              check_panic_stays_enabled, check_one_save_is_small):
                    check(page, seen)
                    print("ok", check.__name__)
                assert not errors, errors
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
