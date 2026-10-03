"""List saved profiles, edit one without losing a field and apply it to all devices."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.phone import sidebar_control

SLOW = {
    "id": "slow",
    "name": "Slow office",
    "upload": {
        "delay_ms": 50,
        "jitter_ms": 10,
        "loss_percent": 2,
        "bandwidth_kbit": 5000,
        "delay_randomness": {"variation_percent": 20, "enabled_probability": 90},
        "corrupt_percent": 1,
    },
    "download": {"delay_ms": 80, "bandwidth_kbit": 1500, "reorder_percent": 2},
    "random_seed": 99,
    "reroll_interval_seconds": 45,
    "reroll_interval_randomness": {"variation_percent": 7, "enabled_probability": 80},
    "outage_probability": 12,
    "outage_duration_seconds": 33,
    "outage_interval_seconds": 77,
    "outage_duration_randomness": {"variation_percent": 11, "enabled_probability": 60},
    "outage_interval_randomness": {"variation_percent": 21, "enabled_probability": 38},
    "website_mode": "allow_only",
    "website_domains": ["zeta.example.com", "alpha.example.com"],
    "website_randomness": {"variation_percent": 13, "enabled_probability": 40},
}
# A second profile with other hidden values and a name that cannot wrap on its own.
OTHER = {
    "id": "other",
    "name": ("Longname" * 13)[:100],
    "upload": {"delay_ms": 20},
    "download": {"delay_ms": 20},
    "reroll_interval_seconds": 30,
    "reroll_interval_randomness": {"variation_percent": 9, "enabled_probability": 55},
    "outage_probability": 5,
    "outage_duration_seconds": 40,
    "outage_interval_seconds": 60,
    "outage_duration_randomness": {"variation_percent": 31, "enabled_probability": 45},
    "outage_interval_randomness": {"variation_percent": 23, "enabled_probability": 35},
    "website_mode": "allow_only",
    "website_domains": ["zed.example.com", "beta.example.com"],
    "website_randomness": {"variation_percent": 17, "enabled_probability": 25},
}
# The browser cannot hold this seed exactly so Edit must refuse it.
BIG_SEED = {"id": "bigseed", "name": "Big seed", "random_seed": 2**53 + 1}
SEED_MESSAGE = "random seed too large to edit here"
HIDDEN = (
    "reroll_interval_randomness",
    "outage_duration_randomness",
    "outage_interval_randomness",
    "website_randomness",
)
SLOW_SUMMARY = (
    "↑ {delay} ms ±10 · 2% loss · 5 Mbit/s · corrupt 1% · "
    "↓ 80 ms · 1.5 Mbit/s · reorder 2% · outages 12% · allow-only 2 sites"
)


def saved(page, base, headers, profile_id):
    profiles = page.request.get(base + "/api/profiles", headers=headers).json()["profiles"]
    matching = [profile for profile in profiles if profile["id"] == profile_id]
    assert len(matching) == 1, matching
    return matching[0]


def is_profile_save(response):
    return response.request.method == "POST" and response.url.endswith("/api/profiles")


def with_delay(profile, delay):
    return dict(profile, upload=dict(profile["upload"], delay_ms=delay))


def check_device_profile_picker(page, base, headers, width):
    picker = page.locator("#device-profile")
    save = page.locator("#save-device")
    expect(picker).to_be_enabled()
    expect(picker).to_have_value("")
    assert not picker.evaluate("node => node.checkValidity()")
    page.locator("#device-id").fill(f"picker-device-{width}")
    page.locator("#device-name").fill("Picker laptop")
    ip = "192.168.50.230" if width == 1920 else "192.168.50.231"
    page.locator("#device-ip").fill(ip)
    picker.select_option(label="Slow office")
    page.evaluate("loadProfiles()")
    expect(picker).to_have_value("slow")
    expect(page.locator("#device-name")).to_have_value("Picker laptop")

    def empty_profiles(route):
        route.fulfill(status=200, content_type="application/json", json={"profiles": []})

    page.route("**/api/profiles", empty_profiles)
    page.evaluate("loadProfiles().then(() => restoreDrafts())")
    expect(picker).to_have_value("")
    expect(picker).to_be_disabled()
    expect(save).to_be_disabled()
    expect(page.locator("#device-profile-note")).to_contain_text("No saved profiles")
    expect(picker.locator("option")).to_have_count(1)
    page.unroute("**/api/profiles", empty_profiles)

    def failed_profiles(route):
        route.fulfill(status=503, content_type="application/json", json={"error": "Fixture failure"})

    page.route("**/api/profiles", failed_profiles)
    page.evaluate("loadProfiles().catch(() => {})")
    expect(save).to_be_disabled()
    expect(page.locator("#device-profile-refresh")).to_be_visible()
    page.unroute("**/api/profiles", failed_profiles)
    page.locator("#device-profile-refresh").click()
    expect(picker).to_be_enabled()
    expect(picker).to_have_value("")

    # Identical names must still select the intended stored ID.
    copies = [dict(saved(page, base, headers, key), name="Same name") for key in ("slow", "other")]

    def duplicate_names(route):
        route.fulfill(status=200, content_type="application/json", json={"profiles": copies})

    page.route("**/api/profiles", duplicate_names)
    page.evaluate("loadProfiles()")
    picker.select_option(label="Same name (other)")
    expect(picker).to_have_value("other")
    page.unroute("**/api/profiles", duplicate_names)
    page.evaluate("loadProfiles()")
    expect(picker).to_have_value("other")
    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
    page.locator("#device-form").screenshot(path=f"test-results/device-profile-picker-{width}.png")

    def missing_profile(route):
        route.fulfill(status=200, content_type="application/json", json={"profiles": [copies[0]]})

    page.route("**/api/profiles", missing_profile)
    page.evaluate("loadProfiles().then(() => restoreDrafts())")
    expect(picker).to_have_value("")
    expect(page.locator("#device-profile-note")).to_contain_text("no longer available")
    page.unroute("**/api/profiles", missing_profile)
    page.evaluate("loadProfiles()")
    picker.select_option(label="Slow office")
    expect(page.locator("#device-profile-note")).to_be_hidden()
    with page.expect_request(
        lambda sent: sent.method == "POST" and sent.url.endswith("/api/devices")
    ) as submitted:
        save.click()
    assert submitted.value.post_data_json["profile_id"] == "slow"
    expect(save).to_be_enabled()
    devices = page.request.get(base + "/api/devices", headers=headers).json()["devices"]
    device = next(item for item in devices if item["id"] == f"picker-device-{width}")
    assert device["profile_id"] == "slow"


def run():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Saved profiles dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                for width, height in ((1920, 1000), (390, 844)):
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    base = f"http://127.0.0.1:{port}"
                    headers = {"Authorization": "Bearer browser-test"}
                    assert page.request.post(
                        base + "/api/general-profile/stop", headers=headers, data={}
                    ).ok
                    for profile in (SLOW, OTHER, BIG_SEED):
                        assert page.request.post(
                            base + "/api/profiles", headers=headers, data=profile
                        ).ok
                    original = saved(page, base, headers, "slow")
                    original_other = saved(page, base, headers, "other")
                    assert original["outage_duration_randomness"] == {
                        "variation_percent": 11, "enabled_probability": 60,
                    }
                    assert original["outage_interval_randomness"] == {
                        "variation_percent": 21, "enabled_probability": 38,
                    }
                    assert original["website_domains"] == ["zeta.example.com", "alpha.example.com"]
                    for key in HIDDEN:
                        assert original[key] != original_other[key], key
                    assert saved(page, base, headers, "bigseed")["random_seed"] == 2**53 + 1

                    # The dashboard unlocks while the token check is still loading. Hold the
                    # last request the check makes so that window stays open, write a newer
                    # message inside it and prove the finishing check does not replace it.
                    held_settings = []
                    page.route(
                        "**/api/traffic-settings", lambda route: held_settings.append(route)
                    )
                    page.goto(base + "/#profiles")
                    page.locator("#open-admin-credentials").click()
                    page.locator("#admin-token").fill("browser-test")
                    page.locator("#check-admin-token").click()
                    expect(page.locator("#private-dashboard")).to_be_visible()
                    for _ in range(50):
                        if held_settings:
                            break
                        page.wait_for_timeout(100)
                    assert len(held_settings) == 1, "the token check did not reach its last request"
                    expect(page.locator("#token-check-status")).to_have_text(
                        "Checking the token and loading diagnostics..."
                    )
                    page.evaluate("showMessage('Newer message.', 'success')")
                    held_settings[0].continue_()
                    page.unroute("**/api/traffic-settings")
                    # The check writes its status text and its message in one step, so once the
                    # status is final nothing from the check can overwrite a later message.
                    expect(page.locator("#token-check-status")).to_contain_text(
                        "Token accepted. Diagnostics loaded"
                    )
                    expect(page.locator("#message")).to_have_text("Newer message.")
                    page.locator("#close-credentials").click()
                    expect(page.locator("#page-profiles")).to_be_visible()
                    check_device_profile_picker(page, base, headers, width)

                    def row_of(profile_id):
                        return page.locator(f'.saved-profile-row[data-profile-id="{profile_id}"]')

                    row = row_of("slow")
                    other_row = row_of("other")
                    submit = page.locator("#profile-form button[type='submit']")
                    expect(row).to_be_visible()
                    expect(other_row).to_be_visible()
                    expect(row).to_contain_text("Slow office")
                    expect(row.locator(".saved-profile-id")).to_have_text("slow")
                    expect(row.locator(".saved-profile-summary")).to_have_text(
                        SLOW_SUMMARY.format(delay=50)
                    )
                    expect(other_row.locator(".saved-profile-summary")).to_have_text(
                        "↕ 20 ms · outages 5% · allow-only 2 sites"
                    )
                    expect(row.locator(".general-badge")).to_have_count(0)
                    edit = row.get_by_role("button", name="Edit Slow office")
                    apply = row.get_by_role("button", name="Apply to all: Slow office")
                    expect(edit).to_have_text("Edit")
                    expect(apply).to_have_text("Apply to all")
                    expect(apply).to_be_enabled()
                    expect(page.locator("#new-profile")).to_be_hidden()
                    expect(page.locator("#profile-form-title")).to_have_text("Save profile")
                    # A 100 character name without a break must wrap inside its row.
                    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
                    inside = other_row.evaluate(
                        """(node) => {
                            const box = node.getBoundingClientRect();
                            return Array.from(node.querySelectorAll('*')).every(
                                (child) => child.getBoundingClientRect().right <= box.right + 1);
                        }"""
                    )
                    assert inside, "the long name overflows its row"
                    page.evaluate("dismissToast()")
                    page.locator("section[aria-labelledby='saved-profiles-title']").screenshot(
                        path=f"test-results/saved-profiles-list-{width}.png"
                    )

                    # Edit fills every field and locks the id.
                    edit.click()
                    expect(page.locator("#profile-form-title")).to_have_text("Edit profile")
                    expect(page.locator("#profile-id")).to_have_value("slow")
                    expect(page.locator("#profile-id")).not_to_be_editable()
                    expect(page.locator("#profile-name")).to_have_value("Slow office")
                    expect(page.locator("#upload-delay")).to_have_value("50")
                    expect(page.locator("#download-bandwidth")).to_have_value("1500")
                    expect(page.locator("#profile-outage-probability")).to_have_value("12")
                    expect(page.locator("#profile-website-domains")).to_have_value(
                        "zeta.example.com, alpha.example.com"
                    )
                    expect(page.locator("#new-profile")).to_be_visible()
                    expect(page.locator("#profile-form-title")).to_be_in_viewport()
                    page.evaluate("dismissToast()")
                    page.locator("#profile-form").screenshot(
                        path=f"test-results/saved-profiles-edit-{width}.png"
                    )
                    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1

                    # Saving an edit keeps every field the form cannot show.
                    page.locator("#upload-delay").fill("75")
                    submit.click()
                    expect(page.locator("#message")).to_contain_text("Profile saved.")
                    expect(row.locator(".saved-profile-summary")).to_have_text(
                        SLOW_SUMMARY.format(delay=75)
                    )
                    after = saved(page, base, headers, "slow")
                    for key in HIDDEN:
                        assert after[key] == original[key], key
                    assert after == with_delay(original, 75), (after, original)
                    expect(page.locator("#profile-form-title")).to_have_text("Edit profile")
                    expect(page.locator("#profile-id")).not_to_be_editable()

                    page.locator("#new-profile").click()
                    expect(page.locator("#profile-id")).to_be_editable()
                    expect(page.locator("#profile-id")).to_have_value("")
                    expect(page.locator("#profile-name")).to_have_value("")
                    expect(page.locator("#upload-delay")).to_have_value("0")
                    expect(page.locator("#profile-outage-probability")).to_have_value("0")
                    expect(page.locator("#profile-website-domains")).to_have_value("")
                    expect(page.locator("#profile-form-title")).to_have_text("Save profile")
                    expect(page.locator("#new-profile")).to_be_hidden()

                    # A seed above 2^53 - 1 cannot be edited in the browser.
                    row_of("bigseed").locator('button[data-action="edit"]').click()
                    expect(page.locator("#message")).to_contain_text(SEED_MESSAGE)
                    expect(page.locator("#profile-id")).to_have_value("")
                    expect(page.locator("#profile-form-title")).to_have_text("Save profile")
                    assert saved(page, base, headers, "bigseed")["random_seed"] == 2**53 + 1

                    # Editing another profile while a save is in flight must not mix their fields.
                    edit.click()
                    expect(page.locator("#profile-id")).to_have_value("slow")
                    page.locator("#upload-delay").fill("76")
                    held = []
                    page.route(
                        "**/api/profiles",
                        lambda route: held.append(route)
                        if route.request.method == "POST"
                        else route.continue_(),
                    )
                    submit.click()
                    for _ in range(50):
                        if held:
                            break
                        page.wait_for_timeout(100)
                    assert len(held) == 1, "the save was not held"
                    other_row.locator('button[data-action="edit"]').click()
                    # The edited fields are still a draft until the save answers, so the
                    # draft guard asks before the form shows another profile.
                    page.get_by_role("dialog").get_by_role("button", name="Continue", exact=True).click()
                    expect(page.locator("#profile-id")).to_have_value("other")
                    with page.expect_response(is_profile_save):
                        held[0].continue_()
                    expect(submit).to_be_enabled()
                    expect(page.locator("#profile-id")).to_have_value("other")
                    expect(page.locator("#profile-form-title")).to_have_text("Edit profile")
                    page.unroute("**/api/profiles")
                    page.locator("#upload-delay").fill("66")
                    with page.expect_response(is_profile_save):
                        submit.click()
                    expect(submit).to_be_enabled()
                    assert saved(page, base, headers, "slow") == with_delay(original, 76)
                    other_after = saved(page, base, headers, "other")
                    for key in HIDDEN:
                        assert other_after[key] == original_other[key], key
                        assert other_after[key] != original[key], key
                    assert other_after == with_delay(original_other, 66), other_after
                    page.locator("#new-profile").click()

                    # Apply to all and Stop from the list.
                    page.route(
                        "**/api/general-profile",
                        lambda route: route.fulfill(
                            status=502,
                            content_type="application/json",
                            body='{"error":"Fixture network update failed"}',
                        )
                        if route.request.method == "POST"
                        else route.continue_(),
                    )
                    apply.click()
                    expect(page.locator("#message")).to_contain_text(
                        "Fixture network update failed"
                    )
                    expect(apply).to_be_enabled()
                    expect(row.locator(".general-badge")).to_have_count(0)
                    page.unroute("**/api/general-profile")

                    toggle = row.locator('button[data-action="general"]')
                    toggle.focus()
                    with page.expect_request(
                        lambda sent: sent.method == "POST"
                        and sent.url.endswith("/api/general-profile")
                    ) as applied:
                        page.keyboard.press("Enter")
                    assert applied.value.post_data_json == {"profile_id": "slow"}
                    expect(row.locator(".general-badge")).to_have_text("General · active")
                    expect(toggle).to_have_text("Stop")
                    expect(toggle).to_have_accessible_name("Stop Slow office")
                    expect(toggle).to_be_enabled()
                    expect(toggle).to_be_focused()
                    general = page.request.get(
                        base + "/api/general-profile", headers=headers
                    ).json()
                    assert general["profile"]["id"] == "slow" and general["active"] is True
                    page.evaluate("dismissToast()")
                    page.locator("section[aria-labelledby='saved-profiles-title']").screenshot(
                        path=f"test-results/saved-profiles-general-{width}.png"
                    )

                    # Focus stays on the button while Stop runs and after the list redraws.
                    stopping = []
                    page.route(
                        "**/api/general-profile/stop",
                        lambda route: stopping.append(route),
                    )
                    with page.expect_request(
                        lambda sent: sent.method == "POST"
                        and sent.url.endswith("/api/general-profile/stop")
                    ) as stopped:
                        page.keyboard.press("Enter")
                    assert stopped.value.post_data_json == {}
                    expect(toggle).to_have_attribute("aria-busy", "true")
                    expect(toggle).to_have_attribute("aria-disabled", "true")
                    expect(toggle).to_be_focused()
                    stopping[0].continue_()
                    page.unroute("**/api/general-profile/stop")
                    expect(row.locator(".general-badge")).to_have_count(0)
                    expect(toggle).to_have_text("Apply to all")
                    expect(toggle).not_to_have_attribute("aria-disabled", "true")
                    expect(toggle).to_be_focused()
                    general = page.request.get(
                        base + "/api/general-profile", headers=headers
                    ).json()
                    assert general["profile"] is None and general["active"] is False

                    # Edit reloads first so a change made elsewhere is not saved back stale.
                    changed = dict(saved(page, base, headers, "slow"), name="Renamed elsewhere")
                    assert page.request.post(
                        base + "/api/profiles", headers=headers, data=changed
                    ).ok
                    expect(row).to_contain_text("Slow office")
                    row.locator('button[data-action="edit"]').click()
                    expect(page.locator("#profile-name")).to_have_value("Renamed elsewhere")
                    expect(row).to_contain_text("Renamed elsewhere")

                    # Clearing the token locks the page and clears the form.
                    sidebar_control(page, "#open-admin-credentials").click()
                    page.locator("#admin-token").fill("")
                    expect(page.locator("#device-profile option")).to_have_count(0)
                    expect(page.locator("#device-profile")).to_be_disabled()
                    expect(page.locator("#save-device")).to_be_disabled()
                    for field, empty in (("profile-id", ""), ("profile-name", "")):
                        assert page.evaluate(f"document.getElementById('{field}').value") == empty
                    assert not page.evaluate("document.getElementById('profile-id').readOnly")
                    assert page.evaluate("document.getElementById('new-profile').hidden")
                    assert page.evaluate(
                        "document.querySelector('#profile-form-title').textContent.trim()"
                    ) == "Save profile"
                    page.locator("#admin-token").fill("browser-test")
                    page.locator("#check-admin-token").click()
                    expect(page.locator("#private-dashboard")).to_be_visible()
                    page.locator("#close-credentials").click()
                    expect(row).to_be_visible()
                    expect(page.locator("#profile-id")).to_have_value("")
                    expect(page.locator("#profile-form-title")).to_have_text("Save profile")
                    assert not errors, errors
                    context.close()
                print("Saved profiles dashboard acceptance passed at desktop and phone widths.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
