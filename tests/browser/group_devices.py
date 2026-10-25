"""Exercise group membership edits through the served dashboard."""

import os
import selectors
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.browser.confirm_dialogs import answer_confirm, cancel_with_escape
from tests.browser.phone import sidebar_control
from tests.browser.protected_clients import HEADERS, sign_in


def dismiss_saved_group_toast(page, members):
    noun = "device" if members == 1 else "devices"
    expect(page.locator("#toast")).to_have_class("toast success")
    expect(page.locator("#toast-message")).to_have_text(
        f"Device group saved and applied. {members} {noun}.")
    page.locator("#toast-dismiss").click()


def check_connected_group_help(page):
    """Keep the empty instruction readable without changing saved group choices."""
    page.locator('[data-page-link="devices"]').first.click()
    page.evaluate("""() => {
        window.groupHelpTest = {groups, data: lastConnectedData};
        groups = [];
        renderConnectedDevices({available: true, association_status_available: true, devices: [{
            mac_address: '02:00:00:00:00:fe', ipv4_address: '192.168.50.254',
            display_name: 'Group selector check', association_verified: true
        }]});
    }""")
    try:
        row = page.locator('.connected-row[data-mac-address="02:00:00:00:00:fe"]')
        select = row.locator('select[data-draft="group"]')
        help_text = row.get_by_text("Create a group on the Profiles page.", exact=True)
        expect(select.locator("option:checked")).to_have_text("No groups")
        expect(select).to_be_disabled()
        expect(help_text).to_be_visible()
        expect(select).to_have_attribute("aria-describedby", help_text.get_attribute("id"))
        assert select.evaluate("""node => {
            const style = getComputedStyle(node);
            const canvas = document.createElement('canvas');
            const context = canvas.getContext('2d');
            context.font = style.font;
            const textWidth = context.measureText(node.selectedOptions[0].textContent).width;
            return textWidth <= node.clientWidth - parseFloat(style.paddingLeft) -
                parseFloat(style.paddingRight) - 24;
        }"""), "Empty group text must fit beside the native select arrow"
        assert help_text.evaluate("node => node.scrollWidth <= node.clientWidth + 1")
        page.evaluate("""() => {
            groups = [
                {id: 'saved-group', name: 'Saved household group',
                    profile_id: 'normal', device_ids: []},
                {id: 'other-group', name: 'Another household group',
                    profile_id: 'normal', device_ids: []}
            ];
            const data = {...lastConnectedData, devices: lastConnectedData.devices.map(device =>
                ({...device, group_id: 'saved-group', assigned_device_id: 'saved-device'}))};
            renderConnectedDevices(data);
            renderConnectedDevices(data);
        }""")
        expect(select).to_have_value("saved-group")
        expect(select.locator("option:checked")).to_have_text("Saved household group")
        expect(select.locator('option[value="other-group"]')).to_have_text(
            "Another household group"
        )
        expect(help_text).to_have_count(0)
        expect(select).not_to_have_attribute("aria-describedby")
    finally:
        page.evaluate("""() => {
            groups = window.groupHelpTest.groups;
            renderConnectedDevices(window.groupHelpTest.data);
            delete window.groupHelpTest;
        }""")


