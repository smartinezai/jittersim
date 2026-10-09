"""Drive the device, group, service and time limit forms through the served dashboard.

Each form is filled with valid values and saved, the page is reloaded and the saved row is read
back, then the row is edited, reset and deleted. Refused input shows its reason next to the form
and sends nothing, a refusal from the Pi keeps what was typed, and the keyboard reaches everything.
"""

import re

from playwright.sync_api import expect

from tests.browser.forms_support import (
    DEVICE,
    DEVICE_TITLE,
    clear_toast,
    discard_all_drafts,
    fail_posts,
    field_error,
    get,
    no_overflow,
    open_card,
    open_page,
    post,
    record_writes,
    reload_signed_in,
    rows_inside,
    run_journey,
    unlock,
)

GIGABYTE = 1073741824
MEGABYTE = 1048576


def save(page, label):
    page.get_by_role("button", name=label, exact=True).click()


def posted(path):
    return lambda sent: sent.method == "POST" and sent.url.endswith(path)


def check_reset_fields(page, prefix):
    """The interval shows for the interval reset and the times for the daily reset, no others."""

    interval, times = page.locator(f"#{prefix}-interval"), page.locator(f"#{prefix}-times")
    for mode, shown in (("manual", None), ("interval", interval), ("daily", times)):
        page.locator(f"#{prefix}-reset-mode").select_option(mode)
        for field in (interval, times):
            if field is shown:
                expect(field).to_be_visible()
                expect(field).to_be_enabled()
            else:
                expect(field).to_be_hidden()
    page.locator(f"#{prefix}-reset-mode").select_option("manual")


def check_reset_validation(page, prefix, button, writes):
    """Reset settings the form refuses: the page sends nothing and says why."""

    before = len(writes)
    mode = page.locator(f"#{prefix}-reset-mode")
    interval, times = page.locator(f"#{prefix}-interval"), page.locator(f"#{prefix}-times")
    mode.select_option("interval")
    for value, reason in (
        ("0", "Value must be greater than or equal to 1."),
        ("525601", "Value must be less than or equal to 525600."),
    ):
        interval.fill(value)
        save(page, button)
        expect(field_error(page, f"{prefix}-interval")).to_contain_text(reason)
    interval.fill("60")
    mode.select_option("daily")
    for value, reason in (
        ("", "Add at least one daily reset time."),
        ("25:00", "Reset times must use HH:MM format."),
        ("08:60", "Reset times must use HH:MM format."),
        ("noon", "Reset times must use HH:MM format."),
    ):
        times.fill(value)
        save(page, button)
        expect(page.locator("#message")).to_have_text(reason)
        expect(page.get_by_role("button", name=button, exact=True)).to_be_enabled()
    assert writes[before:] == [], writes[before:]
    mode.select_option("manual")
    clear_toast(page)


def check_hidden_interval(page, prefix, button, path):
    """An out-of-range interval in a field that is hidden must not stop the save."""

    mode = page.locator(f"#{prefix}-reset-mode")
    mode.select_option("interval")
    page.locator(f"#{prefix}-interval").fill("0")
    mode.select_option("manual")
    with page.expect_request(posted(path)) as sent:
        save(page, button)
    assert sent.value.post_data_json["reset_mode"] == "manual"
    assert sent.value.post_data_json["reset_interval_seconds"] == 0
    # The saving note replaces the last result at once, so "saved" is the answer to this save.
    expect(page.locator("#message")).to_contain_text("saved")
    page.wait_for_function("() => !dashboardRefresh")
    clear_toast(page)
    mode.select_option("interval")
    page.locator(f"#{prefix}-interval").fill("60")
    mode.select_option("manual")


def quota_rows(page, list_id):
    return page.locator(f"#{list_id} .quota-row")


def row_button(page, list_id, label):
    return page.locator(f"#{list_id}").get_by_role("button", name=label, exact=True)


