"""Run update status recovery through the real dashboard with controlled update replies."""

import collections
import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.page_polling import advance, read_port, start_fixture, watch

TOKEN = "browser-test"


def run():
    fixture = start_fixture()
    try:
        port = read_port(fixture)
        width = int(os.environ.get("JITTERSIM_UPDATE_VIEWPORT", "1280"))
        checks = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"))
            try:
                context = browser.new_context(viewport={"width": width, "height": 900})
                counts = collections.Counter()
                errors = []
                state = {"staged": None, "daily": False, "fail_read": False, "lose_save": False, "operation": None, "lose_retry": False, "other_retry": False}
                held = []

                def update_reply(route):
                    request = route.request
                    path = request.url.split("://", 1)[1].split("/", 1)[1]
                    counts[(request.method, path)] += 1
                    assert request.headers.get("authorization") == "Bearer " + TOKEN
                    if path == "api/updates/check-online":
                        if request.method == "POST":
                            state["staged"] = "1.2.0"
                        elif state["fail_read"]:
                            state["fail_read"] = False
                            route.abort()
                            return
                        data = {"state": "done", "job": "0123456789abcdef", "result": "staged",
                                "checked_at": 100, "token_set": True,
                                "settings": {"daily_check": state["daily"], "install_automatically": False}}
                    elif path == "api/updates/online-settings":
                        state["daily"] = request.post_data_json["daily_check"]
                        if state["lose_save"]:
                            state["lose_save"] = False
                            route.abort()
                            return
                        data = {"token_set": True, "settings": {"daily_check": state["daily"], "install_automatically": False}}
                    elif path == "api/updates/retry":
                        assert request.method == "POST"
                        assert request.post_data_json == {"retry_id": "c" * 64}
                        queued = dict(state["operation"], phase="queued")
                        state["operation"] = dict(state["operation"], phase="complete", installed="2.0.0", retry=None)
                        if state["other_retry"]:
                            state["operation"]["request_id"] = "b" * 32
                        if state["lose_retry"]:
                            state["lose_retry"] = False
                            route.abort()
                            return
                        data = {"status": queued}
                    elif path == "api/update-preferences":
                        data = {"check_staged_updates_on_open": False}
                    else:
                        data = {"installed": "1.0.0", "available": state["staged"], "phase": "ready",
                                "installable": bool(state["staged"]), "signed_by": "fixture-key",
                                "message": "fixture signed release status", "rollback_available": False}
                        if state["operation"]:
                            data.update(state["operation"])
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

                for path in ("updates", "updates/retry", "updates/check-online", "updates/online-settings", "update-preferences"):
                    context.route("**/api/" + path, update_reply)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.clock.install()
                page.goto(f"http://127.0.0.1:{port}")
                page.wait_for_timeout(150)
                assert not counts, dict(counts)
                checks.append("no private update read before sign-in")
                page.locator("#open-admin-credentials").click()
                page.locator("#admin-token").fill(TOKEN)
                page.locator("#remember-admin-token").check()
                page.locator("#check-admin-token").click()
                expect(page.locator("#token-check-status")).to_contain_text("Token accepted", timeout=20000)
                expect(page.locator("#online-daily-check")).to_be_enabled()
                page.locator("#close-credentials").click()
                assert counts[("POST", "api/updates/check-online")] == 0
                checks.append("accepted sign-in reads status without starting GitHub")
                page.locator('[data-page-link="updates"]').first.click()
                expect(page.locator("#update-installed")).to_have_text("1.0.0")
                page.locator("#release-check").click()
                expect(page.locator("#sidebar-update-link")).to_contain_text("1.2.0")
                assert counts[("POST", "api/updates/check-online")] == 1
                checks.append("manual check stages a signed release once")
                watch(page)
                state["staged"] = "1.3.0"
                advance(page, 7)
                print(json.dumps({"poll_diagnostic": page.evaluate("({now:Date.now(),releasePollAt,page:currentPage(),dashboardLoaded,refresh:Boolean(dashboardRefresh),poll:Boolean(dashboardPoll),reads:Array.from(updateReads.keys())})"), "requests":dict((method+' '+path,n) for (method,path),n in counts.items()), "errors":errors}), flush=True)
                expect(page.locator("#sidebar-update-link")).to_contain_text("1.3.0")
                assert counts[("POST", "api/updates/check-online")] == 1
                checks.append("external staging refreshes badge without another check write")
                state["fail_read"] = True
                page.evaluate("loadOnlineStatus()")
                expect(page.locator("#online-check-status")).to_contain_text("unknown")
                expect(page.locator("#online-daily-check")).to_be_disabled()
                page.locator("#release-check").click()
                expect(page.locator("#online-daily-check")).to_be_enabled()
                assert counts[("POST", "api/updates/check-online")] == 2
                checks.append("manual retry rereads unknown status before one explicit check")
                state["lose_save"] = True
                page.locator("#online-daily-check").check()
                expect(page.locator("#online-setting-status")).to_have_text("Current setting on the Pi: on.")
                assert counts[("POST", "api/updates/online-settings")] == 1
                checks.append("lost save reply recovers by reading without replay")
                expect(page.locator("#release-retry")).to_be_hidden()
                checks.append("legacy status offers no retained retry")

                def failed_operation():
                    return {"installed": "1.0.0", "phase": "failed", "request_id": "a" * 32,
                            "request_kind": "install", "message": "Retained failed update",
                            "retry": {"id": "c" * 64, "request_id": "a" * 32, "kind": "install",
                                      "version": "2.0.0", "recovering": False}}

                state["operation"] = failed_operation()
                page.evaluate("loadReleaseStatus({force:true})")
                expect(page.locator("#release-retry")).to_be_enabled()
                page.locator("#release-retry").click()
                page.locator("dialog").get_by_role("button", name="Cancel", exact=True).click()
                expect(page.locator("#release-retry")).to_be_enabled()
                assert counts[("POST", "api/updates/retry")] == 0
                checks.append("cancelled retained retry sends no write")
                page.locator("#release-retry").click()
                state["operation"]["retry"]["id"] = "d" * 64
                page.locator("dialog").get_by_role("button", name="Continue", exact=True).click()
                expect(page.locator("#update-progress")).to_contain_text("changed")
                assert counts[("POST", "api/updates/retry")] == 0
                checks.append("changed retry admission is rejected after confirmation")
                state["operation"] = failed_operation()
                state["lose_retry"] = True
                page.evaluate("loadReleaseStatus({force:true})")
                page.locator("#release-retry").click()
                page.locator("dialog").get_by_role("button", name="Continue", exact=True).click()
                expect(page.locator("#update-progress")).to_have_text("Updated to 2.0.0.")
                assert counts[("POST", "api/updates/retry")] == 1
                checks.append("lost retry reply confirms the same request by reads without replay")
                state["operation"] = failed_operation()
                state["other_retry"] = True
                page.evaluate("loadReleaseStatus({force:true})")
                page.locator("#release-retry").click()
                page.locator("dialog").get_by_role("button", name="Continue", exact=True).click()
                expect(page.locator("#update-progress")).to_contain_text("not confirmed")
                assert counts[("POST", "api/updates/retry")] == 2
                checks.append("same version from another request cannot confirm retry")
                state["operation"] = None
                remembered = context.new_page()
                remembered.on("pageerror", lambda error: errors.append(str(error)))
                remembered.goto(f"http://127.0.0.1:{port}")
                expect(remembered.locator("#private-dashboard")).to_be_visible(timeout=20000)
                expect(remembered.locator("#online-daily-check")).to_be_enabled()
                assert counts[("POST", "api/updates/check-online")] == 2
                checks.append("remembered accepted sign-in rereads private status without a check write")
                remembered.close()
                page.route("**/api/updates/check-online", lambda route: held.append(route))
                page.evaluate("void loadOnlineStatus()")
                page.wait_for_timeout(50)
                assert held
                if width < 700:
                    page.locator("#controls-toggle").click()
                page.locator("#open-admin-credentials").click()
                page.locator("#admin-token").fill("replacement-fixture-token")
                held[0].abort()
                page.wait_for_timeout(100)
                expect(page.locator("#online-check-status")).to_have_text("Sign in to read update status.")
                expect(page.locator("#online-daily-check")).to_be_disabled()
                checks.append("late old status failure cannot repaint or unlock replacement sign-in")
                assert not errors, errors
                target = Path("test-results/update-status-recovery")
                target.mkdir(parents=True, exist_ok=True)
                (target / f"{width}.json").write_text(json.dumps({"width": width, "checks": checks,
                    "requests": {method + " " + path: count for (method, path), count in counts.items()},
                    "page_errors": errors, "mode": "Real dashboard consumers with controlled update replies. No live Pi acceptance."}, indent=2) + "\n")
                print(json.dumps({"width": width, "checks": checks}))
            finally:
                browser.close()
    finally:
        fixture.terminate()
        fixture.wait(timeout=10)


if __name__ == "__main__":
    run()
