"""Check that a protected client looks protected and its profile and group controls are off."""

import os
import re
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

MAC_ADDRESS = "02:00:00:00:00:01"
IP_ADDRESS = "192.168.50.184"
HEADERS = {"Authorization": "Bearer browser-test"}
BASE_URL = [""]


def controls(row):
    return {
        "profile": row.locator('select[aria-label^="Profile for"]'),
        "apply": row.get_by_role("button", name=re.compile("^(Apply|Assign) profile")),
        "group": row.locator('select[aria-label^="Group for"]'),
        "add": row.get_by_role("button", name="Add to group"),
    }


def expect_greyed_out(row):
    # to_be_disabled also accepts aria-disabled so check the real property as well
    for name, control in controls(row).items():
        expect(control, name).to_have_js_property("disabled", True)
        expect(control, name).to_have_attribute("aria-disabled", "true")
    expect(row.get_by_role("button", name=re.compile("^Remove protection"))).to_have_js_property(
        "disabled", False
    )
    expect(row.get_by_role("button", name=re.compile("^Remove protection"))).not_to_have_attribute(
        "aria-disabled", re.compile(".*")
    )
    row.locator("summary", has_text="Edit name and type").click()
    expect(row.locator('input[data-draft="name"]')).to_be_enabled()
    expect(row.locator('select[data-draft="type"]')).to_be_enabled()
    expect(row.get_by_role("button", name="Save details")).to_be_enabled()
    row.locator("summary", has_text="Edit name and type").click()


def expect_ordinary(row):
    expect(row).not_to_have_class(re.compile("protected"))
    expect(row.locator(".protected-badge")).to_have_count(0)
    expect(row.get_by_role("button", name=re.compile("^Make unaffected"))).to_be_enabled()
    found = controls(row)
    for name in ("profile", "group"):
        expect(found[name], name).to_have_js_property("disabled", False)
        expect(found[name], name).not_to_have_attribute("aria-disabled", re.compile(".*"))
    # the buttons follow the chosen values exactly as before
    found["profile"].select_option("slow")
    expect(found["apply"]).to_have_js_property("disabled", False)
    expect(found["apply"]).not_to_have_attribute("aria-disabled", re.compile(".*"))
    found["group"].select_option("lab")
    expect(found["add"]).to_have_js_property("disabled", False)
    expect(found["add"]).not_to_have_attribute("aria-disabled", re.compile(".*"))


def look(row):
    return row.evaluate(
        """(node) => ({
            dot: getComputedStyle(node.querySelector('.connected-dot')).backgroundColor,
            border: getComputedStyle(node).borderTopColor
        })"""
    )


def sign_in(page, base):
    page.goto(base)
    expect(page.locator("#private-dashboard")).to_be_hidden()
    page.locator("#open-admin-credentials").click()
    page.locator("#admin-token").fill("browser-test")
    page.locator("#check-admin-token").click()
    expect(page.locator("#private-dashboard")).to_be_visible()
    page.locator("#remember-admin-token").check()
    page.locator("#close-credentials").click()


def saved_device_row(page, width, name):
    """The row or card of the saved device with this address on the Devices page."""

    page.locator('a[data-page-link="devices"]').first.click()
    expect(page.locator("#page-devices")).to_be_visible()
    if width > 600:
        return page.locator("#device-table tbody tr", has_text=name)
    return page.locator("#device-cards .device-card", has_text=name)


def check_saved_device_rows(page, post, width):
    """A saved device that is protected or cannot be checked never shows as degraded."""

    saved = next(
        device
        for device in page.request.get(BASE_URL[0] + "/api/devices", headers=HEADERS).json()["devices"]
        if device["ipv4_address"] == IP_ADDRESS
    )
    alice = {
        "id": saved["id"], "name": saved["name"], "ipv4_address": IP_ADDRESS,
        "profile_id": "slow",
    }
    name = IP_ADDRESS
    # Saved with the protected MAC address: its profile is paused and the row is greyed out.
    post("/api/devices", {**alice, "mac_address": MAC_ADDRESS})
    page.reload()
    row = saved_device_row(page, width, name)
    expect(row).to_contain_text("Protected: profile paused")
    expect(row).not_to_contain_text("Degraded")
    expect(row).not_to_contain_text("delay")
    if width > 600:
        expect(row).to_have_class(re.compile(r"\bprotected\b"))
        assert row.evaluate("(node) => getComputedStyle(node.cells[0]).color") != \
            page.locator("#device-table tbody tr").first.evaluate(
                "(node) => getComputedStyle(node.cells[0].querySelector('strong')).color"
            )
    # The sidebar does not count a paused device as affected.
    expect(page.locator("#apply-summary")).not_to_contain_text("1 device")

    # Saved without a MAC address while the protected client holds its address.
    post("/api/devices", {**alice, "mac_address": None, "expected": {**alice, "mac_address": MAC_ADDRESS}})
    page.reload()
    row = saved_device_row(page, width, name)
    expect(row).to_contain_text("Profile paused")
    expect(row).to_contain_text("This saved device has no MAC address so JitterSim cannot tell it is the protected Fixture client")
    expect(row).not_to_contain_text("Degraded")
    if width > 600:
        expect(row).to_have_class(re.compile(r"\bpaused\b"))
        row.get_by_role("button", name=f"Save MAC {MAC_ADDRESS}").click()
        expect(row).to_contain_text("Protected: profile paused")
        expect(row.get_by_role("button", name=re.compile("^Save MAC"))).to_have_count(0)
    else:
        post("/api/devices", {**alice, "mac_address": MAC_ADDRESS, "expected": {**alice, "mac_address": None}})
        page.reload()
        expect(saved_device_row(page, width, name)).to_contain_text("Protected: profile paused")


