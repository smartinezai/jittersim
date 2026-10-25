"""Exercise mocked resolver aggregates in the real discovery UI, never a live Pi."""

import copy
import time
from pathlib import Path

from playwright.sync_api import expect

from tests.browser.phone import close_controls, sidebar_control


def check_dns_outcomes(browser, base, width, evidence):
    context = browser.new_context(viewport={"width": width, "height": 900})
    try:
        page = context.new_page()
        page.set_default_timeout(15000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        at = int(time.time())
        payload = {
            "available": True, "total_domains": 2,
            "dns_outcome_collection": {
                "status": "partial", "epoch": "synthetic-1", "policy_revision": 7,
                "window_start": at - 60, "window_end": at,
                "lower_bound": True, "coverage_complete": False,
                "collector_available": True, "last_event_at": at - 1,
                "dropped_events": None, "coverage_reason": "fixture_unknown_reason",
            },
            "devices": [{"ipv4_address": "192.168.50.124", "name": "Synthetic device",
                         "domains": [
                             {"domain": "measured.example", "query_count": 19,
                              "globally_blocked": True, "dns_outcomes": {
                                  "blocked_decisions": 50, "answered_noerror": 90,
                                  "other_dns_errors": 887766, "last_observed_at": at - 1,
                              }},
                             {"domain": "historical.example", "query_count": 6,
                              "globally_blocked": False, "dns_outcomes": None},
                         ]}],
        }
        pending = []
        state = {"hold": False}

        def observations(route):
            if state["hold"]:
                pending.append(route)
            else:
                route.fulfill(json=copy.deepcopy(payload))

        page.route("**/api/observations", observations)
        page.goto(base + "/#traffic")
        sidebar_control(page, "#open-admin-credentials").click()
        page.locator("#admin-token").fill("browser-test")
        page.locator("#check-admin-token").click()
        expect(page.locator("#private-dashboard")).to_be_visible()
        page.locator("#close-credentials").click()
        close_controls(page)
        toggle = page.get_by_role("button", name="Traffic discovery", exact=True)
        if toggle.get_attribute("aria-expanded") == "false":
            toggle.click()
        card = page.locator("#observations .observation-device").first
        measured = card.locator('.observation-domain[data-key$="|measured.example"]')
        historical = card.locator('.observation-domain[data-key$="|historical.example"]')
        expect(measured.locator(".observation-dns-outcomes")).to_contain_text(
            "Blocked decisions observed: 50 · DNS answers observed: 90"
        )
        expect(measured.locator(".observation-domain-count")).to_have_text("19 queries")
        expect(measured.locator(".observation-badge")).to_have_text("On global block list")
        expect(historical.locator(".observation-dns-outcomes")).to_contain_text(
            "Unknown/no outcome recorded"
        )
        expect(historical.locator(".observation-badge")).to_be_hidden()
        expect(card).not_to_contain_text("887766")
        summary = page.locator("#observation-scan-summary")
        for text in ("partial coverage", "lower-bound", "Window:", "Policy revision: 7",
                     "Collector available", "Event loss unknown"):
            expect(summary).to_contain_text(text)
        expect(page.locator("#observation-note")).to_contain_text("NOERROR")
        expect(page.locator("#observation-note")).to_contain_text("Encrypted or bypassing DNS")

        device_toggle = card.locator(".observation-device-toggle")
        device_toggle.focus()
        device_toggle.press("Enter")
        expect(device_toggle).to_have_attribute("aria-expanded", "false")
        page.evaluate("() => loadObservations()")
        expect(device_toggle).to_be_focused()
        expect(device_toggle).to_have_attribute("aria-expanded", "false")
        device_toggle.press("Enter")
        expect(device_toggle).to_have_attribute("aria-expanded", "true")
        Path("test-results").mkdir(exist_ok=True)
        page.locator(".observations-card").screenshot(
            path=f"test-results/dns-outcomes-{width}.png"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

        # A new collector epoch replaces, never accumulates or relabels the old counts.
        payload["dns_outcome_collection"].update(epoch="synthetic-2", policy_revision=8,
                                                  dropped_events=0)
        payload["devices"][0]["domains"][0]["dns_outcomes"].update(
            blocked_decisions=2, answered_noerror=0
        )
        page.evaluate("() => loadObservations()")
        expect(measured.locator(".observation-dns-outcomes")).to_contain_text(
            "Blocked decisions observed: 2 · DNS answers observed: 0"
        )
        expect(summary).to_contain_text("Policy revision: 8")
        expect(summary).to_contain_text("Known receiver drops: 0; sender loss unknown")
        expect(measured.locator(".observation-domain-count")).to_have_text("19 queries")

        payload["dns_outcome_collection"].update(status="unavailable", window_start=None,
                                                  window_end=None, collector_available=False)
        page.evaluate("() => loadObservations()")
        expect(summary).to_contain_text("collection telemetry unavailable")
        expect(measured.locator(".observation-dns-outcomes")).to_contain_text("Unknown")
        expect(measured.locator(".observation-dns-outcomes")).not_to_contain_text("observed: 0")

        if width == 375:
            page.set_viewport_size({"width": 320, "height": 900})
            page.evaluate("document.documentElement.style.fontSize='20px'")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator(".observations-card").screenshot(
                path="test-results/dns-outcomes-320-large.png"
            )

        # A same-token access loss clears new private text and suppresses a late reply.
        state["hold"] = True
        with page.expect_request("**/api/observations"):
            page.evaluate("() => { window.outcomeReadForTest = loadObservations(); }")
        assert pending
        page.evaluate("() => lockDashboardAccess('Locked for synthetic outcome fixture')")
        expect(page.locator(".observation-dns-outcomes")).to_have_count(0)
        expect(summary).to_have_text("Saved results refresh automatically.")
        for route in pending:
            route.fulfill(json=payload)
        state["hold"] = False
        page.evaluate("() => window.outcomeReadForTest")
        page.evaluate("() => setDashboardUnlocked(true)")
        expect(page.locator(".observation-dns-outcomes")).to_have_count(0)
        expect(summary).not_to_contain_text("Window:")
        assert not errors, errors
        evidence.append(
            f"resolver outcomes {width}px: independent lower-bound counts, unknown history, "
            "membership/query distinction, window/policy/health, epoch replacement, "
            "keyboard focus/collapse, unavailable telemetry, private clearing and late reply"
        )
    finally:
        context.close()
