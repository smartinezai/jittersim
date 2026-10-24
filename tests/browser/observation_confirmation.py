"""Exercise observed-site confirmation with two real dashboard tabs."""

import argparse
import copy
import json
import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.phone import close_controls, sidebar_control

ADD_PATH = "/api/observations/add-to-profile"
HEADERS = {"Authorization": "Bearer browser-test"}
SINGLE_NAME = "config.extension.grammarly.com"
GROUP_NAMES = (
    "tracking.immobilienscout24.de",
    "api.header.immobilienscout24.de",
    "content.header.immobilienscout24.de",
    "osa.s24-media.immobilienscout24.de",
    "sso.immobilienscout24.de",
    "tenant-network.immobilienscout24.de",
    "web.chatbot.immobilienscout24.de",
)


def authenticate(page, base):
    page.goto(base + "/#traffic")
    sidebar_control(page, "#open-admin-credentials").click()
    page.locator("#admin-token").fill("browser-test")
    page.locator("#check-admin-token").click()
    expect(page.locator("#token-check-status")).to_contain_text(
        "Token accepted. Diagnostics loaded.", timeout=20000
    )
    page.locator("#close-credentials").click()
    close_controls(page)
    toggle = page.get_by_role("button", name="Traffic discovery", exact=True)
    if toggle.get_attribute("aria-expanded") == "false":
        toggle.click()
    expect(page.locator("#observations .observation-device").first).to_be_visible()


def save_from_other_tab(page, profile):
    result = page.evaluate(
        """async profile => {
          const response = await fetch('/api/profiles', {
            method: 'POST',
            headers: {'Authorization': 'Bearer browser-test', 'Content-Type': 'application/json'},
            body: JSON.stringify(profile)
          });
          return {status: response.status, body: await response.json()};
        }""",
        profile,
    )
    assert result["status"] == 200, result


def saved_profile(page, base, profile_id):
    response = page.request.get(base + "/api/profiles", headers=HEADERS)
    assert response.ok, response.text()
    return next(profile for profile in response.json()["profiles"] if profile["id"] == profile_id)


def is_add_response(response):
    return response.request.method == "POST" and response.url.endswith(ADD_PATH)


def action_button(page, kind, profile_id):
    page.wait_for_function("() => !dashboardRefresh")
    card = page.locator(".observation-device").filter(has_text="192.168.50.124").first
    card.get_by_label("Profile for discovered domains", exact=True).select_option(profile_id)
    if kind == "single":
        return card.get_by_role("button", name="Add to profile " + SINGLE_NAME, exact=True)
    group = card.locator(".observation-group").filter(has_text="ImmoScout24")
    return group.locator(".observation-group-profile-button")


def assert_no_replay(page, posts, expected):
    page.wait_for_timeout(250)
    assert len(posts) == expected, posts


def assert_saved_add(page, base, before, names):
    after = saved_profile(page, base, before["id"])
    expected = dict(
        before,
        website_mode="allow_only",
        website_domains=list(dict.fromkeys(before["website_domains"] + list(names))),
    )
    assert after == expected, (after, expected)


def confirm_dialog(page, accepted):
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Every other site will stop working")
    dialog.get_by_role("button", name="Continue" if accepted else "Cancel", exact=True).click()
    expect(dialog).to_have_count(0)


