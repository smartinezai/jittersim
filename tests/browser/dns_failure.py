"""Verify DNS failure controls through the served dashboard and real API.

The existing fixture uses simulated network commands. This journey proves saved
and applied control values rather than actual DNS packets. The intercepted Apply
failure is simulated and does not test helper failure or packet-rule recovery.
"""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.journeys import unlock

HEADERS = {"Authorization": "Bearer browser-test"}
REQUEST_TIMEOUT = 10000
APPLY_PATH = "/api/access/apply"
SIMULATED_FAILURE = "Simulated access-rule apply failure"


def read(page, base, path):
    response = page.request.get(base + path, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    assert response.ok, (path, response.status, response.text())
    return response.json()


def write(page, base, path, data):
    response = page.request.post(
        base + path, headers=HEADERS, data=data, timeout=REQUEST_TIMEOUT
    )
    assert response.ok, (path, response.status, response.text())
    return response.json()


def open_controls(page, *, reload=False):
    if reload:
        page.reload()
        expect(page.locator("#private-dashboard")).to_be_visible()
        expect(page.locator("#message")).to_contain_text("Token accepted. Diagnostics loaded.")
    page.get_by_role("link", name="Access and rules", exact=True).click()
    expect(page.locator("#policy-form")).to_be_visible()


def save_controls(page, percent, *, enabled=True):
    page.locator("#access-enabled").set_checked(enabled)
    page.locator("#dns-failure-percent").fill(str(percent))
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/api/policies"),
        timeout=REQUEST_TIMEOUT,
    ) as pending:
        page.locator("#save-policies").click()
    response = pending.value
    assert response.ok, (response.status, response.text())
    assert response.json()["options"]["dns_failure_percent"] == percent
    assert response.json()["options"]["enabled"] is enabled
    expect(page.locator("#draft-notice")).to_be_hidden()


def apply_controls(page, percent, *, enabled=True, expected_status=200):
    button = page.locator("#apply-access")
    expect(button).to_be_enabled()
    with page.expect_response(
        lambda response: response.request.method == "POST" and response.url.endswith(APPLY_PATH),
        timeout=REQUEST_TIMEOUT,
    ) as pending:
        button.click()
        if enabled and percent == 100:
            dialog = page.get_by_role("dialog")
            expect(dialog).to_contain_text("100% DNS failure chance")
            dialog.get_by_role("button", name="Continue", exact=True).click()
    response = pending.value
    assert response.status == expected_status, (response.status, response.text())
    expect(button).to_be_enabled()


def assert_applied(page, base, percent, *, enabled=True):
    state = read(page, base, "/api/access")
    assert state["applied"] is True, state
    assert state["saved_policy"]["options"]["dns_failure_percent"] == percent
    assert state["policy"]["options"]["dns_failure_percent"] == percent
    assert state["enabled"] is enabled, state
    assert state["policy"]["options"]["enabled"] is enabled
    status = page.locator("#dns-failure-status")
    if enabled:
        expect(status).to_have_text(f"Applied DNS failure chance: {percent}%.")
        expect(page.locator("#access-summary")).to_contain_text("Saved access rules are active")
    else:
        expect(status).to_contain_text(f"Saved DNS failure chance: {percent}%.")
        expect(status).to_contain_text("DNS failures are off")
        expect(page.locator("#access-summary")).to_have_text("Household access rules are disabled.")


def check_invalid_percentages(page, base):
    previous = read(page, base, "/api/policies")
    for invalid in (-1, 101, 37.5, True, "37", None):
        response = page.request.post(
            base + "/api/policies",
            headers=HEADERS,
            data={**previous, "options": {**previous["options"], "dns_failure_percent": invalid}},
            timeout=REQUEST_TIMEOUT,
        )
        assert response.status == 400, (invalid, response.status, response.text())
        assert response.json().get("error"), invalid
        assert read(page, base, "/api/policies") == previous, invalid
        assert_applied(page, base, 100)


def restore_settings(page, base, original):
    current = read(page, base, "/api/policies")
    restored = write(page, base, "/api/policies", {**original, "version": current["version"]})
    write(page, base, APPLY_PATH, {"version": restored["version"]})
    actual = read(page, base, "/api/policies")
    assert {key: value for key, value in actual.items() if key != "version"} == {
        key: value for key, value in original.items() if key != "version"
    }