def check_data_limit(page, base, width):
    writes = record_writes(page)
    open_card(page, "Device data limit")
    button = "Save device limit"
    form = page.locator("#data-limit-form")
    expect(page.locator("#data-limit-count")).to_have_text("0 limits")
    expect(page.locator("#data-limit-list")).to_have_text("No device limits saved.")
    expect(page.locator("#data-limit-amount")).to_have_value("1")
    check_reset_fields(page, "data-limit")

    page.locator("#data-limit-device").select_option(DEVICE)
    page.locator("#data-limit-profile").select_option("slow")
    page.locator("#data-limit-amount").fill("2")
    page.locator("#data-limit-unit").select_option(str(GIGABYTE))
    page.locator("#data-limit-reset-mode").select_option("daily")
    page.locator("#data-limit-times").fill("8:00, 20:30")
    with page.expect_request(posted("/api/data-limits")) as sent:
        save(page, button)
    assert sent.value.post_data_json == {
        "device_id": DEVICE, "profile_id": "slow", "limit_bytes": 2 * GIGABYTE, "enabled": True,
        "reset_mode": "daily", "reset_interval_seconds": 0, "reset_times": [480, 1230],
    }, sent.value.post_data_json
    expect(page.locator("#message")).to_have_text("Device limit saved and applied.")
    row = quota_rows(page, "data-limit-list")
    expect(row).to_have_count(1)
    expect(page.locator("#data-limit-count")).to_have_text("1 limit")
    expect(row.locator("strong")).to_have_text(f"{DEVICE_TITLE} · Slow connection")
    expect(row).to_contain_text("resets daily at 08:00, 20:30")
    expect(row).to_contain_text("0 bytes used of 2.00 GB · 2.00 GB left")
    assert rows_inside(page.locator("#data-limit-list"))
    no_overflow(page, width)
    reload_signed_in(page)
    open_page(page, "schedules")
    expect(quota_rows(page, "data-limit-list")).to_have_count(1)
    expect(quota_rows(page, "data-limit-list")).to_contain_text("resets daily at 08:00, 20:30")
    form.screenshot(path=f"test-results/data-limit-form-{width}.png")

    # Edit fills the form from the row and a new save changes the row in place.
    row_button(page, "data-limit-list", "Edit").click()
    expect(page.locator("#data-limit-device")).to_have_value(DEVICE)
    expect(page.locator("#data-limit-profile")).to_have_value("slow")
    expect(page.locator("#data-limit-amount")).to_have_value("2")
    expect(page.locator("#data-limit-unit")).to_have_value(str(GIGABYTE))
    expect(page.locator("#data-limit-reset-mode")).to_have_value("daily")
    expect(page.locator("#data-limit-times")).to_have_value("08:00, 20:30")
    expect(page.locator("#data-limit-times")).to_be_visible()
    expect(page.locator("#data-limit-interval")).to_be_hidden()
    page.locator("#data-limit-amount").fill("750")
    page.locator("#data-limit-unit").select_option(str(MEGABYTE))
    page.locator("#data-limit-reset-mode").select_option("interval")
    page.locator("#data-limit-interval").fill("90")
    expect(page.locator("#draft-links")).to_contain_text("Review Device data limit")
    save(page, button)
    expect(page.locator("#message")).to_have_text("Device limit saved and applied.")
    expect(quota_rows(page, "data-limit-list")).to_have_count(1)
    expect(quota_rows(page, "data-limit-list")).to_contain_text("resets every 90 minutes")
    expect(quota_rows(page, "data-limit-list")).to_contain_text("0 bytes used of 750.0 MB")
    saved = get(page, base, "/api/data-limits")["data_limits"][0]
    assert (saved["limit_bytes"], saved["reset_mode"], saved["reset_interval_seconds"],
            saved["reset_times"]) == (750 * MEGABYTE, "interval", 5400, [])
    expect(page.locator("#draft-notice")).to_be_hidden()
    clear_toast(page)

    # The edges of the interval are accepted and a limit can be turned off.
    for minutes, words in (("1", "1 minute(?!s)"), ("525600", "525600 minutes")):
        page.locator("#data-limit-interval").fill(minutes)
        save(page, button)
        expect(quota_rows(page, "data-limit-list")).to_contain_text(
            re.compile(f"resets every {words}")
        )
    page.locator("#data-limit-enabled").uncheck()
    save(page, button)
    expect(page.locator("#message")).to_contain_text("Device limit saved")
    assert get(page, base, "/api/data-limits")["data_limits"][0]["enabled"] is False
    page.locator("#data-limit-enabled").check()
    save(page, button)
    clear_toast(page)

    # Refused input shows its reason and sends nothing.
    before = len(writes)
    for value, reason in (
        ("0", "Value must be greater than or equal to 1."),
        ("", "Please fill out this field."),
        ("1.5", "Please enter a valid value."),
    ):
        page.locator("#data-limit-amount").fill(value)
        save(page, button)
        expect(field_error(page, "data-limit-amount")).to_contain_text(reason)
        expect(page.locator("#data-limit-amount")).to_have_attribute("aria-invalid", "true")
    assert writes[before:] == [], writes[before:]
    page.locator("#data-limit-amount").fill("3")
    expect(field_error(page, "data-limit-amount")).to_have_count(0)
    check_reset_validation(page, "data-limit", button, writes)
    check_hidden_interval(page, "data-limit", button, "/api/data-limits")

    # The Pi refuses a limit that is too large for it: the reason shows and the values stay.
    page.locator("#data-limit-amount").fill("99999999999")
    page.locator("#data-limit-unit").select_option(str(GIGABYTE))
    save(page, button)
    expect(page.locator("#data-limit-form .form-error")).to_contain_text(
        "limit_bytes must be between 1 and 9223372036854775807"
    )
    expect(page.locator("#data-limit-amount")).to_have_value("99999999999")
    expect(page.locator("#data-limit-unit")).to_have_value(str(GIGABYTE))
    expect(page.locator("#draft-links")).to_contain_text("Review Device data limit")
    expect(page.get_by_role("button", name=button, exact=True)).to_be_enabled()
    assert get(page, base, "/api/data-limits")["data_limits"][0]["limit_bytes"] == 3 * MEGABYTE
    page.locator("#data-limit-amount").fill("4")
    handler = fail_posts(page, "**/api/data-limits", "Save unavailable")
    save(page, button)
    expect(page.locator("#data-limit-form .form-error")).to_have_text("Save unavailable")
    expect(page.locator("#data-limit-amount")).to_have_value("4")
    page.unroute("**/api/data-limits", handler)

    # The keyboard: Space turns the check box off, Enter in the amount saves the form.
    page.locator("#data-limit-enabled").focus()
    page.keyboard.press("Space")
    expect(page.locator("#data-limit-enabled")).not_to_be_checked()
    page.locator("#data-limit-amount").focus()
    with page.expect_request(posted("/api/data-limits")) as sent:
        page.keyboard.press("Enter")
    assert sent.value.post_data_json["limit_bytes"] == 4 * GIGABYTE
    assert sent.value.post_data_json["enabled"] is False
    expect(quota_rows(page, "data-limit-list")).to_contain_text("4.00 GB left")
    expect(page.locator("#data-limit-form .form-error")).to_have_count(0)
    clear_toast(page)

    # Reset usage and Delete work from the row, with the keyboard too.
    reset = row_button(page, "data-limit-list", "Reset usage")
    reset.focus()
    with page.expect_request(posted("/api/data-limits/reset")) as sent:
        page.keyboard.press("Enter")
    assert sent.value.post_data_json == {"device_id": DEVICE}
    expect(page.locator("#message")).to_have_text("Quota usage reset.")
    delete = row_button(page, "data-limit-list", "Delete")
    delete.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#message")).to_have_text("Device limit deleted.")
    expect(page.locator("#data-limit-list")).to_have_text("No device limits saved.")
    expect(page.locator("#data-limit-count")).to_have_text("0 limits")
    assert get(page, base, "/api/data-limits")["data_limits"] == []
    no_overflow(page, width)
    clear_toast(page)

    # Discard unfinished edits shows the reset fields that match the menu it goes back to.
    page.locator("#data-limit-reset-mode").select_option("daily")
    page.locator("#data-limit-times").fill("09:00")
    discard_all_drafts(page)
    expect(page.locator("#data-limit-reset-mode")).to_have_value("manual")
    expect(page.locator("#data-limit-times")).to_be_hidden()
    expect(page.locator("#data-limit-interval")).to_be_hidden()


