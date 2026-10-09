"""Drive the schedule, timer and progression forms through the served dashboard.

Every form is filled with valid values and saved, the page is reloaded and the saved row is read
back, then the row is edited and deleted. Refused input shows its reason next to the form and
sends nothing, a refusal from the Pi keeps what was typed, and the keyboard reaches everything.
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

ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
DEFAULT_DAYS = ["true", "true", "true", "true", "true", "false", "false"]


def pressed(page):
    return page.evaluate(
        "Array.from(document.querySelectorAll('.day-toggle')).map(b => b.getAttribute('aria-pressed'))"
    )


def saved_schedules(page, base):
    return {item["id"]: item for item in get(page, base, "/api/schedules")["schedules"]}


def schedule_row(page, schedule_id):
    return page.locator(
        "#schedule-list .schedule-row", has_text=re.compile(f"· {re.escape(schedule_id)} ·")
    )


def fill_schedule(page, schedule_id, *, start="09:00", end="17:00", target=None, profile="slow"):
    page.locator("#schedule-id").fill(schedule_id)
    if target is not None:
        page.locator("#schedule-device").select_option(target)
    page.locator("#schedule-profile").select_option(profile)
    page.locator("#schedule-start").fill(start)
    page.locator("#schedule-end").fill(end)


def save_schedule(page):
    page.get_by_role("button", name="Save schedule", exact=True).click()


def check_schedule_form(page, base, width):
    writes = record_writes(page)
    submit = page.get_by_role("button", name="Save schedule", exact=True)
    post(page, base, "/api/devices", {
        "id": "bob", "name": "Bob tablet", "ipv4_address": "192.168.50.77", "profile_id": "normal",
    })
    post(page, base, "/api/groups", {"id": "family", "name": "Family", "device_ids": [DEVICE, "bob"]})
    page.evaluate("refresh()")
    open_page(page, "schedules")
    expect(page.locator("#schedule-device option")).to_have_count(4)
    # What the empty form starts with.
    expect(page.locator("#schedule-list")).to_have_text("No schedules yet.")
    assert pressed(page) == DEFAULT_DAYS
    expect(page.locator("#schedule-start")).to_have_value("09:00")
    expect(page.locator("#schedule-end")).to_have_value("17:00")
    expect(page.locator("#schedule-enabled")).to_be_checked()
    expect(page.locator("#schedule-clock")).to_contain_text("Schedule times use the Pi clock:")
    no_overflow(page, width)

    # A valid schedule is saved, listed and still there after a reload.
    fill_schedule(page, "work", target=f"device:{DEVICE}")
    with page.expect_request(
        lambda sent: sent.method == "POST" and sent.url.endswith("/api/schedules")
    ) as sent:
        save_schedule(page)
    assert sent.value.post_data_json == {
        "id": "work", "device_id": DEVICE, "group_id": None, "everyone_else": False,
        "profile_id": "slow", "days_of_week": [0, 1, 2, 3, 4], "start_minute": 540,
        "end_minute": 1020, "enabled": True,
    }, sent.value.post_data_json
    expect(page.locator("#message")).to_contain_text("Schedule saved. It follows the Pi clock")
    row = schedule_row(page, "work")
    expect(row).to_contain_text(f"{DEVICE_TITLE} → Slow connection")
    expect(row).to_contain_text("Mon–Fri · 09:00–17:00 · work · Enabled")
    assert saved_schedules(page, base)["work"]["start_minute"] == 540
    reload_signed_in(page)
    open_page(page, "schedules")
    row = schedule_row(page, "work")
    expect(row).to_contain_text("Mon–Fri · 09:00–17:00 · work · Enabled")
    expect(page.locator("#schedule-clock")).to_contain_text("(")
    assert rows_inside(page.locator("#schedule-list"))
    no_overflow(page, width)
    page.locator("#schedule-form").screenshot(path=f"test-results/schedule-form-{width}.png")

    # Edit fills the form from the row. Saving the same id does not count as an overlap with
    # itself and changes the saved schedule in place.
    row.get_by_role("button", name="Edit", exact=True).click()
    expect(page.locator("#schedule-id")).to_have_value("work")
    expect(page.locator("#schedule-device")).to_have_value(f"device:{DEVICE}")
    expect(page.locator("#schedule-profile")).to_have_value("slow")
    expect(page.locator("#schedule-start")).to_have_value("09:00")
    expect(page.locator("#schedule-end")).to_have_value("17:00")
    assert pressed(page) == DEFAULT_DAYS
    page.locator("#schedule-end").fill("18:30")
    page.locator('.day-toggle[data-day="4"]').click()
    expect(page.locator('.day-toggle[data-day="4"]')).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#draft-links")).to_contain_text("Review Scheduled profile")
    save_schedule(page)
    expect(page.locator("#message")).to_contain_text("Schedule saved.")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(schedule_row(page, "work")).to_contain_text("Mon–Thu · 09:00–18:30 · work · Enabled")
    expect(page.locator("#schedule-list .schedule-row")).to_have_count(1)
    assert saved_schedules(page, base)["work"]["days_of_week"] == [0, 1, 2, 3]
    expect(page.locator("#draft-notice")).to_be_hidden()

    # A schedule that overlaps another one for the same target asks first. Cancel saves nothing.
    clear_toast(page)
    fill_schedule(page, "work-late", start="18:00", end="20:00", target=f"device:{DEVICE}")
    before = len(writes)
    save_schedule(page)
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("Overlapping schedules: work.")
    expect(dialog).to_contain_text("the active schedule with the first ID alphabetically wins")
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(dialog).to_have_count(0)
    expect(submit).to_be_enabled()
    assert not [write for write in writes[before:] if write == "POST schedules"], writes
    assert "work-late" not in saved_schedules(page, base)
    expect(page.locator("#schedule-id")).to_have_value("work-late")
    save_schedule(page)
    expect(dialog).to_contain_text("Overlapping schedules: work.")
    dialog.get_by_role("button", name="Continue", exact=True).click()
    expect(schedule_row(page, "work-late")).to_contain_text("Mon–Thu · 18:00–20:00")
    assert "work-late" in saved_schedules(page, base)
    clear_toast(page)

    # Windows that only touch do not overlap, and neither do other targets.
    fill_schedule(page, "touching", start="20:00", end="21:00", target=f"device:{DEVICE}")
    save_schedule(page)
    expect(schedule_row(page, "touching")).to_be_visible()
    expect(dialog).to_have_count(0)
    fill_schedule(page, "bob-work", target="device:bob")
    save_schedule(page)
    expect(schedule_row(page, "bob-work")).to_contain_text("Bob tablet → Slow connection")
    expect(dialog).to_have_count(0)

    # An overnight window for a group, and one for everyone else, with a disabled one in between.
    fill_schedule(page, "night", start="22:00", end="06:00", target="group:family",
                  profile="normal")
    save_schedule(page)
    expect(schedule_row(page, "night")).to_contain_text("Group Family → Normal connection")
    expect(schedule_row(page, "night")).to_contain_text("Mon–Thu · 22:00–06:00")
    fill_schedule(page, "others", start="00:00", end="06:00", target="everyone-else")
    page.locator("#schedule-enabled").uncheck()
    save_schedule(page)
    expect(page.locator("#message")).to_contain_text("Schedule saved as disabled.")
    expect(schedule_row(page, "others")).to_contain_text("Everyone else → Slow connection")
    expect(schedule_row(page, "others")).to_contain_text("· others · Disabled")
    saved = saved_schedules(page, base)
    assert saved["others"]["everyone_else"] is True and saved["others"]["enabled"] is False
    assert saved["night"]["group_id"] == "family" and saved["night"]["start_minute"] == 1320
    # A disabled schedule is not an overlap either.
    page.locator("#schedule-id").fill("others-too")
    page.locator("#schedule-enabled").check()
    save_schedule(page)
    expect(schedule_row(page, "others-too")).to_be_visible()
    expect(dialog).to_have_count(0)
    clear_toast(page)

    # Refused input shows its reason beside the field and sends nothing.
    page.locator("#schedule-form button.day-toggle").first.scroll_into_view_if_needed()
    page.locator("#schedule-id").fill("")
    before = len(writes)
    save_schedule(page)
    expect(field_error(page, "schedule-id")).to_have_text("Please fill out this field.")
    expect(page.locator("#schedule-id")).to_have_attribute("aria-invalid", "true")
    page.locator("#schedule-id").fill("Bad ID!")
    save_schedule(page)
    expect(field_error(page, "schedule-id")).to_contain_text("match the requested format")
    page.locator("#schedule-id").fill("fine-id")
    expect(field_error(page, "schedule-id")).to_have_count(0)
    page.locator("#schedule-start").fill("")
    save_schedule(page)
    expect(field_error(page, "schedule-start")).to_have_text("Please fill out this field.")
    page.locator("#schedule-start").fill("08:00")
    assert writes[before:] == [], writes[before:]
    # No day chosen is refused by the page itself.
    for day in range(7):
        toggle = page.locator(f'.day-toggle[data-day="{day}"]')
        if toggle.get_attribute("aria-pressed") == "true":
            toggle.click()
    save_schedule(page)
    expect(page.locator("#message")).to_have_text("Choose at least one day for the schedule.")
    expect(submit).to_be_enabled()
    assert writes[before:] == [], writes[before:]
    clear_toast(page)

    # The Pi refuses a window that starts and ends at the same minute. The reason shows in the
    # form and everything typed stays for a correction.
    page.locator('.day-toggle[data-day="5"]').click()
    page.locator("#schedule-end").fill("08:00")
    save_schedule(page)
    expect(page.locator("#schedule-form .form-error")).to_contain_text(
        "schedule start and end must be different"
    )
    expect(page.locator("#schedule-form .form-error")).to_have_attribute("role", "alert")
    expect(submit).to_be_enabled()
    expect(page.locator("#schedule-id")).to_have_value("fine-id")
    expect(page.locator("#schedule-start")).to_have_value("08:00")
    assert pressed(page)[5] == "true"
    assert "fine-id" not in saved_schedules(page, base)
    expect(page.locator("#draft-links")).to_contain_text("Review Scheduled profile")
    clear_toast(page)

    # A refusal while saving keeps the draft. The same form saves once the Pi answers again.
    page.locator("#schedule-end").fill("09:30")
    handler = fail_posts(page, "**/api/schedules", "Save unavailable")
    save_schedule(page)
    expect(page.locator("#schedule-form .form-error")).to_contain_text("Save unavailable")
    expect(page.locator("#schedule-id")).to_have_value("fine-id")
    expect(page.locator("#schedule-end")).to_have_value("09:30")
    expect(page.locator("#draft-links")).to_contain_text("Review Scheduled profile")
    expect(submit).to_be_enabled()
    assert "fine-id" not in saved_schedules(page, base)
    page.unroute("**/api/schedules", handler)
    save_schedule(page)
    expect(schedule_row(page, "fine-id")).to_contain_text("Sat · 08:00–09:30")
    expect(page.locator("#schedule-form .form-error")).to_have_count(0)
    expect(page.locator("#draft-notice")).to_be_hidden()
    clear_toast(page)

    # The keyboard fills, toggles days and saves. Space presses a day, Tab moves on to the next
    # one and Enter in a field saves the form.
    page.locator("#schedule-id").fill("keys")
    first_day = page.locator('.day-toggle[data-day="0"]')
    first_day.focus()
    expect(first_day).to_be_focused()
    before_days = pressed(page)
    page.keyboard.press("Space")
    expect(first_day).to_have_attribute("aria-pressed", "true" if before_days[0] == "false" else "false")
    page.keyboard.press("Tab")
    expect(page.locator('.day-toggle[data-day="1"]')).to_be_focused()
    page.keyboard.press("Space")
    expect(page.locator('.day-toggle[data-day="1"]')).to_have_attribute(
        "aria-pressed", "true" if before_days[1] == "false" else "false"
    )
    # A day that was not on yet becomes the only day the keyboard needs for a valid schedule.
    page.locator("#schedule-start").fill("11:00")
    page.locator("#schedule-end").fill("12:00")
    page.locator("#schedule-id").focus()
    with page.expect_request(
        lambda sent: sent.method == "POST" and sent.url.endswith("/api/schedules")
    ):
        page.keyboard.press("Enter")
    expect(schedule_row(page, "keys")).to_be_visible()
    assert saved_schedules(page, base)["keys"]["days_of_week"] == [
        index for index, state in enumerate(pressed(page)) if state == "true"
    ]
    clear_toast(page)

    # Delete takes the row away at once and from the Pi.
    row = schedule_row(page, "keys")
    row.get_by_role("button", name="Delete", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.locator("#message")).to_contain_text("Schedule deleted.")
    expect(schedule_row(page, "keys")).to_have_count(0)
    assert "keys" not in saved_schedules(page, base)
    for remaining in ("work", "work-late", "touching", "bob-work", "night", "others", "others-too",
                      "fine-id"):
        schedule_row(page, remaining).get_by_role("button", name="Delete", exact=True).click()
        expect(schedule_row(page, remaining)).to_have_count(0)
    expect(page.locator("#schedule-list")).to_have_text("No schedules yet.")
    assert saved_schedules(page, base) == {}
    assert rows_inside(page.locator("#schedule-list"))
    no_overflow(page, width)

    # Discard unfinished edits puts the days back with the other fields.
    discard_all_drafts(page) if page.locator("#draft-notice").is_visible() else None
    for day, state in enumerate(DEFAULT_DAYS):
        toggle = page.locator(f'.day-toggle[data-day="{day}"]')
        if toggle.get_attribute("aria-pressed") != state:
            toggle.click()
    page.locator("#schedule-id").fill("unfinished")
    page.locator('.day-toggle[data-day="5"]').click()
    page.locator('.day-toggle[data-day="0"]').click()
    assert pressed(page) == ["false", "true", "true", "true", "true", "true", "false"]
    discard_all_drafts(page)
    expect(page.locator("#schedule-id")).to_have_value("")
    assert pressed(page) == DEFAULT_DAYS, pressed(page)
    page.evaluate("refresh()")
    clear_toast(page)


def check_timer_form(page, base, width):
    writes = record_writes(page)
    card = open_card(page, "Run a profile timer")
    submit = page.get_by_role("button", name="Start timer", exact=True)
    expect(page.locator("#timer-count")).to_have_text("0 active timers")
    expect(page.locator("#timer-list")).to_have_text("No active timers.")
    expect(page.locator("#timer-duration")).to_have_value("10")
    expect(page.locator("#timer-device option")).to_have_count(2)
    expect(page.locator("#timer-profile option")).to_have_count(2)

    # A valid timer starts at once, shows its remaining time and survives a reload.
    page.locator("#timer-device").select_option(DEVICE)
    page.locator("#timer-profile").select_option("slow")
    page.locator("#timer-duration").fill("15")
    with page.expect_request(
        lambda sent: sent.method == "POST" and sent.url.endswith("/api/timers")
    ) as sent:
        submit.click()
    assert sent.value.post_data_json == {
        "device_id": DEVICE, "profile_id": "slow", "duration_minutes": 15,
    }, sent.value.post_data_json
    expect(page.locator("#message")).to_have_text("Timer started and the profile is active now.")
    expect(page.locator("#timer-count")).to_have_text("1 active timer")
    row = page.locator("#timer-list .timer-row")
    expect(row).to_have_count(1)
    expect(row).to_contain_text(f"{DEVICE_TITLE} → Slow connection")
    expect(row).to_contain_text(re.compile(r"(15 minutes|14m \d+s) remaining"))
    timers = get(page, base, "/api/timers")["timers"]
    assert [(t["device_id"], t["profile_id"]) for t in timers] == [(DEVICE, "slow")]
    assert 14 * 60 <= timers[0]["remaining_seconds"] <= 15 * 60
    assert rows_inside(page.locator("#timer-list"))
    no_overflow(page, width)
    reload_signed_in(page)
    open_page(page, "schedules")
    open_card(page, "Run a profile timer")
    expect(page.locator("#timer-list .timer-row")).to_have_count(1)
    expect(page.locator("#timer-count")).to_have_text("1 active timer")
    page.locator("#timer-form").screenshot(path=f"test-results/timer-form-{width}.png")

    # Cancel stops it and the device returns to its own profile.
    page.locator("#timer-list").get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#message")).to_have_text("Timer cancelled and the normal profile is active.")
    expect(page.locator("#timer-list")).to_have_text("No active timers.")
    expect(page.locator("#timer-count")).to_have_text("0 active timers")
    assert get(page, base, "/api/timers")["timers"] == []
    clear_toast(page)

    # The edges of the duration are accepted: one minute and one week.
    page.locator("#timer-device").select_option(DEVICE)
    page.locator("#timer-profile").select_option("slow")
    for minutes in (1, 10080):
        page.locator("#timer-duration").fill(str(minutes))
        submit.click()
        expect(page.locator("#timer-list .timer-row")).to_have_count(1)
        assert get(page, base, "/api/timers")["timers"][0]["remaining_seconds"] in range(
            minutes * 60 - 5, minutes * 60 + 1
        )
        page.locator("#timer-list").get_by_role("button", name="Cancel", exact=True).click()
        expect(page.locator("#timer-list")).to_have_text("No active timers.")
    clear_toast(page)

    # Out of range or incomplete input is refused beside the field and sends nothing.
    before = len(writes)
    for value, reason in (
        ("0", "Value must be greater than or equal to 1."),
        ("10081", "Value must be less than or equal to 10080."),
        ("", "Please fill out this field."),
        ("1.5", "Please enter a valid value."),
    ):
        page.locator("#timer-duration").fill(value)
        submit.click()
        expect(field_error(page, "timer-duration")).to_contain_text(reason)
        expect(page.locator("#timer-duration")).to_have_attribute("aria-invalid", "true")
    assert writes[before:] == [], writes[before:]
    page.locator("#timer-duration").fill("20")
    expect(field_error(page, "timer-duration")).to_have_count(0)

    # The Pi refuses the timer: the reason shows in the form and the typed values stay.
    handler = fail_posts(page, "**/api/timers", "invalid timer configuration: device does not exist", 400)
    submit.click()
    expect(page.locator("#timer-form .form-error")).to_have_text(
        "invalid timer configuration: device does not exist"
    )
    expect(page.locator("#timer-duration")).to_have_value("20")
    expect(page.locator("#timer-profile")).to_have_value("slow")
    expect(page.locator("#draft-links")).to_contain_text("Review Profile timer")
    expect(submit).to_be_enabled()
    assert get(page, base, "/api/timers")["timers"] == []
    page.unroute("**/api/timers", handler)

    # The keyboard: focus the duration, type and press Enter.
    page.locator("#timer-duration").focus()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.type("25")
    with page.expect_request(
        lambda sent: sent.method == "POST" and sent.url.endswith("/api/timers")
    ) as sent:
        page.keyboard.press("Enter")
    assert sent.value.post_data_json["duration_minutes"] == 25
    expect(page.locator("#timer-list .timer-row")).to_have_count(1)
    expect(page.locator("#timer-form .form-error")).to_have_count(0)
    expect(page.locator("#draft-notice")).to_be_hidden()
    cancel = page.locator("#timer-list").get_by_role("button", name="Cancel", exact=True)
    cancel.focus()
    page.keyboard.press("Space")
    expect(page.locator("#timer-list")).to_have_text("No active timers.")
    assert card.get_attribute("aria-expanded") == "true"
    no_overflow(page, width)
    clear_toast(page)


def check_progression_form(page, base, width):
    writes = record_writes(page)
    open_card(page, "Progression mode")
    submit = page.get_by_role("button", name="Start progression", exact=True)
    mode = page.locator("#progression-mode")
    expect(page.locator("#progression-count")).to_have_text("0 active progressions")
    expect(page.locator("#progression-list")).to_have_text("No active progressions.")
    expect(page.locator("#progression-duration")).to_have_value("300")
    # Each mode shows only its own target.
    for value, visible in (("time", ()), ("traffic", ("#progression-traffic-target",)),
                           ("bytes", ("#progression-bytes-target",))):
        mode.select_option(value)
        for field in ("#progression-traffic-target", "#progression-bytes-target"):
            if field in visible:
                expect(page.locator(field)).to_be_visible()
            else:
                expect(page.locator(field)).to_be_hidden()
    mode.select_option("time")

    page.locator("#progression-device").select_option(DEVICE)
    page.locator("#progression-start-profile").select_option("normal")
    page.locator("#progression-end-profile").select_option("slow")
    page.locator("#progression-duration").fill("30")
    with page.expect_request(
        lambda sent: sent.method == "POST" and sent.url.endswith("/api/progressions")
    ) as sent:
        submit.click()
    assert sent.value.post_data_json == {
        "device_id": DEVICE, "start_profile_id": "normal", "end_profile_id": "slow",
        "mode": "time", "duration_minutes": 30, "traffic_target_queries": 0,
        "traffic_target_bytes": 0, "website_domains": [],
    }, sent.value.post_data_json
    expect(page.locator("#message")).to_have_text("Progression started and is active now.")
    expect(page.locator("#progression-count")).to_have_text("1 active progression")
    row = page.locator("#progression-list .progression-row")
    expect(row).to_have_count(1)
    expect(row).to_contain_text(f"{DEVICE_TITLE} · Normal connection → Slow connection")
    expect(row).to_contain_text(re.compile(r"\d+% · time based · (29m \d+s|30 minutes) left"))
    assert rows_inside(page.locator("#progression-list"))
    no_overflow(page, width)
    reload_signed_in(page)
    open_page(page, "schedules")
    open_card(page, "Progression mode")
    expect(page.locator("#progression-list .progression-row")).to_have_count(1)
    page.locator("#progression-form").screenshot(path=f"test-results/progression-form-{width}.png")
    page.locator("#progression-list").get_by_role(
        "button", name="Stop and recover", exact=True
    ).click()
    expect(page.locator("#message")).to_have_text(
        "Progression stopped and the normal profile is active."
    )
    expect(page.locator("#progression-list")).to_have_text("No active progressions.")
    assert get(page, base, "/api/progressions")["progressions"] == []
    clear_toast(page)

    # The device-data mode converts megabytes and the DNS mode sends its query count.
    page.locator("#progression-device").select_option(DEVICE)
    page.locator("#progression-start-profile").select_option("normal")
    page.locator("#progression-end-profile").select_option("slow")
    mode.select_option("bytes")
    page.locator("#progression-bytes-target").fill("50")
    page.locator("#progression-domains").fill("youtube.com, example.com")
    with page.expect_request(
        lambda sent: sent.method == "POST" and sent.url.endswith("/api/progressions")
    ) as sent:
        submit.click()
    body = sent.value.post_data_json
    assert body["mode"] == "bytes" and body["traffic_target_bytes"] == 50 * 1048576, body
    assert body["traffic_target_queries"] == 0
    assert body["website_domains"] == ["youtube.com", "example.com"], body
    expect(page.locator("#progression-list .progression-row")).to_contain_text(
        re.compile(r"0 bytes / 50\.0 MB")
    )
    saved = get(page, base, "/api/progressions")["progressions"][0]
    assert saved["website_domains"] == ["youtube.com", "example.com"], saved
    page.locator("#progression-list").get_by_role("button", name="Stop and recover").click()
    expect(page.locator("#progression-list")).to_have_text("No active progressions.")
    mode.select_option("traffic")
    page.locator("#progression-traffic-target").fill("250")
    page.locator("#progression-domains").fill("")
    with page.expect_request(
        lambda sent: sent.method == "POST" and sent.url.endswith("/api/progressions")
    ) as sent:
        submit.click()
    body = sent.value.post_data_json
    assert body["mode"] == "traffic" and body["traffic_target_queries"] == 250, body
    assert body["traffic_target_bytes"] == 0
    expect(page.locator("#progression-list .progression-row")).to_contain_text("0 DNS queries")
    page.locator("#progression-list").get_by_role("button", name="Stop and recover").click()
    expect(page.locator("#progression-list")).to_have_text("No active progressions.")
    clear_toast(page)

    # A target that is out of range in a mode that is not chosen must not block the form. The
    # field is hidden, so a refusal there would never be seen.
    mode.select_option("traffic")
    page.locator("#progression-traffic-target").fill("0")
    mode.select_option("time")
    before = len(writes)
    with page.expect_request(
        lambda sent: sent.method == "POST" and sent.url.endswith("/api/progressions")
    ) as sent:
        submit.click()
    assert sent.value.post_data_json["mode"] == "time"
    assert writes[before:] == ["POST progressions"], writes[before:]
    expect(page.locator("#progression-list .progression-row")).to_have_count(1)
    page.locator("#progression-list").get_by_role("button", name="Stop and recover").click()
    expect(page.locator("#progression-list")).to_have_text("No active progressions.")
    clear_toast(page)

    # Out of range input in the chosen mode is refused beside the field and sends nothing.
    before = len(writes)
    for value, reason in (
        ("0", "Value must be greater than or equal to 1."),
        ("10081", "Value must be less than or equal to 10080."),
        ("", "Please fill out this field."),
    ):
        page.locator("#progression-duration").fill(value)
        submit.click()
        expect(field_error(page, "progression-duration")).to_contain_text(reason)
    page.locator("#progression-duration").fill("30")
    mode.select_option("traffic")
    page.locator("#progression-traffic-target").fill("0")
    submit.click()
    expect(field_error(page, "progression-traffic-target")).to_contain_text(
        "Value must be greater than or equal to 1."
    )
    page.locator("#progression-traffic-target").fill("")
    submit.click()
    expect(field_error(page, "progression-traffic-target")).to_have_text("Please fill out this field.")
    page.locator("#progression-traffic-target").fill("10")
    mode.select_option("bytes")
    page.locator("#progression-bytes-target").fill("10000001")
    submit.click()
    expect(field_error(page, "progression-bytes-target")).to_contain_text(
        "Value must be less than or equal to 10000000."
    )
    assert writes[before:] == [], writes[before:]
    page.locator("#progression-bytes-target").fill("5")

    # The Pi refuses a website name: the reason shows in the form and the typed values stay.
    page.locator("#progression-domains").fill("not a domain")
    submit.click()
    expect(page.locator("#progression-form .form-error")).to_contain_text(
        "invalid progression configuration"
    )
    expect(page.locator("#progression-domains")).to_have_value("not a domain")
    expect(page.locator("#progression-bytes-target")).to_have_value("5")
    expect(page.locator("#progression-end-profile")).to_have_value("slow")
    expect(page.locator("#draft-links")).to_contain_text("Review Progression mode")
    expect(submit).to_be_enabled()
    assert get(page, base, "/api/progressions")["progressions"] == []
    handler = fail_posts(page, "**/api/progressions", "Save unavailable")
    page.locator("#progression-domains").fill("example.com")
    submit.click()
    expect(page.locator("#progression-form .form-error")).to_have_text("Save unavailable")
    expect(page.locator("#progression-domains")).to_have_value("example.com")
    page.unroute("**/api/progressions", handler)

    # The keyboard: Tab goes from the menu to the duration and Enter there saves the form.
    mode.select_option("time")
    mode.focus()
    page.keyboard.press("Tab")
    expect(page.locator("#progression-duration")).to_be_focused()
    with page.expect_request(
        lambda sent: sent.method == "POST" and sent.url.endswith("/api/progressions")
    ):
        page.keyboard.press("Enter")
    expect(page.locator("#progression-list .progression-row")).to_have_count(1)
    expect(page.locator("#progression-form .form-error")).to_have_count(0)
    stop = page.locator("#progression-list").get_by_role("button", name="Stop and recover")
    stop.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#progression-list")).to_have_text("No active progressions.")
    no_overflow(page, width)
    clear_toast(page)

    # Discard unfinished edits shows only the target of the mode the form goes back to.
    mode.select_option("bytes")
    page.locator("#progression-bytes-target").fill("77")
    discard_all_drafts(page)
    expect(mode).to_have_value("time")
    expect(page.locator("#progression-bytes-target")).to_be_hidden()
    expect(page.locator("#progression-traffic-target")).to_be_hidden()


def check(page, base, width):
    unlock(page, base)
    open_page(page, "schedules")
    # The device menus follow what the Pi has saved.
    expect(page.locator("#timer-device option")).to_have_count(1)
    check_schedule_form(page, base, width)
    check_timer_form(page, base, width)
    check_progression_form(page, base, width)


def run():
    run_journey("Schedule, timer and progression forms", check)


if __name__ == "__main__":
    run()
