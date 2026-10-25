"""Check real dashboard scan POSTs with synthetic replies and a browser fake clock."""

from playwright.sync_api import expect


SCAN = "/api/observations/scan"


def check_recurring_scan(browser, base, width, evidence, authenticate):
    context = browser.new_context(viewport={"width": width, "height": 900})
    try:
        page = context.new_page()
        page.set_default_timeout(15000)
        page.clock.install()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        open_gets = set()
        page.on("request", lambda request: open_gets.add(request)
                if request.method == "GET" and "/api/" in request.url else None)
        page.on("requestfinished", lambda request: open_gets.discard(request))
        page.on("requestfailed", lambda request: open_gets.discard(request))
        state = {"posts": 0, "hold": True}
        pending = []
        capture = {"captured_observations": 1, "observations": [
            {"ipv4_address": "192.168.50.124", "domain": "synthetic-capture.example"}
        ]}

        def scan(route):
            assert route.request.method == "POST"
            assert route.request.post_data_json == {}
            assert route.request.headers.get("authorization") == "Bearer browser-test"
            state["posts"] += 1
            page.evaluate("n => window.scanPostsForTest = n", state["posts"])
            if state["hold"]:
                pending.append(route)
                page.evaluate("window.scanRouteHeldForTest = true")
            else:
                route.fulfill(json=capture)

        def advance(seconds):
            # Let ordinary status GETs finish between fake seconds so their request
            # deadlines do not become an unrelated fast-forward failure.
            for _ in range(seconds):
                page.clock.run_for(1000)
                page.wait_for_timeout(5)
                for _ in range(100):
                    if not open_gets:
                        break
                    page.wait_for_timeout(10)
                assert not open_gets, [request.url for request in open_gets]

        page.route("**" + SCAN, scan)
        authenticate(page, base)
        page.wait_for_function("() => !dashboardRefresh && !dashboardPoll")
        page.clock.pause_at(page.evaluate("Date.now()/1000") + 1)
        if page.locator("#toast-dismiss").is_visible():
            expect(page.locator("#toast-message")).to_have_text("Token accepted. Diagnostics loaded.")
            page.locator("#toast-dismiss").click()
        button = page.locator("#scan-observations")
        status = page.locator("#observation-capture-status")
        expect(button).to_have_text("Start scanning")
        expect(button).to_have_attribute("aria-pressed", "false")
        advance(31)
        assert state["posts"] == 0  # Saved-results GET polling is not a capture.

        button.click()
        page.wait_for_function("() => window.scanRouteHeldForTest === true")
        expect(button).to_have_text("Stop scanning")
        expect(button).to_be_enabled()
        expect(button).to_have_attribute("aria-pressed", "true")
        advance(1)
        assert state["posts"] == 1
        button.click()
        expect(button).to_have_text("Start scanning")
        expect(button).to_be_disabled()
        expect(status).to_contain_text("already running may finish")
        for route in pending:
            route.fulfill(json=capture)
        pending.clear()
        page.wait_for_function("() => !observationRequestInFlight")
        expect(button).to_be_enabled()
        expect(status).not_to_contain_text("Capture complete")
        assert state["posts"] == 1

        state["hold"] = False
        button.click()
        expect(status).to_contain_text("Next capture in 30 seconds")
        page.wait_for_function("() => !observationRequestInFlight")
        assert state["posts"] == 2
        advance(29)
        assert state["posts"] == 2
        advance(2)
        page.wait_for_function("() => window.scanPostsForTest === 3")
        page.wait_for_function("() => !observationRequestInFlight")
        expect(status).to_contain_text("Next capture in 30 seconds")
        assert button.bounding_box()["height"] >= (44 if width < 768 else 32)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator(".observations-card").screenshot(
            path=f"test-results/recurring-scan-{width}.png"
        )

        page.locator('a[data-page-link="profiles"]').click()
        advance(61)
        assert state["posts"] == 3
        page.locator('a[data-page-link="traffic"]').click()
        expect(button).to_have_text("Start scanning")
        button.click()
        expect(status).to_contain_text("Next capture in 30 seconds")
        page.wait_for_function("() => !observationRequestInFlight")
        assert state["posts"] == 4

        # Exercise the real visibility listener with a synthetic hidden-document
        # event; this is not a physical browser-background/phone lifecycle claim.
        page.evaluate("""() => {
          Object.defineProperty(document,'hidden',{configurable:true,value:true});
          document.dispatchEvent(new Event('visibilitychange'));
        }""")
        expect(button).to_have_text("Start scanning")
        page.evaluate("""() => {
          Object.defineProperty(document,'hidden',{configurable:true,value:false});
          document.dispatchEvent(new Event('visibilitychange'));
        }""")
        advance(61)
        assert state["posts"] == 4

        state["hold"] = True
        page.evaluate("window.scanRouteHeldForTest = false")
        button.click()
        page.wait_for_function("() => window.scanRouteHeldForTest === true")
        assert state["posts"] == 5
        page.evaluate("() => lockDashboardAccess('Locked for recurring-capture fixture')")
        for route in pending:
            route.fulfill(json=capture)
        pending.clear()
        page.wait_for_function("() => !observationRequestInFlight")
        expect(status).not_to_contain_text("Capture complete")
        assert page.evaluate("latestObservationScan === null && observationScanning === false")
        state["hold"] = False
        page.evaluate("() => checkAdminToken()")
        expect(page.locator("#private-dashboard")).to_be_visible()
        expect(button).to_have_text("Start scanning")
        expect(button).to_be_enabled()
        advance(61)
        assert state["posts"] == 5  # Checking the same token never auto-starts capture.
        assert not errors, errors
        evidence.append(
            f"recurring capture {width}px: default OFF despite saved GETs, actual authenticated "
            "POST/body counts, immediate Start, completion+30s cadence, non-overlap, usable Stop "
            "during a delayed reply, no late completion, navigation/simulated visibility stop, "
            "same-token lock+recheck remains OFF, touch size and no horizontal overflow"
        )
    finally:
        context.close()
