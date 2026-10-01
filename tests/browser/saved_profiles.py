"""List saved profiles, edit one without losing a field and apply it to all devices."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

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
    "download": {"delay_ms": 80, "bandwidth_kbit": 1500},
    "random_seed": 99,
    "reroll_interval_seconds": 45,
    "reroll_interval_randomness": {"variation_percent": 7, "enabled_probability": 80},
    "outage_probability": 12,
    "outage_duration_seconds": 33,
    "outage_interval_seconds": 77,
    "outage_duration_randomness": {"variation_percent": 11, "enabled_probability": 60},
    "outage_interval_randomness": {"variation_percent": 11, "enabled_probability": 60},
    "website_mode": "allow_only",
    "website_domains": ["a.example.com", "b.example.com"],
    "website_randomness": {"variation_percent": 13, "enabled_probability": 40},
}


def saved_slow(page, base, headers):
    profiles = page.request.get(base + "/api/profiles", headers=headers).json()["profiles"]
    matching = [profile for profile in profiles if profile["id"] == "slow"]
    assert len(matching) == 1, matching
    return matching[0]


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
                    assert page.request.post(
                        base + "/api/profiles", headers=headers, data=SLOW
                    ).ok
                    original = saved_slow(page, base, headers)
                    assert original["outage_duration_randomness"] == {
                        "variation_percent": 11, "enabled_probability": 60,
                    }

                    page.goto(base + "/#profiles")
                    page.locator("#open-admin-credentials").click()
                    page.locator("#admin-token").fill("browser-test")
                    page.locator("#check-admin-token").click()
                    expect(page.locator("#private-dashboard")).to_be_visible()
                    page.locator("#close-credentials").click()
                    expect(page.locator("#page-profiles")).to_be_visible()

                    row = page.locator('.saved-profile-row[data-profile-id="slow"]')
                    expect(row).to_be_visible()
                    expect(row).to_contain_text("Slow office")
                    expect(row.locator(".saved-profile-id")).to_have_text("slow")
                    expect(row.locator(".saved-profile-summary")).to_have_text(
                        "↑ 50 ms ±10 · 2% loss · 5 Mbit/s · ↓ 80 ms · 1.5 Mbit/s"
                    )
                    expect(row.locator(".general-badge")).to_have_count(0)
                    edit = row.get_by_role("button", name="Edit Slow office")
                    apply = row.get_by_role("button", name="Apply to all: Slow office")
                    expect(edit).to_have_text("Edit")
                    expect(apply).to_have_text("Apply to all")
                    expect(apply).to_be_enabled()
                    expect(page.locator("#new-profile")).to_be_hidden()
                    expect(page.locator("#profile-form-title")).to_have_text("Save profile")
                    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
                    page.locator("section[aria-labelledby='saved-profiles-title']").screenshot(
                        path=f"test-results/saved-profiles-list-{width}.png"
                    )

                    edit.click()
                    expect(page.locator("#profile-form-title")).to_have_text("Edit profile")
                    expect(page.locator("#profile-id")).to_have_value("slow")
                    expect(page.locator("#profile-id")).not_to_be_editable()
                    expect(page.locator("#profile-name")).to_have_value("Slow office")
                    expect(page.locator("#upload-delay")).to_have_value("50")
                    expect(page.locator("#download-bandwidth")).to_have_value("1500")
                    expect(page.locator("#profile-outage-probability")).to_have_value("12")
                    expect(page.locator("#profile-website-domains")).to_have_value(
                        "a.example.com, b.example.com"
                    )
                    expect(page.locator("#new-profile")).to_be_visible()
                    expect(page.locator("#profile-form-title")).to_be_in_viewport()
                    page.evaluate("dismissToast()")
                    page.locator("#profile-form").screenshot(
                        path=f"test-results/saved-profiles-edit-{width}.png"
                    )
                    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1

                    page.locator("#upload-delay").fill("75")
                    page.locator("#profile-form button[type='submit']").click()
                    expect(page.locator("#message")).to_contain_text("Profile saved.")
                    expect(row.locator(".saved-profile-summary")).to_have_text(
                        "↑ 75 ms ±10 · 2% loss · 5 Mbit/s · ↓ 80 ms · 1.5 Mbit/s"
                    )
                    after = saved_slow(page, base, headers)
                    assert after["outage_duration_randomness"] == {
                        "variation_percent": 11, "enabled_probability": 60,
                    }
                    assert after["outage_interval_randomness"] == {
                        "variation_percent": 11, "enabled_probability": 60,
                    }
                    assert after["website_randomness"] == {
                        "variation_percent": 13, "enabled_probability": 40,
                    }
                    assert after["reroll_interval_randomness"] == {
                        "variation_percent": 7, "enabled_probability": 80,
                    }
                    expected = dict(original, upload=dict(original["upload"], delay_ms=75))
                    assert after == expected, (after, expected)
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

                    with page.expect_request(
                        lambda sent: sent.method == "POST"
                        and sent.url.endswith("/api/general-profile")
                    ) as applied:
                        apply.click()
                    assert applied.value.post_data_json == {"profile_id": "slow"}
                    expect(row.locator(".general-badge")).to_have_text("General · active")
                    stop = row.get_by_role("button", name="Stop Slow office")
                    expect(stop).to_have_text("Stop")
                    expect(stop).to_be_enabled()
                    general = page.request.get(
                        base + "/api/general-profile", headers=headers
                    ).json()
                    assert general["profile"]["id"] == "slow" and general["active"] is True
                    page.evaluate("dismissToast()")
                    page.locator("section[aria-labelledby='saved-profiles-title']").screenshot(
                        path=f"test-results/saved-profiles-general-{width}.png"
                    )

                    with page.expect_request(
                        lambda sent: sent.method == "POST"
                        and sent.url.endswith("/api/general-profile/stop")
                    ) as stopped:
                        stop.click()
                    assert stopped.value.post_data_json == {}
                    expect(row.locator(".general-badge")).to_have_count(0)
                    expect(apply).to_have_text("Apply to all")
                    general = page.request.get(
                        base + "/api/general-profile", headers=headers
                    ).json()
                    assert general["profile"] is None and general["active"] is False
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