def check_device_collapse(browser, base, width, evidence):
    context = browser.new_context(viewport={"width": width, "height": 900})
    try:
        page = context.new_page()
        authenticate(page, base)
        page.wait_for_function("() => !dashboardRefresh")
        cards = page.locator("#observations .observation-device")
        card = cards.filter(has_text="192.168.50.124").first
        other = cards.filter(has_text="192.168.50.184").first
        toggle = card.locator(".observation-device-toggle")
        body = card.locator(".observation-device-body")
        posts = []
        page.on("request", lambda request: posts.append(request.url)
                if request.method == "POST" else None)
        expect(toggle).to_have_attribute("aria-expanded", "true")
        toggle.focus()
        toggle.press("Enter")
        expect(body).to_be_hidden()
        expect(toggle).to_be_focused()
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(other.locator(".observation-device-body")).to_be_visible()
        assert toggle.get_attribute("aria-controls") == body.get_attribute("id")
        page.evaluate("() => loadObservations()")
        expect(body).to_be_hidden()
        expect(toggle).to_be_focused()
        box = toggle.bounding_box()
        assert box and box["width"] >= 44 and box["height"] >= 44, box
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        page.screenshot(path=f"test-results/discovery-devices-collapsed-{width}.png")
        toggle.press("Space")
        expect(body).to_be_visible()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        page.screenshot(path=f"test-results/discovery-devices-expanded-{width}.png")
        if width == 1920:
            page.evaluate("""async () => {
                const data = await request('/api/observations', 'GET');
                data.devices.forEach(device => {
                    device.display_name = 'Living room streaming device belonging to the household';
                });
                renderObservations(data);
            }""")
            for narrow in (820, 320):
                page.set_viewport_size({"width": narrow, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), narrow
                target = toggle.bounding_box()
                assert target and target["width"] >= 44 and target["height"] >= 44, target
                card.screenshot(path=f"test-results/discovery-devices-long-name-{narrow}.png")
        assert posts == [], posts
        evidence.append(f"device groups at {width}px: keyboard collapse, focus and state after refresh, "
                        "independent devices, 44px target, no overflow or writes")
    finally:
        context.close()


def check_membership_labels(browser, base, width, evidence):
    context = browser.new_context(viewport={"width": width, "height": 900})
    try:
        page = context.new_page()
        authenticate(page, base)
        page.wait_for_function("() => !dashboardRefresh")
        def membership_response(route):
            response = route.fetch()
            data = response.json()
            for device in data["devices"]:
                for name in device["domains"]:
                    name["globally_blocked"] = any(part in name["domain"] for part in (
                        "netflix.com", "nflxvideo.net", "whatsapp.net"))
            route.fulfill(response=response, json=data)

        page.route("**/api/observations", membership_response)
        page.evaluate("() => loadObservations()")
        card = page.locator('.observation-device').filter(has_text='192.168.50.124').first
        group = card.locator('.observation-group').filter(has_text='Netflix')
        badge = group.locator('.observation-group-toggle .observation-badge')
        expect(badge).to_have_text('All on global block list')
        expect(group.locator('.observation-group-block-button')).to_have_text('Unblock all')
        expect(group.locator('.observation-group-toggle .observation-domain-count')).to_have_text(
            '43 queries')
        single = card.locator('.observation-domain').filter(has_text='mmx-ds.cdn.whatsapp.net')
        expect(single.locator('.observation-badge')).to_have_text('On global block list')
        expect(single.get_by_role('button', name='Unblock mmx-ds.cdn.whatsapp.net', exact=True)).to_be_visible()
        expect(page.locator('#observation-note')).to_contain_text('not successful website requests')
        expect(page.locator('#observation-note')).to_contain_text('not whether a query or request was blocked')
        for check_width in (width, 320) if width == 390 else (width,):
            page.set_viewport_size({"width": check_width, "height": 900})
            card.screenshot(path=f'test-results/discovery-membership-{check_width}.png')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), check_width
            assert badge.evaluate("""badge => {
                const count = badge.closest('.observation-group-toggle')
                    .querySelector('.observation-domain-count').getBoundingClientRect();
                const box = badge.getBoundingClientRect();
                return box.right <= count.left || box.bottom <= count.top || box.top >= count.bottom;
            }"""), f'membership badge overlaps query count at {check_width}px'
        evidence.append(f'membership labels at {width}px: exact list membership, unchanged counts and actions')
    finally:
        context.close()


