"""Plain test support for bounded browser request lifecycle draining."""

import time


def drain_gets(page, open_gets):
    """Pump both finish and failure callbacks within the existing real-time bound."""
    deadline = time.monotonic() + 15
    while open_gets and time.monotonic() < deadline:
        # A single requestfinished wait can miss a completion or wait forever
        # for a request that instead emitted requestfailed. The registered
        # lifecycle callbacks maintain the authoritative outstanding set.
        page.wait_for_timeout(10)
    assert not open_gets, [request.url for request in open_gets]

