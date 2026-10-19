"""route_support against a real browser: removing a route while its handler works.

Playwright continues the request of a handler that is removed while it runs when that handler is
the last route of the page, and the handler's own fulfill then fails with "Route is already
handled". keep_interception_on is the fix (tests/browser/route_support.py explains both effects
of removing the last route, and the other one cannot be shown without luck). Without the helper
the same steps end in that error at a later call of Playwright. The server answers /slow after
half a second, so the handler is certainly at work when the route is removed.
The tests need Playwright and Chromium and skip where they are missing, as the hosted unit jobs
have neither.
"""

import http.server
import os
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.browser.route_support import keep_interception_on  # noqa: E402


class Slow(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/slow"):
            time.sleep(0.5)
        body = b"<p>real</p>" if self.path == "/" else b"real"
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if self.path == "/" else "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def make_page():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"))
        except sync_api.Error as error:
            server.shutdown()
            pytest.skip("Chromium is not installed: " + str(error)[:80])

        def make(kept_on):
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
            if kept_on:
                keep_interception_on(page)
            return page

        yield make
        browser.close()
    server.shutdown()


def remove_a_route_while_its_handler_works(page):
    working = []

    def hide_the_answer(route):
        working.append(1)
        response = route.fetch()
        assert response.text() == "real"
        route.fulfill(response=response, body="hidden")

    page.route("**/slow", hide_the_answer)
    page.evaluate("() => { window.answer = fetch('/slow').then((reply) => reply.text()); }")
    for _ in range(100):
        if working:
            break
        page.wait_for_timeout(20)
    assert working, "the handler never started"
    page.unroute("**/slow", hide_the_answer)
    return page.evaluate("() => window.answer")


def test_a_handler_at_work_still_answers_when_its_route_is_removed(make_page):
    page = make_page(kept_on=True)
    assert remove_a_route_while_its_handler_works(page) == "hidden"
    # The route is gone: the next read is the real one.
    assert page.evaluate("() => fetch('/slow').then((reply) => reply.text())") == "real"