def check_new_scan_names(browser, base, width, evidence):
    context = browser.new_context(viewport={"width": width, "height": 900})
    try:
        page = context.new_page()
        authenticate(page, base)
        page.wait_for_function("() => !dashboardRefresh")
        original = page.request.get(base + "/api/observations", headers=HEADERS).json()
        first = next(d for d in original["devices"] if d["ipv4_address"] == "192.168.50.124")
        old = next(o for o in first["domains"] if o["domain"] == "logs.dradis.netflix.com")
        fresh = dict(old, domain="fresh.netflix.com", query_count=1)
        state = {"phase": 0, "fail": False, "posts": 0}

        def observations(route):
            data = copy.deepcopy(original)
            if state["phase"]:
                for device in data["devices"]:
                    if device["ipv4_address"] == "192.168.50.124":
                        device["domains"].append(fresh)
                    elif device["ipv4_address"] == "192.168.50.184":
                        device["domains"].append(old)
                data["total_domains"] += 2
            route.fulfill(json=data)

        def scan(route):
            state["posts"] += 1
            if state["fail"]:
                state["fail"] = False
                route.fulfill(status=503, json={"error": "scan unavailable"})
                return
            state["phase"] += 1
            route.fulfill(json={"captured_observations": 3, "observations": [
                {"ipv4_address": "192.168.50.124", "domain": old["domain"]},
                {"ipv4_address": "192.168.50.124", "domain": fresh["domain"]},
                {"ipv4_address": "192.168.50.184", "domain": old["domain"]}]})

        page.route("**/api/observations", observations)
        page.route("**/api/observations/scan", scan)
        page.evaluate("() => loadObservations()")
        summary = page.locator("#observation-scan-summary")
        expect(summary).to_contain_text("Saved results refresh automatically")
        page.locator("#scan-observations").click()
        expect(summary).to_contain_text("2 displayed names new to list")
        expect(page.locator("#scan-observations")).to_be_enabled()
        card = page.locator(".observation-device").filter(has_text="192.168.50.124").first
        group = card.locator(".observation-group").filter(has_text="Netflix")
        expect(group.locator(".observation-new-group")).to_have_text("1 new to list")
        expect(group.locator(".observation-new-group")).to_be_visible()
        expect(group.locator(".observation-group-body")).to_be_hidden()
        expect(page.locator(".observation-new-name")).to_have_count(2)
        for _ in range(2):
            page.evaluate("() => pollDashboard()")
        page.evaluate("() => showPage('devices', true)")
        page.evaluate("() => showPage('traffic', true)")
        expect(summary).to_contain_text("2 displayed names new to list")
        expect(page.locator(".observation-new-name")).to_have_count(2)
        assert state["posts"] == 1, state
        page.set_viewport_size({"width": 320 if width == 390 else width, "height": 900})
        card.screenshot(path=f"test-results/discovery-new-names-{width}.png")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        state["fail"] = True
        page.locator("#scan-observations").click()
        expect(page.locator("#message")).to_contain_text("scan unavailable")
        expect(summary).to_contain_text("2 displayed names new to list")
        page.locator("#scan-observations").click()
        expect(summary).to_contain_text("0 displayed names new to list")
        expect(page.locator(".observation-new-name")).to_have_count(0)
        assert state["posts"] == 3, state
        evidence.append(f"new names at {width}px: repeat/device-pair distinction, collapsed groups, "
                        "poll and navigation persistence, failed scan retention, next-scan replacement")
    finally:
        context.close()