def check_group_data_limit(page, base, width):
    writes = record_writes(page)
    open_card(page, "Shared group data limit")
    button = "Save group limit"
    expect(page.locator("#group-data-limit-count")).to_have_text("0 group limits")
    expect(page.locator("#group-data-limit-list")).to_have_text("No shared group limits saved.")
    expect(page.locator("#group-data-limit-group option")).to_have_text(
        ["Family (2 devices)", "Solo (1 device)"]
    )
    check_reset_fields(page, "group-data-limit")

    page.locator("#group-data-limit-group").select_option("family")
    page.locator("#group-data-limit-profile").select_option("slow")
    page.locator("#group-data-limit-amount").fill("500")
    page.locator("#group-data-limit-reset-mode").select_option("interval")
    page.locator("#group-data-limit-interval").fill("90")
    with page.expect_request(posted("/api/group-data-limits")) as sent:
        save(page, button)
    assert sent.value.post_data_json == {
        "group_id": "family", "profile_id": "slow", "limit_bytes": 500 * MEGABYTE,
        "enabled": True, "reset_mode": "interval", "reset_interval_seconds": 5400,
        "reset_times": [],
    }, sent.value.post_data_json
    expect(page.locator("#message")).to_have_text("Shared group limit saved and applied.")
    # The saved limit is listed with its group and fallback profile.
    expect(page.locator("#group-data-limit-count")).to_have_text("1 group limit")
    row = quota_rows(page, "group-data-limit-list")
    expect(row).to_have_count(1)
    expect(row.locator("strong")).to_have_text("Family · Slow connection")
    expect(row).to_contain_text("resets every 90 minutes")
    expect(row).to_contain_text("0 bytes used of 500.0 MB · 500.0 MB left")
    expect(row).not_to_contain_text("NaN")
    expect(row).not_to_contain_text("weight")
    expect(row).not_to_contain_text("Domains")
    assert rows_inside(page.locator("#group-data-limit-list"))
    no_overflow(page, width)
    reload_signed_in(page)
    open_page(page, "schedules")
    expect(quota_rows(page, "group-data-limit-list")).to_have_count(1)
    page.locator("#group-data-limit-form").screenshot(
        path=f"test-results/group-data-limit-form-{width}.png"
    )

    row_button(page, "group-data-limit-list", "Edit").click()
    expect(page.locator("#group-data-limit-group")).to_have_value("family")
    expect(page.locator("#group-data-limit-profile")).to_have_value("slow")
    expect(page.locator("#group-data-limit-amount")).to_have_value("500")
    expect(page.locator("#group-data-limit-unit")).to_have_value(str(MEGABYTE))
    expect(page.locator("#group-data-limit-reset-mode")).to_have_value("interval")
    expect(page.locator("#group-data-limit-interval")).to_have_value("90")
    page.locator("#group-data-limit-amount").fill("1")
    page.locator("#group-data-limit-unit").select_option(str(GIGABYTE))
    page.locator("#group-data-limit-reset-mode").select_option("daily")
    page.locator("#group-data-limit-times").fill("06:00")
    save(page, button)
    expect(page.locator("#message")).to_have_text("Shared group limit saved and applied.")
    expect(quota_rows(page, "group-data-limit-list")).to_contain_text("resets daily at 06:00")
    expect(quota_rows(page, "group-data-limit-list")).to_contain_text("1.00 GB left")
    expect(page.locator("#group-data-limit-count")).to_have_text("1 group limit")
    clear_toast(page)

    before = len(writes)
    for value, reason in (("0", "Value must be greater than or equal to 1."),
                          ("", "Please fill out this field.")):
        page.locator("#group-data-limit-amount").fill(value)
        save(page, button)
        expect(field_error(page, "group-data-limit-amount")).to_contain_text(reason)
    assert writes[before:] == [], writes[before:]
    page.locator("#group-data-limit-amount").fill("2")
    check_reset_validation(page, "group-data-limit", button, writes)
    check_hidden_interval(page, "group-data-limit", button, "/api/group-data-limits")
    handler = fail_posts(page, "**/api/group-data-limits", "invalid group data limit configuration: "
                         "device group does not exist", 400)
    save(page, button)
    expect(page.locator("#group-data-limit-form .form-error")).to_have_text(
        "invalid group data limit configuration: device group does not exist"
    )
    expect(page.locator("#group-data-limit-amount")).to_have_value("2")
    expect(page.locator("#draft-links")).to_contain_text("Review Shared group data limit")
    page.unroute("**/api/group-data-limits", handler)
    page.locator("#group-data-limit-amount").focus()
    with page.expect_request(posted("/api/group-data-limits")):
        page.keyboard.press("Enter")
    expect(page.locator("#group-data-limit-form .form-error")).to_have_count(0)
    clear_toast(page)

    row_button(page, "group-data-limit-list", "Reset usage").click()
    expect(page.locator("#message")).to_have_text("Quota usage reset.")
    clear_toast(page)
    # The row stays while the other lists are read again: the service and time lists below it
    # must follow what the Pi has saved.
    post(page, base, "/api/service-quotas", {
        "id": "alice-icloud", "name": "iCloud", "device_id": DEVICE, "profile_id": "slow",
        "limit_bytes": 5 * MEGABYTE, "domains": ["gateway.icloud.com"],
    })
    page.evaluate("loadQuotas()")
    expect(page.locator("#service-quota-count")).to_have_text("1 service limit")
    expect(quota_rows(page, "service-quota-list")).to_have_count(1)
    expect(quota_rows(page, "group-data-limit-list")).to_have_count(1)
    post(page, base, "/api/service-quotas/delete", {"id": "alice-icloud"})
    page.evaluate("loadQuotas()")
    expect(page.locator("#service-quota-count")).to_have_text("0 service limits")

    delete = row_button(page, "group-data-limit-list", "Delete")
    delete.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#message")).to_have_text("Shared limit deleted.")
    expect(page.locator("#group-data-limit-list")).to_have_text("No shared group limits saved.")
    expect(page.locator("#group-data-limit-count")).to_have_text("0 group limits")
    assert get(page, base, "/api/group-data-limits")["group_data_limits"] == []
    no_overflow(page, width)
    clear_toast(page)


