"""A fake-clock fence must observe both request lifecycle completion events."""
from types import SimpleNamespace

import pytest

from tests import recurring_get_drain as scan


@pytest.mark.parametrize("terminal", ["requestfinished", "requestfailed"])
def test_fence_settles_from_either_registered_lifecycle_callback(monkeypatch, terminal):
    request = SimpleNamespace(url="http://fixture/api/observations")
    pending = {"request": request}
    now = [0.0]
    monkeypatch.setattr(scan.time, "monotonic", lambda: now[0])

    class Page:
        def wait_for_event(self, name, **kwargs):
            raise AssertionError("must not depend on only one future event")

        def wait_for_timeout(self, milliseconds):
            now[0] += milliseconds / 1000
            # Model the same discard registered for both finish/failure.
            if now[0] >= 1.1:
                assert terminal in ("requestfinished", "requestfailed")
                pending.clear()

    scan.drain_gets(Page(), pending.values())
    assert not pending and 1 < now[0] < 15


def test_fence_keeps_finite_bound_and_reports_pending_url(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(scan.time, "monotonic", lambda: now[0])
    pending = [SimpleNamespace(url="http://fixture/api/health")]

    class Page:
        def wait_for_timeout(self, milliseconds):
            now[0] += milliseconds / 1000

    with pytest.raises(AssertionError, match="api/health"):
        scan.drain_gets(Page(), pending)
    assert 15 <= now[0] < 15.02