def check_actions(browser, base, kind, width, evidence):
    context = browser.new_context(viewport={"width": width, "height": 1000 if width > 768 else 844})
    context.set_default_timeout(15000)
    try:
        editor = context.new_page()
        authenticate(editor, base)
        ids = {scenario: f"obs-{kind}-{width}-{scenario}" for scenario in (
            "stale", "normal", "existing", "failure"
        )}
        for scenario, profile_id in ids.items():
            restricted = scenario in ("stale", "existing")
            save_from_other_tab(editor, {
                "id": profile_id,
                "name": "Observed " + kind + " " + scenario,
                "upload": {"delay_ms": 37},
                "download": {"bandwidth_kbit": 1700},
                "random_seed": 19,
                "website_mode": "allow_only" if restricted else "unrestricted",
                "website_domains": ["keep.example"] if restricted else [],
            })
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        authenticate(page, base)
        posts = []
        page.on("request", lambda request: posts.append(request.post_data_json)
                if request.method == "POST" and request.url.endswith(ADD_PATH) else None)
        names = (SINGLE_NAME,) if kind == "single" else GROUP_NAMES
        prefix = f"{kind} at {width}px: "

        # The first tab retains the old allow list while another tab saves unrestricted.
        before = saved_profile(editor, base, ids["stale"])
        before = dict(before, website_mode="unrestricted", website_domains=[])
        save_from_other_tab(editor, before)
        assert page.evaluate(
            "id => profiles.find(profile => profile.id === id).website_mode", ids["stale"]
        ) == "allow_only"
        button = action_button(page, kind, ids["stale"])
        with page.expect_response(is_add_response) as received:
            button.click()
        response = received.value
        assert response.status == 409, (
            "A stale observed-site action changed restrictions without acknowledgment",
            response.status, response.text(),
        )
        conflict = response.json()
        assert conflict["code"] == "allow_only_confirmation_required", conflict
        assert conflict["profile"] == before, conflict
        assert posts[0]["confirm_allow_only"] is False, posts
        assert saved_profile(editor, base, ids["stale"]) == before
        page.wait_for_function(
            "id => profiles.find(profile => profile.id === id).website_mode === 'unrestricted'",
            arg=ids["stale"],
        )
        expect(page.locator("#message")).to_contain_text(conflict["error"])
        expect(button).to_be_enabled()
        expect(page.get_by_role("dialog")).to_have_count(0)
        assert_no_replay(page, posts, 1)
        evidence.append(
            prefix + "stale conflict preserves settings and updates the page cache without replay"
        )

        button.click()
        expect(page.get_by_role("dialog")).to_be_visible()
        assert len(posts) == 1, posts
        page.get_by_role("dialog").screenshot(
            path=f"test-results/observation-confirmation-{kind}-{width}.png"
        )
        confirm_dialog(page, False)
        expect(button).to_be_enabled()
        assert_no_replay(page, posts, 1)
        assert saved_profile(editor, base, ids["stale"]) == before
        evidence.append(prefix + "the next deliberate click asks and declining sends no request")

        button.click()
        with page.expect_response(is_add_response) as received:
            confirm_dialog(page, True)
        assert received.value.status == 200, received.value.text()
        assert posts[-1]["confirm_allow_only"] is True, posts
        assert_saved_add(editor, base, before, names)
        assert_no_replay(page, posts, 2)
        evidence.append(prefix + "explicit confirmation permits the restrictive transition")

        # A current unrestricted profile asks before its very first mutation.
        posts.clear()
        before = saved_profile(editor, base, ids["normal"])
        button = action_button(page, kind, ids["normal"])
        button.click()
        confirm_dialog(page, False)
        assert_no_replay(page, posts, 0)
        assert saved_profile(editor, base, ids["normal"]) == before
        button.click()
        with page.expect_response(is_add_response) as received:
            confirm_dialog(page, True)
        assert received.value.status == 200, received.value.text()
        assert posts[-1]["confirm_allow_only"] is True, posts
        assert_saved_add(editor, base, before, names)
        assert_no_replay(page, posts, 1)
        evidence.append(
            prefix + "a current unrestricted profile requires confirmation before its first request"
        )

        posts.clear()
        before = saved_profile(editor, base, ids["existing"])
        button = action_button(page, kind, ids["existing"])
        with page.expect_response(is_add_response) as received:
            button.click()
        assert received.value.status == 200, received.value.text()
        assert posts[-1]["confirm_allow_only"] is False, posts
        expect(page.get_by_role("dialog")).to_have_count(0)
        assert_saved_add(editor, base, before, names)
        assert_no_replay(page, posts, 1)
        evidence.append(prefix + "an existing allow list remains usable without another question")

        posts.clear()
        before = saved_profile(editor, base, ids["failure"])
        button = action_button(page, kind, ids["failure"])
        page.route("**" + ADD_PATH, lambda route: route.abort("failed"))
        try:
            button.click()
            confirm_dialog(page, True)
            message = page.locator("#message")
            expect(message).to_contain_text("Lost the connection to the Pi")
            expect(message).to_contain_text("this device rejoins on its own")
            for raw_error in ("Load failed", "Failed to fetch", "NetworkError"):
                assert raw_error not in message.text_content()
            expect(button).to_be_enabled()
            assert_no_replay(page, posts, 1)
            assert saved_profile(editor, base, ids["failure"]) == before
            evidence.append(
                prefix + "transport failure restores the action without retrying its request"
            )
        finally:
            page.unroute("**" + ADD_PATH)
        assert not errors, errors
    finally:
        context.close()


def run(*, cases=("single", "group"), widths=(1920, 390)):
    environment = {**os.environ, "JITTERSIM_BROWSER_TEST_OBSERVATIONS": "1"}
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"],
        stdout=subprocess.PIPE, text=True, env=environment,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Observed-site dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                evidence = []
                for width in widths:
                    check_membership_labels(browser, f"http://127.0.0.1:{port}", width, evidence)
                    check_device_collapse(browser, f"http://127.0.0.1:{port}", width, evidence)
                    check_new_scan_names(browser, f"http://127.0.0.1:{port}", width, evidence)
                for width in widths:
                    for kind in cases:
                        check_actions(browser, f"http://127.0.0.1:{port}", kind, width, evidence)
                print(json.dumps(
                    {"result": "passed", "checks": len(evidence), "evidence": evidence}, indent=2
                ))
            finally:
                browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("single", "group"))
    parser.add_argument("--width", type=int, choices=(1920, 390))
    arguments = parser.parse_args()
    run(cases=(arguments.case,) if arguments.case else ("single", "group"),
        widths=(arguments.width,) if arguments.width else (1920, 390))