def pick_domains(page, prefix, domains):
    # A save reads the page again and fills the list of website names without a choice. A choice
    # made before that read finishes is put back from the draft only a moment later.
    page.wait_for_function("() => !dashboardRefresh")
    page.locator(f"#{prefix}-domains").select_option(domains)


def check_service_quota(page, base, width):
    writes = record_writes(page)
    open_card(page, "Service data limit")
    button = "Save service limit"
    expect(page.locator("#service-quota-count")).to_have_text("0 service limits")
    expect(page.locator("#service-quota-list")).to_have_text("No service limits saved.")
    expect(page.locator("#service-quota-domains option")).to_have_text(
        ["10000.mt.eu.iot.dreame.tech", "gateway.icloud.com"]
    )
    check_reset_fields(page, "service-quota")

    page.locator("#service-quota-id").fill("alice-icloud")
    page.locator("#service-quota-name").fill("iCloud")
    page.locator("#service-quota-profile").select_option("slow")
    page.locator("#service-quota-amount").fill("300")
    page.locator("#service-quota-weight").fill("1.1")
    pick_domains(page, "service-quota", ["gateway.icloud.com"])
    with page.expect_request(posted("/api/service-quotas")) as sent:
        save(page, button)
    assert sent.value.post_data_json == {
        "id": "alice-icloud", "name": "iCloud", "device_id": DEVICE, "profile_id": "slow",
        "domains": ["gateway.icloud.com"], "limit_bytes": 300 * MEGABYTE,
        "weight_percent": 110, "enabled": True, "reset_mode": "manual",
        "reset_interval_seconds": 0, "reset_times": [],
    }, sent.value.post_data_json
    expect(page.locator("#message")).to_have_text("Service limit saved and applied.")
    expect(page.locator("#service-quota-count")).to_have_text("1 service limit")
    row = quota_rows(page, "service-quota-list")
    expect(row).to_have_count(1)
    expect(row.locator("strong")).to_have_text(f"iCloud · {DEVICE_TITLE}")
    expect(row).to_contain_text("manual reset")
    expect(row).to_contain_text("0 bytes used of 300.0 MB · 300.0 MB left · weight 1.1×")
    expect(row).to_contain_text("Domains: gateway.icloud.com")
    assert rows_inside(page.locator("#service-quota-list"))
    no_overflow(page, width)
    reload_signed_in(page)
    open_page(page, "schedules")
    expect(quota_rows(page, "service-quota-list")).to_contain_text("weight 1.1×")
    page.locator("#service-quota-form").screenshot(path=f"test-results/service-quota-form-{width}.png")

    # Edit fills the form, including the weight and the chosen website names.
    row_button(page, "service-quota-list", "Edit").click()
    expect(page.locator("#service-quota-id")).to_have_value("alice-icloud")
    expect(page.locator("#service-quota-name")).to_have_value("iCloud")
    expect(page.locator("#service-quota-device")).to_have_value(DEVICE)
    expect(page.locator("#service-quota-profile")).to_have_value("slow")
    expect(page.locator("#service-quota-amount")).to_have_value("300")
    expect(page.locator("#service-quota-weight")).to_have_value("1.1")
    assert page.evaluate(
        "Array.from(document.getElementById('service-quota-domains').selectedOptions)"
        ".map(option => option.value)"
    ) == ["gateway.icloud.com"]
    pick_domains(page, "service-quota", ["gateway.icloud.com", "10000.mt.eu.iot.dreame.tech"])
    page.locator("#service-quota-weight").fill("10")
    page.locator("#service-quota-amount").fill("2")
    page.locator("#service-quota-unit").select_option(str(GIGABYTE))
    page.locator("#service-quota-reset-mode").select_option("daily")
    page.locator("#service-quota-times").fill("23:59")
    save(page, button)
    expect(page.locator("#message")).to_have_text("Service limit saved and applied.")
    expect(quota_rows(page, "service-quota-list")).to_have_count(1)
    expect(quota_rows(page, "service-quota-list")).to_contain_text("resets daily at 23:59")
    expect(quota_rows(page, "service-quota-list")).to_contain_text("weight 10.0×")
    saved = get(page, base, "/api/service-quotas")["service_quotas"][0]
    assert saved["weight_percent"] == 1000 and saved["limit_bytes"] == 2 * GIGABYTE, saved
    assert saved["domains"] == ["10000.mt.eu.iot.dreame.tech", "gateway.icloud.com"], saved
    clear_toast(page)
    # The saved form keeps its fields but the list of website names starts without a choice.
    page.locator("#service-quota-weight").fill("0.1")
    pick_domains(page, "service-quota", ["gateway.icloud.com"])
    save(page, button)
    expect(quota_rows(page, "service-quota-list")).to_contain_text("weight 0.1×")
    assert get(page, base, "/api/service-quotas")["service_quotas"][0]["weight_percent"] == 10
    clear_toast(page)

    # Refused input shows its reason and sends nothing.
    before = len(writes)
    # The browser writes its decimal sign by the locale of the machine, so accept both.
    for value, reason in (
        ("0.05", re.compile(r"Value must be greater than or equal to 0[.,]1\.")),
        ("10.1", "Value must be less than or equal to 10."),
        ("1.25", "Please enter a valid value."),
        ("", "Please fill out this field."),
    ):
        page.locator("#service-quota-weight").fill(value)
        save(page, button)
        expect(field_error(page, "service-quota-weight")).to_contain_text(reason)
    page.locator("#service-quota-weight").fill("1")
    page.locator("#service-quota-name").fill("")
    save(page, button)
    expect(field_error(page, "service-quota-name")).to_have_text("Please fill out this field.")
    page.locator("#service-quota-name").fill("iCloud")
    page.locator("#service-quota-id").fill("Bad ID")
    save(page, button)
    expect(field_error(page, "service-quota-id")).to_contain_text("match the requested format")
    page.locator("#service-quota-id").fill("alice-icloud")
    # No website name chosen.
    pick_domains(page, "service-quota", [])
    save(page, button)
    expect(field_error(page, "service-quota-domains")).to_contain_text("Please select an item")
    assert writes[before:] == [], writes[before:]
    pick_domains(page, "service-quota", ["gateway.icloud.com"])
    expect(field_error(page, "service-quota-domains")).to_have_count(0)
    check_reset_validation(page, "service-quota", button, writes)
    check_hidden_interval(page, "service-quota", button, "/api/service-quotas")

    # A device with nothing observed has no names to choose.
    page.locator("#service-quota-device").select_option("bob")
    option = page.locator("#service-quota-domains option")
    expect(option).to_have_count(1)
    expect(option).to_have_text("No observed domains for this device")
    assert option.evaluate("node => node.disabled")
    before = len(writes)
    save(page, button)
    expect(field_error(page, "service-quota-domains")).to_contain_text("Please select an item")
    assert writes[before:] == [], writes[before:]
    page.locator("#service-quota-device").select_option(DEVICE)
    pick_domains(page, "service-quota", ["gateway.icloud.com"])

    # A refusal from the Pi keeps what was typed.
    handler = fail_posts(page, "**/api/service-quotas", "invalid service quota configuration: "
                         "service quota domains must come from DNS observations", 400)
    save(page, button)
    expect(page.locator("#service-quota-form .form-error")).to_have_text(
        "invalid service quota configuration: service quota domains must come from DNS observations"
    )
    expect(page.locator("#service-quota-id")).to_have_value("alice-icloud")
    expect(page.locator("#service-quota-weight")).to_have_value("1")
    expect(page.locator("#draft-links")).to_contain_text("Review Service data limit")
    page.unroute("**/api/service-quotas", handler)

    # The keyboard: Enter in the name saves, then Reset usage and Delete from the row.
    page.locator("#service-quota-name").focus()
    with page.expect_request(posted("/api/service-quotas")) as sent:
        page.keyboard.press("Enter")
    assert sent.value.post_data_json["weight_percent"] == 100
    expect(page.locator("#service-quota-form .form-error")).to_have_count(0)
    clear_toast(page)
    reset = row_button(page, "service-quota-list", "Reset usage")
    reset.focus()
    with page.expect_request(posted("/api/service-quotas/reset")) as sent:
        page.keyboard.press("Enter")
    assert sent.value.post_data_json == {"id": "alice-icloud"}
    expect(page.locator("#message")).to_have_text("Quota usage reset.")
    delete = row_button(page, "service-quota-list", "Delete")
    delete.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#message")).to_have_text("Service limit deleted.")
    expect(page.locator("#service-quota-list")).to_have_text("No service limits saved.")
    expect(page.locator("#service-quota-count")).to_have_text("0 service limits")
    assert get(page, base, "/api/service-quotas")["service_quotas"] == []
    no_overflow(page, width)
    clear_toast(page)


