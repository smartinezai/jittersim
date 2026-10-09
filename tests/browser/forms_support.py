"""Steps shared by the journeys of the schedule, timer, progression and limit forms.

Each journey starts its own disposable dashboard for every window width, so a width never sees
what the other one saved. The page is signed in with the remembered token, which lets a journey
reload the page and read the saved rows back from the Pi.
"""

import os
import selectors
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.phone import sidebar_control

TOKEN = "browser-test"
HEADERS = {"Authorization": "Bearer " + TOKEN}
WIDTHS = ((1440, 1000), (375, 812))
# The fixture saves a device with these names and observes these website names for it.
DEVICE = "alice"
DEVICE_TITLE = "Test phone"
OBSERVED = ("10000.mt.eu.iot.dreame.tech", "gateway.icloud.com")


@contextmanager
def served_dashboard(extra=None):
    """Run the fixture dashboard with its demo data and observed websites; yield its address."""

    environment = {
        **os.environ,
        "JITTERSIM_BROWSER_TEST_SEED": "1",
        "JITTERSIM_BROWSER_TEST_OBSERVATIONS": "1",
        **(extra or {}),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"],
        stdout=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("The forms dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        process.wait(timeout=10)


def run_journey(title, check):
    """Run check(page, base, width) on its own dashboard at every window width."""

    Path("test-results").mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
        )
        try:
            for width, height in WIDTHS:
                with served_dashboard() as base:
                    # The browser words its own refusals and formats numbers by locale, so pin it.
                    context = browser.new_context(
                        viewport={"width": width, "height": height}, locale="en-US"
                    )
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    check(page, base, width)
                    assert not errors, errors
                    context.close()
            print(f"{title} acceptance passed at desktop and phone widths.")
        finally:
            browser.close()


def post(page, base, path, data):
    response = page.request.post(base + path, headers=HEADERS, data=data)
    assert response.ok, (path, response.status, response.text())
    return response.json()


def get(page, base, path):
    response = page.request.get(base + path, headers=HEADERS)
    assert response.ok, (path, response.status, response.text())
    return response.json()


def clear_toast(page):
    """Dismiss the result toast so that it covers nothing of the form on a small window."""

    page.evaluate("dismissToast()")


def wait_for_token_check(page):
    """Wait for the remembered token check that ends every load of the page."""

    expect(page.locator("#message")).to_contain_text(
        "Token accepted. Diagnostics loaded.", timeout=20000
    )


def unlock(page, base):
    """Open the page, sign in and keep the token so that the page can be reloaded."""

    page.goto(base)
    sidebar_control(page, "#open-admin-credentials").click()
    page.locator("#admin-token").fill(TOKEN)
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    page.locator("#remember-admin-token").check()
    # A reload before the token is stored would forget it.
    expect(page.locator("#remember-admin-token-note")).to_contain_text(
        "Remembered until", timeout=20000
    )
    page.locator("#close-credentials").click()


def reload_signed_in(page):
    page.reload()
    wait_for_token_check(page)


def open_page(page, name):
    page.locator(f'a[data-page-link="{name}"]').first.click()
    panel = page.locator(f"#page-{name}")
    expect(panel).to_be_visible()
    return panel


def open_card(page, title):
    """Open one collapsible card of the Schedules and limits page by its heading."""

    toggle = page.locator("#page-schedules h2 .card-toggle", has_text=title)
    expect(toggle).to_have_count(1)
    if toggle.get_attribute("aria-expanded") == "false":
        toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    return toggle


def no_overflow(page, width):
    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1, (
        "the page scrolls sideways at " + str(width)
    )


def rows_inside(container):
    """Whether every part of every row of a saved list stays inside the row."""

    return container.evaluate(
        """(list) => Array.from(list.children).every((row) => {
            const box = row.getBoundingClientRect();
            return Array.from(row.querySelectorAll('*')).every(
                (child) => child.getBoundingClientRect().right <= box.right + 1);
        })"""
    )


# The profile form asks the Pi to work out the ranges it shows. That is a question with a POST,
# sent on its own after a load, and it saves nothing.
READ_POSTS = ("profiles/ranges",)


def record_writes(page):
    """Collect every request that saves something, so that a refused form can show it sent none."""

    writes = []

    def record(request):
        path = request.url.split("/api/", 1)[-1]
        if request.method != "GET" and "/api/" in request.url and path not in READ_POSTS:
            writes.append(request.method + " " + path)

    page.on("request", record)
    return writes


def fail_posts(page, pattern, message, status=503):
    """Answer every save to the matching path with an error and return the handler to remove."""

    def handler(route):
        if route.request.method == "POST":
            route.fulfill(
                status=status, content_type="application/json", json={"error": message}
            )
        else:
            route.continue_()

    page.route(pattern, handler)
    return handler


def field_error(page, field_id):
    """The inline note that the page puts after a field the browser refused."""

    return page.locator(f"#{field_id}").locator("xpath=..").locator(".field-error")


def discard_all_drafts(page):
    page.get_by_role("button", name="Discard unfinished edits", exact=True).click()
    page.get_by_role("dialog").get_by_role("button", name="Continue", exact=True).click()
    expect(page.locator("#draft-notice")).to_be_hidden()
