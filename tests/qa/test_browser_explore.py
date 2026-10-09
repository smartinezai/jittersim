"""Open every dashboard page at a desktop and a phone width and look for what is plainly wrong (QA, 2026-10-09).

The explorer (tests/qa/browser_explorer.py) drives Chromium against the browser fixture. This test is
the quick pass: it loads each page and checks layout overflow, controls with no accessible name, focus
that cannot move on, text stuck on Loading and words like undefined or NaN on screen, plus the console
and the network. The slow pass that also presses every control is for people:

    python -m tests.qa.browser_explorer qa-browser-report
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.qa import browser_explorer  # noqa: E402

# Known and harmless: a visually hidden username field that only password managers read.
ALLOWED = (
    ("unlabeled-control", 'name="username"'),
    # The page probes the Pi's .local name for its one-address link. A machine without mDNS cannot resolve it.
    ("request-failed", ".local:"),
)


def unexpected(findings):
    return [
        finding for finding in findings
        if not any(kind == finding["kind"] and text in finding["detail"] for kind, text in ALLOWED)
    ]


def test_every_page_is_clean_at_desktop_and_phone_width(tmp_path):
    try:
        report = browser_explorer.main(str(tmp_path / "report"), click=False)
    except Exception as error:  # no browser installed in this environment
        if "Executable doesn't exist" in str(error) or "playwright install" in str(error):
            pytest.skip("Chromium is not installed for Playwright")
        raise
    problems = unexpected(report["findings"])
    assert not problems, "\n".join(f"{p['where']} {p['kind']}: {p['detail'][:160]}" for p in problems[:15])
    # Both widths reached every page.
    assert set(report["summary"]) == {"1440px", "375px"}
