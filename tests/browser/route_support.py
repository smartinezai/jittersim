"""Keep request interception on for the whole life of a page that adds and removes routes.

Playwright asks Chromium to stop pausing requests when the last route of a page is removed, and
that goes wrong in two ways. Both showed up as flakes of the browser journeys, and neither is a
fault of the dashboard.

1. A request that starts between the message and Chromium acting on it is paused and never
   continued. It neither reaches the server nor fails. In `saved_profiles` the save that a journey
   had just held started a burst of reads while the last route was removed. The server log
   showed the requests never arrived, and the page waited 15 seconds for them.

2. A handler that is working when the last route goes has its request continued as it was, and
   the handler's own `route.fulfill` then fails with "Route is already handled!". In
   `limits_quotas` the handler of `hide_observed` fetches the real answer first and is still at
   work when a poll lands next to `page.unroute`.

`keep_interception_on` registers one route that is never removed. Interception then stays on, the
last route is never the one that goes, and neither effect can happen. Call it right after
`new_page()` in a journey that removes routes while the page is reading.
"""

# Nothing ever asks for this address. The route exists only to keep interception on.
KEEP_INTERCEPTION_ON = "**/__keep-request-interception-on__"


def keep_interception_on(page):
    """Register a route that stays for the life of the page. Call it right after new_page()."""

    page.route(KEEP_INTERCEPTION_ON, lambda route: route.abort())
