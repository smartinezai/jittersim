"""Two ways Playwright loses a request when a journey takes a route off a busy page.

Both were found by looking at the failures of `saved_profiles` and `limits_quotas` and at what
reached the dashboard server. Neither is a fault of the dashboard.

1. Turning request interception off loses requests. Playwright asks Chromium to stop pausing
   requests when the last route of a page is removed. A request that starts in the moment between
   that message and Chromium acting on it is paused and then never continued, so it neither
   reaches the server nor fails. In `saved_profiles` the save that a journey had just held started
   a burst of reads while the last route was removed, and the page waited 15 seconds for them.
   `keep_interception_on` registers one route that is never removed, so interception stays on for
   the whole life of the page and there is nothing to turn off.

2. Removing a handler cancels the request it is still working on. Playwright continues a request
   whose handler is taken away while it runs, and the handler's own `route.fulfill` then fails with
   "Route is already handled!". A handler that fetches the real answer first, as `hide_observed`
   in `limits_quotas` does, is still running when a poll lands near `page.unroute`.
   `watched` counts the running calls of a handler and `release_route` waits for them before it
   removes the route. A call that still loses the race ends quietly: the request was continued
   as it was, which is what the removal asked for.
"""

from playwright.sync_api import Error as PlaywrightError

# Nothing ever asks for this address. The route exists only to keep interception on.
KEEP_INTERCEPTION_ON = "**/__keep-request-interception-on__"
# About ten seconds: a handler that has not finished by then is stuck, and removing it is right.
RELEASE_POLLS = 200
RELEASE_POLL_MS = 50


def keep_interception_on(page):
    """Register a route that stays for the life of the page. Call it right after new_page()."""

    page.route(KEEP_INTERCEPTION_ON, lambda route: route.abort())


def watched(handler):
    """Wrap a route handler so that release_route can see whether a call is still running."""

    def wrapper(route):
        wrapper.running += 1
        try:
            handler(route)
        except PlaywrightError as error:
            # The route was removed while this call worked, so it was continued already.
            if "Route is already handled" not in str(error):
                raise
        finally:
            wrapper.running -= 1

    wrapper.running = 0
    return wrapper


def release_route(page, pattern, handler):
    """Remove a route after the calls of its handler that are running have finished."""

    for _ in range(RELEASE_POLLS):
        if not getattr(handler, "running", 0):
            break
        page.wait_for_timeout(RELEASE_POLL_MS)
    page.unroute(pattern, handler)