def check_width(browser, base, width, height):
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    sign_in(page, base)
    check_connected_group_help(page)

    def post(path, data):
        response = page.request.post(base + path, headers=HEADERS, data=data)
        assert response.ok, (path, response.status, response.text())
        return response.json()

    for profile_id in ("normal", "slow"):
        post("/api/profiles", {"id": profile_id, "name": profile_id.title()})
    prefix = "group-" + str(width)
    for index, name in enumerate(("Laptop", "Phone")):
        post("/api/devices", {
            "id": prefix + "-" + str(index), "name": name,
            "ipv4_address": "192.168.50." + str(201 + index),
            "profile_id": "normal",
        })
    page.evaluate("refresh()")
    page.locator('[data-page-link="profiles"]').click()
    form = page.locator("#group-form")
    name = "Family " + str(width)
    form.locator("#group-id").fill(prefix)
    form.locator("#group-name").fill(name)
    profile = form.locator("#group-profile")
    profile.select_option("slow")
    laptop = form.locator('input[value="' + prefix + '-0"]')
    phone = form.locator('input[value="' + prefix + '-1"]')
    laptop.focus()
    laptop.press("Space")
    phone.check()
    expect(form.locator("#group-member-count")).to_have_text("2 of 15 selected")
    laptop.focus()
    page.evaluate("refresh()")
    expect(laptop).to_be_focused()
    expect(laptop).to_be_checked()
    expect(phone).to_be_checked()
    expect(profile).to_have_value("slow")
    held_saves = []

    def hold_save(route):
        if route.request.method == "POST":
            held_saves.append(route)
        else:
            route.continue_()

    if width == 1920:
        page.route("**/api/groups", hold_save)
    form.locator("#group-save").click()
    if width == 1920:
        expect(profile).to_be_disabled()
        page.evaluate("""() => document.getElementById('group-form').dispatchEvent(
            new Event('submit', {bubbles: true, cancelable: true}))""")
        assert len(held_saves) == 1
        held_saves[0].continue_()
        page.unroute("**/api/groups", hold_save)
    row = page.locator("#group-list .quota-row").filter(has_text=name)
    expect(row).to_contain_text("2 devices:")
    expect(row).to_contain_text("Profile: Slow")
    # The saved-result toast can cover the phone's next group action.
    dismiss_saved_group_toast(page, 2)

    row.get_by_role("button", name="Edit devices").click()
    expect(form.locator("#group-id")).to_have_js_property("readOnly", True)
    phone.uncheck()
    page.evaluate("refresh()")
    expect(phone).not_to_be_checked()
    form.get_by_role("button", name="Cancel", exact=True).click()
    expect(form.locator("#group-id")).to_have_js_property("readOnly", False)
    response = page.request.get(base + "/api/groups", headers=HEADERS).json()
    assert len(next(group for group in response["groups"] if group["id"] == prefix)["device_ids"]) == 2

    row.get_by_role("button", name="Edit devices").click()
    phone.uncheck()
    if width == 1920:
        def reject_save(route):
            route.fulfill(status=409, json={"error": "The selected group changed. Review and try again."})

        page.route("**/api/groups", reject_save, times=1)
        form.locator("#group-save").click()
        expect(form.locator("#group-validation")).to_contain_text("group changed")
        expect(profile).to_have_value("slow")
        expect(laptop).to_be_checked()
        expect(phone).not_to_be_checked()
        page.unroute("**/api/groups", reject_save)
    form.locator("#group-save").click()
    expect(row).to_contain_text("1 device:")
    dismiss_saved_group_toast(page, 1)
    devices = page.request.get(base + "/api/devices", headers=HEADERS).json()["devices"]
    assert next(device for device in devices if device["id"] == prefix + "-1")["profile_id"] == "normal"

    row.get_by_role("button", name="Edit devices").click()
    laptop.uncheck()
    expect(form.locator("#group-save")).to_be_disabled()
    expect(form.locator("#group-validation")).to_contain_text("at least one device")
    laptop.check()

    async_profile = "**/api/profiles"

    def missing_profile(route):
        data = route.fetch().json()
        data["profiles"] = [item for item in data["profiles"] if item["id"] != "slow"]
        route.fulfill(json=data)

    page.route(async_profile, missing_profile)
    page.evaluate("loadProfiles()")
    expect(profile).to_have_value("slow")
    expect(form.locator("#group-validation")).to_contain_text("no longer available")
    expect(form.locator("#group-save")).to_be_disabled()
    page.unroute(async_profile, missing_profile)
    page.evaluate("loadProfiles()")
    expect(form.locator("#group-save")).to_be_enabled()

    if width == 1920:
        def fail_group_read(route):
            route.fulfill(status=503, json={"error": "Group details could not load."})

        page.route("**/api/groups", fail_group_read, times=1)
        page.evaluate("loadGroups()")
        expect(form.locator("#group-save")).to_be_disabled()
        expect(laptop).to_be_checked()
        expect(profile).to_have_value("slow")
        page.unroute("**/api/groups", fail_group_read)
        page.evaluate("loadGroups()")
        expect(form.locator("#group-save")).to_be_enabled()

    # A selected client that disappears stays checked until deliberately removed.
    form.get_by_role("button", name="Cancel", exact=True).click()
    form.locator("#group-id").fill(prefix + "-live")
    form.locator("#group-name").fill("Connected group")
    live = form.locator('input[value="live:02:00:00:00:00:01"]')
    live.check()
    expect(form.locator("#group-validation")).to_contain_text("without a saved profile")
    profile.select_option("slow")
    expect(form.locator("#group-save")).to_be_enabled()
    page.evaluate("""() => {
        renderConnectedDevices({available: true, association_status_available: true, devices: []});
    }""")
    expect(live).to_be_checked()
    expect(form.locator("#group-validation")).to_contain_text("unavailable")
    expect(form.locator("#group-save")).to_be_disabled()
    live.uncheck()
    page.evaluate("loadConnectedDevices()")
    live.check()
    form.locator("#group-save").click()
    connected_row = page.locator("#group-list .quota-row").filter(has_text="Connected group")
    expect(connected_row).to_be_visible()
    dismiss_saved_group_toast(page, 1)
    device = next(device for device in page.request.get(base + "/api/devices", headers=HEADERS).json()["devices"]
                  if device.get("mac_address") == "02:00:00:00:00:01")
    assert device["profile_id"] == "slow"
    delete = connected_row.get_by_role("button", name="Delete group", exact=True)
    delete.click()
    cancel_with_escape(page, delete, verb="Delete group", title="Delete this group?",
                       text="Members keep their saved profiles.")
    expect(connected_row).to_have_count(1)
    delete.click()
    answer_confirm(page, "Delete group", title="Delete this group?", text="saved profiles")
    expect(connected_row).to_have_count(0)
    assert any(item["id"] == device["id"] for item in page.request.get(
        base + "/api/devices", headers=HEADERS).json()["devices"])

    # Keep other groups' members visible with an explanation of their disabled control.
    expect(laptop).to_be_disabled()
    expect(laptop.locator("..")).to_contain_text("Already in " + name)
    page.evaluate("""() => {
        savedDevices = Array.from({length: 16}, (_, i) => ({
            id: 'limit-' + i, name: 'Device ' + i,
            ipv4_address: '192.168.50.' + (30 + i), profile_id: 'normal'
        }));
        updateGroupMemberOptions();
    }""")
    for index in range(15):
        form.locator('input[value="limit-' + str(index) + '"]').check()
    expect(form.locator('input[value="limit-15"]')).to_be_disabled()
    form.locator('input[value="limit-0"]').uncheck()
    expect(form.locator('input[value="limit-15"]')).to_be_enabled()
    page.evaluate('stopEditing("group"); refresh()')
    row.get_by_role("button", name="Edit devices").click()
    form.screenshot(path="test-results/group-devices-" + str(width) + ".png")
    assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
    if width == 1920:
        page.evaluate("document.documentElement.style.zoom = '2'")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        page.evaluate("document.documentElement.style.zoom = ''")

    sidebar_control(page, "#open-admin-credentials").click()
    page.locator("#admin-token").fill("changed-token")
    expect(form.locator("#group-devices input")).to_have_count(0)
    expect(form.locator("#group-name")).to_have_value("")
    expect(page.locator("#group-list")).to_be_empty()
    assert not errors, errors
    # Leave the shared fixture small enough for the next viewport's plan.
    response = context.request
    response.post(base + "/api/groups/delete", headers=HEADERS, data={"id": prefix})
    for device_id in (prefix + "-0", prefix + "-1", device["id"]):
        response.post(base + "/api/devices/delete", headers=HEADERS, data={"id": device_id})
    context.close()


def run():
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.browser.fixture"], stdout=subprocess.PIPE, text=True
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise RuntimeError("Group dashboard fixture did not start")
            port = int(process.stdout.readline().strip())
        Path("test-results").mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("JITTERSIM_BROWSER_EXECUTABLE")
            )
            try:
                for width, height in ((1920, 1000), (390, 844)):
                    check_width(browser, "http://127.0.0.1:" + str(port), width, height)
                print("Group device controls passed at desktop and phone widths.")
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    run()
