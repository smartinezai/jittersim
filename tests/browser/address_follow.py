"""A saved phone that comes back with a new private address is suggested, moved and undone."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.confirm_dialogs import answer_confirm, cancel_with_escape

QUESTION = "This looks like Dad's phone (new private address 02:00:00:00:00:01). Move its settings?"


def sign_in(page, port):
    page.goto(f"http://127.0.0.1:{port}")
    expect(page.locator("#address-follow-list")).to_contain_text("Check the admin token")
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill("browser-test")
    page.locator("#check-admin-token").click()
    page.locator("#close-credentials").click()


def run():
    environment = {**os.environ, "JITTERSIM_BROWSER_TEST_ADDRESS_FOLLOW": "1"}
    process = subprocess.Popen([sys.executable, "-m", "tests.browser.fixture"],
                               stdout=subprocess.PIPE, text=True, env=environment)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Address follow fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"))
            try:
                context = browser.new_context(viewport={"width": 1920, "height": 1000})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                sign_in(page, port)

                card = page.locator("#address-follow-card")
                row = card.locator(".address-follow-row")
                expect(row).to_have_count(1)
                expect(row).to_contain_text(QUESTION)
                expect(row).to_contain_text('It sends the same device name "dads-iphone".')
                expect(row).to_contain_text("Yes moves its profile to the new address.")
                expect(page.locator("#address-follow-mode")).to_have_value("ask")
                # The sidebar says so and its link leads to the card.
                link = page.locator("#status-address-follow-link")
                expect(link).to_have_text("1 device has a new address. Review it")
                page.screenshot(path="test-results/address-follow-1920.png")

                # No sends the dismissal and says nothing moved.
                page.route("**/api/address-follow/dismiss", lambda route: route.fulfill(
                    status=200, content_type="application/json", body='{"saved": true}'))
                row.get_by_role("button", name="No, not now for Dad's phone").click()
                expect(page.locator("#action-announcer")).to_have_text(
                    "Hidden for a day. Nothing was moved.")
                page.unroute("**/api/address-follow/dismiss")

                # Yes asks first. Escape changes nothing and gives focus back.
                yes = row.get_by_role("button", name="Yes, move the settings of Dad's phone")
                yes.click()
                cancel_with_escape(page, yes, verb="Move settings", title="Move Dad's phone?",
                                   text="Only say yes if this is really that device.")
                expect(row).to_have_count(1)
                yes.click()
                answer_confirm(page, "Move settings", title="Move Dad's phone?")
                expect(page.locator("#action-announcer")).to_contain_text(
                    "Moved. Dad's phone uses its new address now.")
                expect(card.locator(".address-follow-row")).to_have_count(0)
                expect(page.locator("#address-follow-list")).to_contain_text(
                    "No saved device has come back with a new address.")
                expect(page.locator("#status-address-follow")).to_be_hidden()

                history = page.locator("#address-follow-history")
                page.locator("#address-follow-history-box summary").click()
                expect(history).to_contain_text(
                    "Dad's phone: aa:bb:cc:00:00:01 to 02:00:00:00:00:01 · profile · After Yes")
                undo = history.get_by_role("button", name="Undo the move of Dad's phone")
                undo.click()
                answer_confirm(page, "Undo move", title="Undo this move?",
                               text="Its settings go back to aa:bb:cc:00:00:01")
                expect(page.locator("#action-announcer")).to_have_text(
                    "Move undone. Dad's phone uses its old address again.")
                expect(history).to_contain_text("Undone")
                # After an Undo the same pair is not suggested again.
                expect(card.locator(".address-follow-row")).to_have_count(0)

                # Automatic asks once before it is turned on.
                page.locator("#address-follow-mode").select_option("automatic")
                save = page.locator("#address-follow-save")
                save.click()
                answer_confirm(page, "Turn on", title="Turn on automatic moves?",
                               text="Protection and dashboard access never move automatically.")
                expect(page.locator("#action-announcer")).to_have_text(
                    "Automatic moves are on for strong evidence.")
                assert not errors, errors
                context.close()

                # A phone sees the same card without sideways scrolling.
                context = browser.new_context(viewport={"width": 390, "height": 844})
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                sign_in(page, port)
                expect(page.locator("#address-follow-mode")).to_have_value("automatic")
                page.locator("#address-follow-history-box summary").click()
                expect(page.locator("#address-follow-history")).to_contain_text("Undone")
                page.locator("#address-follow-card").scroll_into_view_if_needed()
                page.screenshot(path="test-results/address-follow-390.png")
                assert page.evaluate("document.documentElement.scrollWidth") <= 391
                assert not errors, errors
                context.close()
                print("Address follow browser acceptance passed.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