def check_time_quota(page, base, width):
    writes = record_writes(page)
    open_card(page, "Service time limit")
    button = "Save time limit"
    expect(page.locator("#time-quota-count")).to_have_text("0 time limits")
    expect(page.locator("#time-quota-list")).to_have_text("No time limits saved.")
    expect(page.locator("#time-quota-amount")).to_have_value("5")
    expect(page.locator("#time-quota-window")).to_have_value("720")
    check_reset_fields(page, "time-quota")

    page.locator("#time-quota-id").fill("alice-icloud-time")
    page.locator("#time-quota-name").fill("iCloud time")
    page.locator("#time-quota-profile").select_option("slow")
    pick_domains(page, "time-quota", ["gateway.icloud.com"])
    with page.expect_request(posted("/api/time-quotas")) as sent:
        save(page, button)
    assert sent.value.post_data_json == {
        "id": "alice-icloud-time", "name": "iCloud time", "device_id": DEVICE,
        "profile_id": "slow", "domains": ["gateway.icloud.com"], "limit_seconds": 5 * 3600,
        "window_duration_seconds": 720 * 60, "enabled": True, "reset_mode": "manual",
        "reset_interval_seconds": 0, "reset_times": [],
    }, sent.value.post_data_json
    expect(page.locator("#message")).to_contain_text("Time limit saved")
    expect(page.locator("#time-quota-count")).to_have_text("1 time limit")
    row = quota_rows(page, "time-quota-list")
    expect(row).to_have_count(1)
    expect(row.locator("strong")).to_have_text(f"iCloud time · {DEVICE_TITLE}")
    expect(row).to_contain_text("Timer stopped")
    expect(row).to_contain_text("0 seconds used of 5 hours · timer window 12 hours · manual reset")
    expect(row).to_contain_text("Domains: gateway.icloud.com")
    assert rows_inside(page.locator("#time-quota-list"))
    no_overflow(page, width)
    reload_signed_in(page)
    open_page(page, "schedules")
    expect(quota_rows(page, "time-quota-list")).to_contain_text("Timer stopped")
    page.locator("#time-quota-form").screenshot(path=f"test-results/time-quota-form-{width}.png")

    # Start its timer from the row. The window cannot be made shorter while it runs.
    row_button(page, "time-quota-list", "Start timer").click()
    expect(page.locator("#message")).to_contain_text("Time quota timer started")
    row = quota_rows(page, "time-quota-list")
    expect(row).to_contain_text(re.compile(r"\d+ (seconds|minutes|hours) left in timer"))
    expect(row_button(page, "time-quota-list", "Stop timer")).to_be_visible()
    assert get(page, base, "/api/time-quotas")["time_quotas"][0]["running"] is True
    clear_toast(page)
    row_button(page, "time-quota-list", "Edit").click()
    expect(page.locator("#time-quota-id")).to_have_value("alice-icloud-time")
    expect(page.locator("#time-quota-amount")).to_have_value("5")
    expect(page.locator("#time-quota-unit")).to_have_value("3600")
    expect(page.locator("#time-quota-window")).to_have_value("720")
    expect(page.locator("#time-quota-device")).to_have_value(DEVICE)
    page.locator("#time-quota-window").fill("60")
    save(page, button)
    expect(page.locator("#time-quota-form .form-error")).to_contain_text(
        "stop the timer before shortening its window"
    )
    expect(page.locator("#time-quota-window")).to_have_value("60")
    expect(page.locator("#draft-links")).to_contain_text("Review Service time limit")
    assert get(page, base, "/api/time-quotas")["time_quotas"][0]["window_duration_seconds"] == 720 * 60
    # A longer window and other settings save while it runs.
    page.locator("#time-quota-window").fill("1000")
    page.locator("#time-quota-amount").fill("90")
    page.locator("#time-quota-unit").select_option("60")
    page.locator("#time-quota-reset-mode").select_option("interval")
    page.locator("#time-quota-interval").fill("120")
    save(page, button)
    expect(page.locator("#time-quota-form .form-error")).to_have_count(0)
    expect(quota_rows(page, "time-quota-list")).to_contain_text("0 seconds used of 90 minutes")
    expect(quota_rows(page, "time-quota-list")).to_contain_text("resets every 120 minutes")
    saved = get(page, base, "/api/time-quotas")["time_quotas"][0]
    assert (saved["limit_seconds"], saved["window_duration_seconds"], saved["running"]) == (
        5400, 60000, True), saved
    clear_toast(page)
    row_button(page, "time-quota-list", "Stop timer").click()
    expect(page.locator("#message")).to_contain_text("Time quota updated")
    expect(quota_rows(page, "time-quota-list")).to_contain_text("Timer stopped")
    assert get(page, base, "/api/time-quotas")["time_quotas"][0]["running"] is False
    clear_toast(page)
    # With the timer stopped the window can be shortened.
    row_button(page, "time-quota-list", "Edit").click()
    page.locator("#time-quota-window").fill("60")
    save(page, button)
    expect(quota_rows(page, "time-quota-list")).to_contain_text(re.compile("timer window 1 hour(?!s)"))
    clear_toast(page)

    # Refused input shows its reason and sends nothing.
    before = len(writes)
    for field, value, reason in (
        ("amount", "0", "Value must be greater than or equal to 1."),
        ("amount", "", "Please fill out this field."),
        ("window", "0", "Value must be greater than or equal to 1."),
        ("window", "10081", "Value must be less than or equal to 10080."),
        ("window", "1.5", "Please enter a valid value."),
    ):
        page.locator("#time-quota-amount").fill("3")
        page.locator("#time-quota-window").fill("60")
        page.locator(f"#time-quota-{field}").fill(value)
        save(page, button)
        expect(field_error(page, f"time-quota-{field}")).to_contain_text(reason)
    page.locator("#time-quota-amount").fill("3")
    page.locator("#time-quota-window").fill("60")
    pick_domains(page, "time-quota", [])
    save(page, button)
    expect(field_error(page, "time-quota-domains")).to_contain_text("Please select an item")
    assert writes[before:] == [], writes[before:]
    pick_domains(page, "time-quota", ["gateway.icloud.com"])
    check_reset_validation(page, "time-quota", button, writes)
    check_hidden_interval(page, "time-quota", button, "/api/time-quotas")

    # The Pi refuses an allowance beyond a year: the reason shows and the values stay.
    pick_domains(page, "time-quota", ["gateway.icloud.com"])
    page.locator("#time-quota-amount").fill("9999")
    page.locator("#time-quota-unit").select_option("3600")
    save(page, button)
    expect(page.locator("#time-quota-form .form-error")).to_contain_text(
        "limit_seconds must be between 60 and 31536000"
    )
    expect(page.locator("#time-quota-amount")).to_have_value("9999")
    expect(page.locator("#draft-links")).to_contain_text("Review Service time limit")
    expect(page.get_by_role("button", name=button, exact=True)).to_be_enabled()
    page.locator("#time-quota-amount").fill("3")
    pick_domains(page, "time-quota", ["gateway.icloud.com"])
    handler = fail_posts(page, "**/api/time-quotas", "Save unavailable")
    save(page, button)
    expect(page.locator("#time-quota-form .form-error")).to_have_text("Save unavailable")
    expect(page.locator("#time-quota-amount")).to_have_value("3")
    page.unroute("**/api/time-quotas", handler)

    # The keyboard: Enter in the name saves, then Start, Reset usage and Delete from the row.
    page.locator("#time-quota-name").focus()
    with page.expect_request(posted("/api/time-quotas")) as sent:
        page.keyboard.press("Enter")
    assert sent.value.post_data_json["limit_seconds"] == 3 * 3600
    expect(page.locator("#time-quota-form .form-error")).to_have_count(0)
    clear_toast(page)
    start = row_button(page, "time-quota-list", "Start timer")
    start.focus()
    with page.expect_request(posted("/api/time-quotas/start")) as sent:
        page.keyboard.press("Space")
    assert sent.value.post_data_json == {"id": "alice-icloud-time"}
    expect(row_button(page, "time-quota-list", "Stop timer")).to_be_visible()
    reset = row_button(page, "time-quota-list", "Reset usage")
    reset.focus()
    with page.expect_request(posted("/api/time-quotas/reset")) as sent:
        page.keyboard.press("Enter")
    assert sent.value.post_data_json == {"id": "alice-icloud-time"}
    expect(page.locator("#message")).to_have_text("Time quota usage reset.")
    delete = row_button(page, "time-quota-list", "Delete")
    delete.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#message")).to_have_text("Time limit deleted.")
    expect(page.locator("#time-quota-list")).to_have_text("No time limits saved.")
    expect(page.locator("#time-quota-count")).to_have_text("0 time limits")
    assert get(page, base, "/api/time-quotas")["time_quotas"] == []
    no_overflow(page, width)
    clear_toast(page)


def check(page, base, width):
    unlock(page, base)
    post(page, base, "/api/devices", {
        "id": "bob", "name": "Bob tablet", "ipv4_address": "192.168.50.77", "profile_id": "normal",
    })
    post(page, base, "/api/groups", {"id": "family", "name": "Family", "device_ids": [DEVICE, "bob"]})
    post(page, base, "/api/devices", {
        "id": "carol", "name": "Carol phone", "ipv4_address": "192.168.50.78", "profile_id": "normal",
    })
    post(page, base, "/api/groups", {"id": "solo", "name": "Solo", "device_ids": ["carol"]})
    page.evaluate("refresh()")
    open_page(page, "schedules")
    expect(page.locator("#data-limit-device option")).to_have_count(3)
    check_data_limit(page, base, width)
    check_group_data_limit(page, base, width)
    check_service_quota(page, base, width)
    check_time_quota(page, base, width)


def run():
    run_journey("Data limit, group limit, service limit and time limit forms", check)


if __name__ == "__main__":
    run()