def check_width(browser, base, width, height):
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    BASE_URL[0] = base
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    sign_in(page, base)
    request = page.request

    def post(path, data):
        response = request.post(base + path, headers=HEADERS, data=data)
        assert response.ok, (path, response.status, response.text())

    # start from an unprotected client whatever an earlier width left behind
    request.post(
        base + "/api/unaffected-devices/delete", headers=HEADERS, data={"mac_address": MAC_ADDRESS}
    )
    post(
        "/api/profiles",
        {
            "id": "slow",
            "name": "Shared slow network",
            "upload": {"bandwidth_kbit": 1000},
            "download": {"bandwidth_kbit": 2000},
        },
    )
    # a group the client is not in so the group select has something to offer
    post(
        "/api/devices",
        {"id": "lab-pc", "name": "Lab PC", "ipv4_address": "192.168.50.200", "profile_id": "slow"},
    )
    post(
        "/api/groups",
        {"id": "lab", "name": "Lab", "device_ids": ["lab-pc"], "profile_id": "slow"},
    )
    # a saved profile makes the protected row's profile select start on a value
    post(
        "/api/connected-devices/apply-profile",
        {"ipv4_address": IP_ADDRESS, "mac_address": MAC_ADDRESS, "profile_id": "slow"},
    )
    page.reload()
    row = page.locator(f'.connected-row[data-mac-address="{MAC_ADDRESS}"]')
    expect(row).to_be_visible()

    expect_ordinary(row)
    ordinary_look = look(row)
    page.wait_for_timeout(300)  # let the 160 ms button fade finish
    row.screenshot(path=f"test-results/protected-clients-ordinary-{width}.png")

    post("/api/unaffected-devices", {"mac_address": MAC_ADDRESS, "display_name": "Fixture client"})
    page.reload()
    row = page.locator(f'.connected-row[data-mac-address="{MAC_ADDRESS}"]')
    expect(row).to_be_visible()
    expect(row).to_have_class(re.compile(r"\bprotected\b"))
    expect(row.locator(".protected-badge")).to_have_text("Protected")
    expect(row).to_contain_text("Unaffected by all profiles and quotas")
    protected_look = look(row)
    assert protected_look["dot"] != ordinary_look["dot"], (protected_look, ordinary_look)
    assert protected_look["border"] != ordinary_look["border"], (protected_look, ordinary_look)
    expect_greyed_out(row)
    page.wait_for_timeout(300)  # let the 160 ms button fade finish
    row.screenshot(path=f"test-results/protected-clients-protected-{width}.png")
    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1

    check_saved_device_rows(page, post, width)
    row = page.locator(f'.connected-row[data-mac-address="{MAC_ADDRESS}"]')

    row.get_by_role("button", name=re.compile("^Remove protection")).click()
    expect(row.get_by_role("button", name=re.compile("^Make unaffected"))).to_be_visible()
    expect(row).not_to_have_class(re.compile("protected"))
    expect(row.locator(".protected-badge")).to_have_count(0)
    expect(controls(row)["profile"]).to_have_js_property("disabled", False)
    expect(controls(row)["group"]).to_have_js_property("disabled", False)
    assert not errors, errors
    context.close()


def run():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Protected clients dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                for width, height in ((1920, 1000), (390, 844)):
                    check_width(browser, f"http://127.0.0.1:{port}", width, height)
                print("Protected client acceptance passed at desktop and phone widths.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