def check_width(browser, base, width, height):
    context = browser.new_context(viewport={"width": width, "height": height})
    context.set_default_timeout(REQUEST_TIMEOUT)
    context.set_default_navigation_timeout(15000)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    original = None
    try:
        page.goto(base)
        unlock(page)
        original = read(page, base, "/api/policies")
        open_controls(page)
        for percent in (0, 37, 100):
            before = read(page, base, "/api/access")
            save_controls(page, percent)
            saved = read(page, base, "/api/access")
            assert saved["saved_policy"]["options"]["dns_failure_percent"] == percent
            assert saved.get("policy") == before.get("policy")
            open_controls(page, reload=True)
            expect(page.locator("#dns-failure-percent")).to_have_value(str(percent))
            expect(page.locator("#access-enabled")).to_be_checked()
            apply_controls(page, percent)
            assert_applied(page, base, percent)
            open_controls(page, reload=True)
            assert_applied(page, base, percent)

        check_invalid_percentages(page, base)
        save_controls(page, 100, enabled=False)
        apply_controls(page, 100, enabled=False)
        assert_applied(page, base, 100, enabled=False)
        open_controls(page, reload=True)
        expect(page.locator("#access-enabled")).not_to_be_checked()
        assert_applied(page, base, 100, enabled=False)

        save_controls(page, 37)
        before_failure = read(page, base, "/api/access")
        intercepted = []

        def fail_apply(route):
            intercepted.append(route.request.post_data_json)
            route.fulfill(status=503, json={"error": SIMULATED_FAILURE})

        # Only the browser response fails. The actual helper receives no failed Apply.
        page.route("**" + APPLY_PATH, fail_apply)
        try:
            apply_controls(page, 37, expected_status=503)
            expect(page.locator("#message")).to_contain_text(SIMULATED_FAILURE)
            after_failure = read(page, base, "/api/access")
            assert after_failure["policy"] == before_failure["policy"]
            assert after_failure["saved_policy"]["options"]["dns_failure_percent"] == 37
            assert after_failure["applied"] is False
            assert len(intercepted) == 1, intercepted
        finally:
            page.unroute("**" + APPLY_PATH, fail_apply)
        apply_controls(page, 37)
        assert_applied(page, base, 37)

        # Resetting experiment profiles preserves independently applied access rules.
        write(page, base, "/api/reset", {})
        open_controls(page, reload=True)
        assert_applied(page, base, 37)
        assert not errors, errors
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
        page.locator(".access-options").screenshot(
            path=f"test-results/dns-failure-recovered-{width}.png", animations="disabled"
        )
    finally:
        primary_error = sys.exc_info()[0] is not None
        try:
            if original is not None:
                restore_settings(page, base, original)
        except Exception:
            if not primary_error:
                raise
            print(
                "Could not restore disposable fixture settings after the failure.", file=sys.stderr
            )
        finally:
            context.close()


def run():
    Path("test-results").mkdir(exist_ok=True)
    expect.set_options(timeout=REQUEST_TIMEOUT)
    fixture = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True,
        env={
            **os.environ, "JITTERSIM_BROWSER_TEST_WIFI_PEER": "0",
            "JITTERSIM_BROWSER_TEST_SEED": "0",
        },
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(fixture.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("DNS failure dashboard fixture did not start within 15 seconds")
            line = fixture.stdout.readline().strip()
            if not line.isdigit():
                raise RuntimeError("DNS failure dashboard fixture did not report a port")
            base = f"http://127.0.0.1:{int(line)}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE"), timeout=15000
            )
            try:
                for width, height in ((1440, 1000), (390, 844)):
                    check_width(browser, base, width, height)
            finally:
                browser.close()
        print(
            "DNS failure controls passed at desktop and phone widths: 0%, 37%, 100%, "
            "reload, invalid values, disabled rules and simulated Apply failure recovery. "
            "The network executor was simulated."
        )
    finally:
        fixture.terminate()
        try:
            fixture.wait(timeout=5)
        except subprocess.TimeoutExpired:
            fixture.kill()
            fixture.wait(timeout=5)
        if fixture.stdout is not None:
            fixture.stdout.close()


if __name__ == "__main__":
    run()
