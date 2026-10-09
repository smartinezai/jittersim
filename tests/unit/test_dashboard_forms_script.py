"""Run the page code behind the schedule, timer, progression and limit forms in node.

The functions come from the dashboard exactly as it is served. A small stand-in for the page
supplies the elements, so no browser is needed. The browser journeys in tests/browser/
schedules_timers.py and limits_quotas.py drive the same forms in a real page.
"""

import json
import subprocess

import pytest

from tests.dashboard_script import NODE, constant_source, function_source, run_node

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required to run the dashboard")

# A page that keeps its elements in a table. Setting textContent drops what was drawn inside.
PAGE = """
class FakeNode {
  constructor(tag) {
    this.tag = tag; this.children = []; this.style = {}; this.className = "";
    this.hidden = false; this.disabled = false; this.value = ""; this.checked = false;
    this.dataset = {}; this.listeners = {}; this._text = "";
  }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  set textContent(value) { this._text = String(value); this.children = []; }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
  addEventListener(name, handler) { (this.listeners[name] = this.listeners[name] || []).push(handler); }
  click() { (this.listeners.click || []).forEach((handler) => handler()); }
  find(text) { return this.children.find((child) => child.textContent === text); }
  setAttribute(name, value) { this.attributes = Object.assign(this.attributes || {}, {[name]: value}); }
  getAttribute(name) { return (this.attributes || {})[name]; }
  scrollIntoView() {}
}
const document = { createElement: (tag) => new FakeNode(tag), querySelectorAll: () => [] };
const elements = {};
function element(id) { return elements[id] = elements[id] || Object.assign(new FakeNode(id), {id}); }
const calls = [];
function descendants(node) { return node.children.flatMap((child) => [child, ...descendants(child)]); }
"""


def async_function_source(name):
    """function_source starts at the word function, so an async function needs its keyword back."""

    return "async " + function_source(name)


def run_async(sources, expression):
    """Run the sources, wait for the promise that the expression gives and return its value."""

    program = "\n".join(sources) + "\n(" + expression + ").then((value) => console.log(JSON.stringify(value)));\n"
    result = subprocess.run([NODE, "-"], input=program, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def run(names, expression, setup="", constants=()):
    sources = [PAGE, *(constant_source(name) for name in constants),
               *(function_source(name) for name in names), setup]
    return run_node(sources, expression)


def test_quota_sizes_use_the_largest_unit_that_shows_something():
    sizes = [0, 1, 1023, 1024, 1536, 1048575, 1048576, 1572864, 1073741824, 5368709120]
    assert run(["formatQuotaBytes"], f"{sizes}.map(formatQuotaBytes)") == [
        "0 bytes", "1 bytes", "1023 bytes", "1.0 KB", "1.5 KB", "1024.0 KB", "1.0 MB", "1.5 MB",
        "1.00 GB", "5.00 GB",
    ]


def test_quota_reset_text_names_the_reset_each_limit_has():
    expression = """[
      {reset_mode: "manual"},
      {reset_mode: "interval", reset_interval_seconds: 60},
      {reset_mode: "interval", reset_interval_seconds: 5400},
      {reset_mode: "interval", reset_interval_seconds: 31536000},
      {reset_mode: "daily", reset_times: [480, 1230]},
      {reset_mode: "daily", reset_times: [0, 1439]},
      {reset_mode: "something else"}
    ].map(quotaResetText)"""
    assert run(["quotaResetText"], expression) == [
        "manual reset", "resets every 1 minute", "resets every 90 minutes",
        "resets every 525600 minutes", "resets daily at 08:00, 20:30",
        "resets daily at 00:00, 23:59", "manual reset",
    ]


def test_durations_say_one_minute_and_one_hour_in_the_singular():
    seconds = [1, 59, 60, 61, 90, 3599, 3600, 3660, 7200, 43200]
    assert run(["formatQuotaDuration"], f"{seconds}.map(formatQuotaDuration)") == [
        "1 second", "59 seconds", "1 minute", "61 seconds", "90 seconds", "3599 seconds",
        "1 hour", "61 minutes", "2 hours", "12 hours",
    ]
    assert run(["formatTimerRemaining"], f"{[0, 1, 59, 60, 61, 120, 3600, 3601]}.map(formatTimerRemaining)") == [
        "0 seconds", "1 second", "59 seconds", "1 minute", "1m 1s", "2 minutes", "60 minutes",
        "60m 1s",
    ]


def reset_fields(prefix="data-limit"):
    return f"""
      for (const suffix of ["reset-mode", "interval-field", "times-field", "interval", "times"]) {{
        element("{prefix}-" + suffix);
      }}
      function state(mode) {{
        element("{prefix}-reset-mode").value = mode;
        syncQuotaReset("{prefix}");
        return ["interval-field", "times-field", "interval", "times"].map((suffix) => {{
          const node = element("{prefix}-" + suffix);
          return [suffix, node.hidden, node.disabled];
        }});
      }}
    """


def test_each_reset_mode_shows_and_enables_only_its_own_field():
    # A hidden field that stays enabled still takes part in the form's own check. An interval of 0
    # typed before the mode was changed would then stop the save without a word.
    for prefix in ("data-limit", "group-data-limit", "service-quota", "time-quota"):
        program = [PAGE, function_source("syncQuotaReset"), reset_fields(prefix)]
        modes = run_node(
            program, '({manual: state("manual"), interval: state("interval"), daily: state("daily")})'
        )
        assert modes == {
            "manual": [["interval-field", True, False], ["times-field", True, False],
                       ["interval", False, True], ["times", False, True]],
            "interval": [["interval-field", False, False], ["times-field", True, False],
                         ["interval", False, False], ["times", False, True]],
            "daily": [["interval-field", True, False], ["times-field", False, False],
                      ["interval", False, True], ["times", False, False]],
        }, prefix


def test_each_progression_mode_shows_and_enables_only_its_own_target():
    setup = """
      const mode = element("progression-mode");
      function state(value) {
        mode.value = value;
        syncProgressionMode();
        return ["traffic", "bytes"].map((kind) => [
          element("progression-" + kind + "-field").hidden,
          element("progression-" + kind + "-target").required,
          element("progression-" + kind + "-target").disabled,
        ]);
      }
    """
    states = run_node([PAGE, function_source("syncProgressionMode"), setup],
                      '({time: state("time"), traffic: state("traffic"), bytes: state("bytes")})')
    assert states == {
        "time": [[True, False, True], [True, False, True]],
        "traffic": [[False, True, False], [True, False, True]],
        "bytes": [[True, False, True], [False, True, False]],
    }


def test_reset_times_become_minutes_of_the_day():
    setup = """
      function payload(mode, interval, times) {
        element("q-reset-mode").value = mode;
        element("q-interval").value = interval;
        element("q-times").value = times;
        try { return quotaSchedulePayload("q"); } catch (error) { return error.message; }
      }
    """
    expression = """({
      manual: payload("manual", "60", "08:00"),
      interval: payload("interval", "90", ""),
      daily: payload("daily", "", "8:00, 20:30 ,23:59,,00:00"),
      missing: payload("daily", "", " , "),
      hours: payload("daily", "", "24:00"),
      minutes: payload("daily", "", "08:60"),
      words: payload("daily", "", "noon"),
      seconds: payload("daily", "", "08:00:30"),
      bare: payload("daily", "", "8")
    })"""
    result = run(["quotaSchedulePayload"], expression, setup=setup)
    assert result["manual"] == {"reset_mode": "manual", "reset_interval_seconds": 0, "reset_times": []}
    assert result["interval"] == {
        "reset_mode": "interval", "reset_interval_seconds": 5400, "reset_times": []}
    assert result["daily"] == {
        "reset_mode": "daily", "reset_interval_seconds": 0, "reset_times": [480, 1230, 1439, 0]}
    assert result["missing"] == "Add at least one daily reset time."
    for refused in ("hours", "minutes", "words", "seconds", "bare"):
        assert result[refused] == "Reset times must use HH:MM format.", refused


def test_a_saved_limit_fills_the_amount_in_gigabytes_only_when_it_is_whole():
    setup = """
      function filled(bytes) {
        setQuotaAmount("q", bytes);
        return [element("q-unit").value, element("q-amount").value];
      }
    """
    expression = """[1073741824, 2147483648, 1610612736, 1048576, 524288000].map(filled)"""
    assert run(["setQuotaAmount"], expression, setup=setup) == [
        ["1073741824", 1], ["1073741824", 2], ["1048576", 1536], ["1048576", 1], ["1048576", 500],
    ]


def row_program():
    sources = [
        PAGE,
        function_source("formatQuotaBytes"),
        function_source("quotaResetText"),
        function_source("quotaRowProgress"),
        function_source("createQuotaRow"),
        """
        function editDataLimit(quota) { calls.push(["edit device", quota.device_id]); }
        function editGroupDataLimit(quota) { calls.push(["edit group", quota.group_id]); }
        function editServiceQuota(quota) { calls.push(["edit service", quota.id]); }
        function resetQuota(quota, service) { calls.push(["reset", service]); }
        function deleteItem(path, body, message) { calls.push(["delete", path, body, message]); }
        """,
    ]
    return sources


DEVICE_LIMIT = """{
  device_id: "alice", device_name: "Test phone", profile_name: "Slow connection",
  used_bytes: 524288000, limit_bytes: 1073741824, remaining_bytes: 549453824,
  reached: false, reset_mode: "manual"
}"""
GROUP_LIMIT = """{
  group_id: "family", group_name: "Family", profile_name: "Slow connection",
  used_bytes: 0, limit_bytes: 524288000, remaining_bytes: 524288000, reached: false,
  reset_mode: "interval", reset_interval_seconds: 5400
}"""
SERVICE_QUOTA = """{
  id: "alice-icloud", name: "iCloud", device_name: "Test phone", domains: ["gateway.icloud.com", "icloud.com"],
  used_bytes: 0, limit_bytes: 314572800, remaining_bytes: 314572800, weight_percent: 110,
  reached: true, reset_mode: "daily", reset_times: [480]
}"""


def describe(quota, service):
    return run_node(
        row_program(),
        f"""(() => {{
          const row = createQuotaRow({{quota: {quota}, service: {service}}});
          const [heading, detail, meter, actions] = row.children;
          return {{
            className: row.className,
            title: heading.children[0].textContent,
            status: heading.children[1].textContent,
            detail: detail.textContent,
            width: meter.children[0].style.width,
            buttons: actions.children.filter((node) => node.tag === "button").map((node) => node.textContent),
            domains: actions.children.filter((node) => node.tag === "span").map((node) => node.textContent),
          }};
        }})()""",
    )


def test_a_device_limit_row_names_its_device_and_fallback_profile():
    row = describe(DEVICE_LIMIT, "false")
    assert row == {
        "className": "quota-row", "title": "Test phone · Slow connection",
        "status": "manual reset",
        "detail": "500.0 MB used of 1.00 GB · 524.0 MB left",
        "width": "49%", "buttons": ["Edit", "Reset usage", "Delete"], "domains": [],
    }


def test_a_shared_group_limit_row_draws_without_a_weight_or_website_names():
    # It threw on the website names, so no group limit was ever listed and the service and time
    # limit lists below it were never drawn again.
    row = describe(GROUP_LIMIT, '"group"')
    assert row["title"] == "Family · Slow connection"
    assert row["status"] == "resets every 90 minutes"
    assert row["detail"] == "0 bytes used of 500.0 MB · 500.0 MB left"
    assert row["domains"] == [] and "NaN" not in row["detail"]
    assert row["buttons"] == ["Edit", "Reset usage", "Delete"]


def test_a_service_limit_row_shows_its_weight_and_website_names():
    row = describe(SERVICE_QUOTA, "true")
    assert row["className"] == "quota-row reached"
    assert row["title"] == "iCloud · Test phone"
    assert row["status"] == "Limit reached"
    assert row["detail"] == "0 bytes used of 300.0 MB · 300.0 MB left · weight 1.1×"
    assert row["domains"] == ["Domains: gateway.icloud.com, icloud.com"]


def test_the_buttons_of_each_kind_of_limit_use_that_kinds_routes():
    program = row_program()
    expression = f"""(() => {{
      for (const [quota, service] of [[{DEVICE_LIMIT}, false], [{GROUP_LIMIT}, "group"], [{SERVICE_QUOTA}, true]]) {{
        const actions = createQuotaRow({{quota, service}}).children[3];
        for (const label of ["Edit", "Reset usage", "Delete"]) actions.find(label).click();
      }}
      return calls;
    }})()"""
    assert run_node(program, expression) == [
        ["edit device", "alice"], ["reset", False],
        ["delete", "/api/data-limits/delete", {"device_id": "alice"}, "Device limit deleted."],
        ["edit group", "family"], ["reset", "group"],
        ["delete", "/api/group-data-limits/delete", {"group_id": "family"}, "Shared limit deleted."],
        ["edit service", "alice-icloud"], ["reset", True],
        ["delete", "/api/service-quotas/delete", {"id": "alice-icloud"}, "Service limit deleted."],
    ]


def test_reset_usage_posts_to_the_route_of_its_kind():
    setup = """
      function request(path, method, body) { calls.push([path, method, body]); return Promise.resolve({}); }
      function showMessage(message, kind) { calls.push([message, kind]); }
      function setButtonBusy(button, busy) { calls.push(["busy", busy]); }
      function loadQuotas() { calls.push(["loaded"]); }
    """
    expression = """(async () => {
      const button = {};
      await resetQuota({device_id: "alice"}, false, button);
      await resetQuota({group_id: "family"}, "group", button);
      await resetQuota({id: "alice-icloud"}, true, button);
      return calls;
    })()"""
    assert run_async([PAGE, async_function_source("resetQuota"), setup], expression) == [
        ["busy", True], ["Resetting quota usage...", "working"],
        ["/api/data-limits/reset", "POST", {"device_id": "alice"}],
        ["Quota usage reset.", "success"], ["loaded"], ["busy", False],
        ["busy", True], ["Resetting quota usage...", "working"],
        ["/api/group-data-limits/reset", "POST", {"group_id": "family"}],
        ["Quota usage reset.", "success"], ["loaded"], ["busy", False],
        ["busy", True], ["Resetting quota usage...", "working"],
        ["/api/service-quotas/reset", "POST", {"id": "alice-icloud"}],
        ["Quota usage reset.", "success"], ["loaded"], ["busy", False],
    ]


def test_a_refused_reset_says_why_and_leaves_the_list_alone():
    setup = """
      function request() { return Promise.reject(new Error("data limit does not exist")); }
      function showMessage(message, kind) { calls.push([message, kind]); }
      function setButtonBusy(button, busy) { calls.push(["busy", busy]); }
      function loadQuotas() { calls.push(["loaded"]); }
    """
    expression = "resetQuota({device_id: 'alice'}, false, {}).then(() => calls)"
    assert run_async([PAGE, async_function_source("resetQuota"), setup], expression) == [
        ["busy", True], ["Resetting quota usage...", "working"],
        ["data limit does not exist", "error"], ["busy", False],
    ]


def test_the_limit_lists_count_what_they_show_and_say_why_they_are_empty():
    setup = """
      function lists(data) {
        renderDataLimits(data); renderGroupDataLimits(data); renderServiceQuotas(data);
        return ["data-limit", "group-data-limit", "service-quota"].map((prefix) => [
          element(prefix + "-count").textContent, element(prefix + "-list").children.length,
          element(prefix + "-list").textContent.slice(0, 40)]);
      }
      const device = DEVICE; const group = GROUP; const service = SERVICE;
    """.replace("DEVICE", DEVICE_LIMIT).replace("GROUP", GROUP_LIMIT).replace("SERVICE", SERVICE_QUOTA)
    program = [
        PAGE,
        function_source("formatQuotaBytes"), function_source("quotaResetText"),
        function_source("quotaRowProgress"), function_source("createQuotaRow"),
        function_source("renderDataLimits"), function_source("renderGroupDataLimits"),
        function_source("renderServiceQuotas"),
        "function editDataLimit() {} function editGroupDataLimit() {} function editServiceQuota() {}",
        "function resetQuota() {} function deleteItem() {}",
        setup,
    ]
    expression = """({
      none: lists({}),
      refused: lists({error: "Check the admin token."}),
      one: lists({data_limits: [device], group_data_limits: [group], service_quotas: [service]}),
      two: lists({data_limits: [device, device], group_data_limits: [group, group],
                  service_quotas: [service, service]})
    })"""
    result = run_node(program, expression)
    assert result["none"] == [
        ["0 limits", 0, "No device limits saved."],
        ["0 group limits", 0, "No shared group limits saved."],
        ["0 service limits", 0, "No service limits saved."],
    ]
    assert [row[2] for row in result["refused"]] == ["Check the admin token."] * 3
    assert [(row[0], row[1]) for row in result["one"]] == [
        ("1 limit", 1), ("1 group limit", 1), ("1 service limit", 1)]
    assert [(row[0], row[1]) for row in result["two"]] == [
        ("2 limits", 2), ("2 group limits", 2), ("2 service limits", 2)]


def test_days_are_written_as_ranges():
    setup = "const days = (list) => formatDays(list);"
    cases = [[0, 1, 2, 3, 4], [5, 6], [0], [0, 2], [0, 1, 2, 4, 5, 6], [6, 0, 1], [0, 1, 2, 3, 4, 5, 6]]
    result = run(["formatDays"], f"{cases}.map(days)", setup=setup, constants=["DAY_NAMES"])
    assert result == ["Mon–Fri", "Sat, Sun", "Mon", "Mon, Wed", "Mon–Wed, Fri–Sun", "Mon, Tue, Sun",
                      "Every day"]
    cases = run(["formatMinute"], "[0, 5, 59, 60, 540, 1020, 1439].map(formatMinute)")
    assert cases == ["00:00", "00:05", "00:59", "01:00", "09:00", "17:00", "23:59"]


def test_a_time_field_becomes_a_minute_of_the_day():
    setup = """
      function minute(value) { element("t").value = value; return timeToMinute("t"); }
    """
    assert run(["timeToMinute"], '["00:00", "09:00", "17:30", "23:59"].map(minute)', setup=setup) == [
        0, 540, 1050, 1439]


def test_a_schedule_target_goes_to_the_form_value_and_back():
    names = ["scheduleTargetFields", "scheduleTargetValue"]
    expression = """({
      device: scheduleTargetFields("device:alice"),
      group: scheduleTargetFields("group:family"),
      everyone: scheduleTargetFields("everyone-else"),
      values: [
        scheduleTargetValue({device_id: "alice", group_id: null, everyone_else: false}),
        scheduleTargetValue({device_id: null, group_id: "family", everyone_else: false}),
        scheduleTargetValue({device_id: null, group_id: null, everyone_else: true}),
      ],
    })"""
    result = run(names, expression)
    assert result["device"] == {"device_id": "alice", "group_id": None, "everyone_else": False}
    assert result["group"] == {"device_id": None, "group_id": "family", "everyone_else": False}
    assert result["everyone"] == {"device_id": None, "group_id": None, "everyone_else": True}
    assert result["values"] == ["device:alice", "group:family", "everyone-else"]


def test_a_saved_schedule_lists_its_target_days_hours_and_state():
    setup = """
      let groups = [{id: "family", name: "Family"}];
      function deviceName(id) { return "Phone " + id; }
      function profileName(id) { return "Profile " + id; }
      function editSchedule() {} function deleteItem() {}
    """
    schedules = """[
      {id: "work", device_id: "alice", group_id: null, everyone_else: false, profile_id: "slow",
       days_of_week: [0, 1, 2, 3, 4], start_minute: 540, end_minute: 1020, enabled: true},
      {id: "night", device_id: null, group_id: "family", everyone_else: false, profile_id: "normal",
       days_of_week: [4, 5], start_minute: 1320, end_minute: 360, enabled: false},
      {id: "others", device_id: null, group_id: null, everyone_else: true, profile_id: "slow",
       days_of_week: [0, 1, 2, 3, 4, 5, 6], start_minute: 0, end_minute: 1439, enabled: true},
    ]"""
    expression = f"""(() => {{
      renderSchedules({schedules});
      const rows = element("schedule-list").children;
      const empty = (renderSchedules([]), element("schedule-list").textContent);
      renderSchedules({schedules});
      return {{ empty, rows: element("schedule-list").children.map((row) =>
        [row.children[0].children[0].textContent, row.children[0].children[1].textContent,
         row.children[1].children.map((node) => node.textContent)]) }};
    }})()"""
    result = run(
        ["renderSchedules", "scheduleTargetName", "formatDays", "formatMinute"],
        expression, setup=setup, constants=["DAY_NAMES"],
    )
    assert result["empty"] == "No schedules yet."
    assert result["rows"] == [
        ["Phone alice → Profile slow", "Mon–Fri · 09:00–17:00 · work · Enabled", ["Edit", "Delete"]],
        ["Group Family → Profile normal", "Fri, Sat · 22:00–06:00 · night · Disabled",
         ["Edit", "Delete"]],
        ["Everyone else → Profile slow", "Every day · 00:00–23:59 · others · Enabled",
         ["Edit", "Delete"]],
    ]


def test_the_timer_list_counts_the_timers_and_each_has_a_cancel_button():
    setup = """
      function cancelTimer(id) { calls.push(["cancel", id]); }
      function lists(timers) {
        renderTimers({timers});
        return [element("timer-count").textContent, element("timer-list").textContent];
      }
    """
    one = '{id: "t1", device_name: "Test phone", profile_name: "Slow", remaining_seconds: 905}'
    two = '{id: "t2", device_name: "Bob", profile_name: "Normal", remaining_seconds: 60}'
    expression = f"""(() => {{
      const result = {{none: lists(undefined), one: lists([{one}]), two: lists([{one}, {two}])}};
      element("timer-list").children[1].children[1].click();
      result.rows = element("timer-list").children.map((row) => row.children[0].children.map((node) => node.textContent));
      result.calls = calls;
      return result;
    }})()"""
    result = run(["renderTimers", "formatTimerRemaining"], expression, setup=setup)
    assert result["none"] == ["0 active timers", "No active timers."]
    assert result["one"] == ["1 active timer", "Test phone → Slow15m 5s remainingCancel"]
    assert result["two"][0] == "2 active timers"
    assert result["rows"] == [["Test phone → Slow", "15m 5s remaining"], ["Bob → Normal", "1 minute remaining"]]
    assert result["calls"] == [["cancel", "t2"]]


def test_cancelling_a_timer_says_whether_the_normal_profile_is_active_now():
    setup = """
      let live = true;
      function request(path, method, body) {
        calls.push([path, method, body]);
        return live === null ? Promise.reject(new Error("timer does not exist or is already inactive"))
          : Promise.resolve({live_applied: live});
      }
      function showMessage(message, kind) { calls.push([message, kind]); }
      function refreshAfterSuccess(message) { calls.push(["refresh", message]); }
      function setButtonBusy(button, busy) { calls.push(["busy", busy]); }
    """
    expression = """(async () => {
      await cancelTimer("t1", {});
      live = false;
      await cancelTimer("t2", {});
      live = null;
      await cancelTimer("t3", {});
      return calls.filter((call) => call[0] !== "busy");
    })()"""
    assert run_async([PAGE, async_function_source("cancelTimer"), setup], expression) == [
        ["Cancelling the timer...", "working"], ["/api/timers/cancel", "POST", {"id": "t1"}],
        ["refresh", "Timer cancelled and the normal profile is active."],
        ["Cancelling the timer...", "working"], ["/api/timers/cancel", "POST", {"id": "t2"}],
        ["refresh", "Timer cancelled."],
        ["Cancelling the timer...", "working"], ["/api/timers/cancel", "POST", {"id": "t3"}],
        ["timer does not exist or is already inactive", "error"],
    ]


def test_each_progression_shows_the_signal_of_its_mode():
    setup = """
      function stopProgression(id) { calls.push(["stop", id]); }
      const base = {device_name: "Test phone", start_profile_name: "Normal", end_profile_name: "Slow"};
      function lists(progressions) {
        renderProgressions({progressions});
        return [element("progression-count").textContent, element("progression-list").children.map((row) => [
          row.children[0].children[0].textContent, row.children[0].children[1].textContent,
          row.children[1].children[0].style.width, row.children[2].textContent])];
      }
    """
    expression = """(() => {
      const timed = Object.assign({id: "p1", mode: "time", progress_percent: 40, remaining_seconds: 1800}, base);
      const dns = Object.assign({id: "p2", mode: "traffic", progress_percent: 5, traffic_queries: 12,
                                 remaining_seconds: 59}, base);
      const data = Object.assign({id: "p3", mode: "bytes", progress_percent: 100, traffic_bytes: 5242880,
                                  traffic_target_bytes: 52428800, remaining_seconds: 0}, base);
      const result = {none: lists(undefined), one: lists([timed]), three: lists([timed, dns, data])};
      element("progression-list").children[0].children[2].click();
      result.calls = calls;
      return result;
    })()"""
    result = run(["renderProgressions", "formatTimerRemaining", "formatQuotaBytes"], expression, setup=setup)
    assert result["none"] == ["0 active progressions", []]
    assert result["one"][0] == "1 active progression"
    assert result["three"][0] == "3 active progressions"
    assert result["three"][1] == [
        ["Test phone · Normal → Slow", "40% · time based · 30 minutes left", "40%", "Stop and recover"],
        ["Test phone · Normal → Slow", "5% · 12 DNS queries · 59 seconds left", "5%", "Stop and recover"],
        ["Test phone · Normal → Slow", "100% · 5.0 MB / 50.0 MB · 0 seconds left", "100%", "Stop and recover"],
    ]
    assert result["calls"] == [["stop", "p1"]]


def test_stopping_a_progression_says_whether_the_normal_profile_is_active_now():
    setup = """
      let live = true;
      function request(path, method, body) {
        calls.push([path, method, body]);
        return Promise.resolve({live_applied: live});
      }
      function showMessage(message, kind) { calls.push([message, kind]); }
      function refreshAfterSuccess(message) { calls.push(["refresh", message]); }
      function setButtonBusy() {}
    """
    expression = """(async () => {
      await stopProgression("p1", {});
      live = false;
      await stopProgression("p2", {});
      return calls;
    })()"""
    assert run_async([PAGE, async_function_source("stopProgression"), setup], expression) == [
        ["Stopping the progression and restoring the normal profile...", "working"],
        ["/api/progressions/stop", "POST", {"id": "p1"}],
        ["refresh", "Progression stopped and the normal profile is active."],
        ["Stopping the progression and restoring the normal profile...", "working"],
        ["/api/progressions/stop", "POST", {"id": "p2"}],
        ["refresh", "Progression stopped."],
    ]


TIME_QUOTA = """{
  id: "video-time", name: "Video time", device_name: "Test phone", domains: ["a.example", "b.example"],
  used_seconds: 1800, limit_seconds: 7200, window_duration_seconds: 43200, window_remaining_seconds: 3600,
  reached: false, running: true, reset_mode: "manual"
}"""


def test_a_time_limit_row_shows_its_timer_and_the_button_that_fits_it():
    setup = """
      function editTimeQuota(quota) { calls.push(["edit", quota.id]); }
      function timeQuotaAction(quota, action) { calls.push([action, quota.id]); }
      function resetTimeQuota(quota) { calls.push(["reset", quota.id]); }
      function deleteItem(path, body, message) { calls.push(["delete", path, body, message]); }
      const stopped = Object.assign({}, QUOTA, {running: false, window_remaining_seconds: 0});
      const reached = Object.assign({}, QUOTA, {reached: true});
      function describe(quota) {
        const row = createTimeQuotaRow(quota);
        const [heading, detail, meter, actions] = row.children;
        return {
          className: row.className, title: heading.children[0].textContent,
          status: heading.children[1].textContent, detail: detail.textContent,
          width: meter.children[0].style.width,
          buttons: actions.children.filter((node) => node.tag === "button").map((node) => node.textContent),
          domains: actions.children.filter((node) => node.tag === "span").map((node) => node.textContent),
        };
      }
    """.replace("QUOTA", TIME_QUOTA)
    expression = """(() => {
      const result = {running: describe(QUOTA), stopped: describe(stopped), reached: describe(reached)};
      const actions = createTimeQuotaRow(QUOTA).children[3];
      for (const label of ["Edit", "Stop timer", "Reset usage", "Delete"]) actions.find(label).click();
      createTimeQuotaRow(stopped).children[3].find("Start timer").click();
      result.calls = calls;
      return result;
    })()""".replace("QUOTA", TIME_QUOTA)
    result = run(["createTimeQuotaRow", "formatQuotaDuration", "quotaResetText"], expression, setup=setup)
    running = result["running"]
    assert running["title"] == "Video time · Test phone"
    assert running["status"] == "1 hour left in timer"
    assert running["detail"] == "30 minutes used of 2 hours · timer window 12 hours · manual reset"
    assert running["width"] == "25%"
    assert running["buttons"] == ["Edit", "Stop timer", "Reset usage", "Delete"]
    assert running["domains"] == ["Domains: a.example, b.example"]
    assert result["stopped"]["status"] == "Timer stopped"
    assert "Start timer" in result["stopped"]["buttons"]
    assert result["reached"]["status"] == "Limit reached"
    assert result["reached"]["className"] == "quota-row reached"
    assert result["calls"] == [
        ["edit", "video-time"], ["stop", "video-time"], ["reset", "video-time"],
        ["delete", "/api/time-quotas/delete", {"id": "video-time"}, "Time limit deleted."],
        ["start", "video-time"],
    ]


def test_the_time_limit_list_counts_what_it_shows_and_says_why_it_is_empty():
    setup = """
      function createTimeQuotaRow(quota) { return new FakeNode("row"); }
      function lists(data) {
        renderTimeQuotas(data);
        return [element("time-quota-count").textContent, element("time-quota-list").children.length,
                element("time-quota-list").textContent];
      }
    """
    expression = """({none: lists({}), refused: lists({error: "Check the admin token."}),
                       one: lists({time_quotas: [{}]}), two: lists({time_quotas: [{}, {}]})})"""
    assert run(["renderTimeQuotas"], expression, setup=setup) == {
        "none": ["0 time limits", 0, "No time limits saved."],
        "refused": ["0 time limits", 0, "Check the admin token."],
        "one": ["1 time limit", 1, ""],
        "two": ["2 time limits", 2, ""],
    }


def test_the_time_limit_timer_actions_post_to_start_and_stop_and_say_what_happened():
    setup = """
      let answer = {live_applied: true};
      function request(path, method, body) {
        calls.push([path, method, body]);
        return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
      }
      function showMessage(message, kind) { calls.push([message, kind]); }
      function refreshAfterSuccess(message) { calls.push(["refresh", message]); }
      function loadQuotas() { calls.push(["loaded"]); }
      function setButtonBusy() {}
    """
    expression = """(async () => {
      const quota = {id: "video-time"};
      await timeQuotaAction(quota, "start", {});
      await timeQuotaAction(quota, "stop", {});
      answer = {live_applied: false};
      await timeQuotaAction(quota, "start", {});
      await timeQuotaAction(quota, "stop", {});
      answer = new Error("time quota does not exist");
      await timeQuotaAction(quota, "start", {});
      answer = {};
      await resetTimeQuota(quota, {});
      return calls.filter((call) => !String(call[0]).endsWith("..."));
    })()"""
    result = run_async(
        [PAGE, async_function_source("timeQuotaAction"), async_function_source("resetTimeQuota"), setup],
        expression,
    )
    assert result == [
        ["/api/time-quotas/start", "POST", {"id": "video-time"}],
        ["refresh", "Time quota timer started and applied."],
        ["/api/time-quotas/stop", "POST", {"id": "video-time"}],
        ["refresh", "Time quota updated and applied."],
        ["/api/time-quotas/start", "POST", {"id": "video-time"}],
        ["refresh", "Time quota timer started."],
        ["/api/time-quotas/stop", "POST", {"id": "video-time"}],
        ["refresh", "Time quota updated."],
        ["/api/time-quotas/start", "POST", {"id": "video-time"}],
        ["time quota does not exist", "error"],
        ["/api/time-quotas/reset", "POST", {"id": "video-time"}],
        ["Time quota usage reset.", "success"], ["loaded"],
    ]


def test_the_domain_picker_offers_the_names_seen_for_the_chosen_device():
    setup = """
      let observedDomainsByDevice = {alice: ["a.example", "b.example", "c.example"], bob: []};
      let savedQuotaDomains = {};
      function options(prefix, device, selected) {
        element(prefix + "-device").value = device;
        populateQuotaDomains(prefix, selected);
        return element(prefix + "-domains").children.map((option) => [option.value, option.textContent, option.selected === true, option.disabled === true]);
      }
    """
    expression = """({
      all: options("service-quota", "alice"),
      kept: options("time-quota", "alice", ["b.example", "gone.example"]),
      nothing: options("service-quota", "bob"),
      unknown: options("service-quota", "ghost"),
      service: (element("service-quota-device").value = "alice", populateServiceQuotaDomains(["c.example"]),
                element("service-quota-domains").children.map((option) => option.selected === true)),
      time: (element("time-quota-device").value = "alice", populateTimeQuotaDomains(["a.example"]),
             element("time-quota-domains").children.map((option) => option.selected === true))
    })"""
    result = run(
        ["populateQuotaDomains", "populateServiceQuotaDomains", "populateTimeQuotaDomains"],
        expression, setup=setup,
    )
    assert result["all"] == [["a.example", "a.example", False, False], ["b.example", "b.example", False, False],
                             ["c.example", "c.example", False, False]]
    # The Pi keeps the names a limit was saved with after their observations expire, so an edit
    # must offer them selected or saving it would drop them.
    assert [(option[0], option[2]) for option in result["kept"]] == [
        ("a.example", False), ("b.example", True), ("c.example", False), ("gone.example", True)]
    assert result["kept"][3][1] == "gone.example (saved, not seen lately)"
    assert result["kept"][1][1] == "b.example"
    empty = [["", "No observed domains for this device", False, True]]
    assert result["nothing"] == empty and result["unknown"] == empty
    assert result["service"] == [False, False, True]
    assert result["time"] == [True, False, False]


def test_a_refresh_keeps_the_names_a_limit_was_saved_with_for_its_own_device_only():
    setup = """
      let observedDomainsByDevice = {alice: ["a.example"], bob: ["a.example", "other.example"]};
      let savedQuotaDomains = {};
      function shown(prefix, device) {
        if (device) element(prefix + "-device").value = device;
        populateQuotaDomains(prefix);
        return element(prefix + "-domains").children.map((option) => [option.value, option.selected === true]);
      }
    """
    expression = """(() => {
      element("service-quota-device").value = "alice";
      populateQuotaDomains("service-quota", ["gone.example", "a.example"]);
      return {
        edit: element("service-quota-domains").children.map((option) => [option.value, option.selected === true]),
        refreshed: shown("service-quota"),
        another: shown("service-quota", "bob"),
        back: shown("service-quota", "alice"),
        timeForm: shown("time-quota", "alice"),
        forgotten: (delete savedQuotaDomains["service-quota"], shown("service-quota")),
      };
    })()"""
    result = run(["populateQuotaDomains"], expression, setup=setup)
    both = [["a.example", True], ["gone.example", True]]
    assert result["edit"] == both and result["refreshed"] == both and result["back"] == both
    assert result["another"] == [["a.example", False], ["other.example", False]]
    # The other form has its own limit and keeps nothing of this one.
    assert result["timeForm"] == [["a.example", False]]
    assert result["forgotten"] == [["a.example", False]]


def edit_setup():
    return """
      let guarded = false;
      function guardDraft(id, again) { calls.push(["guard", id]); return guarded; }
      function revealElement(node) { calls.push(["reveal", node.id]); }
      function fields(prefix, names) { return names.map((name) => {
        const node = element(prefix + "-" + name);
        return name.endsWith("enabled") || name === "enabled" ? node.checked : node.value; }); }
    """


def test_edit_fills_a_device_limit_form_from_the_saved_row():
    expression = """(() => {
      const quota = {device_id: "alice", profile_id: "slow", limit_bytes: 1610612736, reset_mode: "daily",
                     reset_interval_seconds: 0, reset_times: [480, 1230], enabled: false};
      for (const suffix of ["interval-field", "times-field", "interval", "times"]) element("data-limit-" + suffix);
      editDataLimit(quota);
      return {
        values: fields("data-limit", ["device", "profile", "amount", "unit", "reset-mode", "interval", "times", "enabled"]),
        hidden: [element("data-limit-interval-field").hidden, element("data-limit-times-field").hidden],
        calls
      };
    })()"""
    result = run(["editDataLimit", "setQuotaAmount", "syncQuotaReset"], expression, setup=edit_setup())
    assert result["values"] == ["alice", "slow", 1536, "1048576", "daily", 1, "08:00, 20:30", False]
    assert result["hidden"] == [True, False]
    assert result["calls"] == [["guard", "data-limit-form"], ["reveal", "data-limit-form"]]


def test_edit_does_not_replace_unfinished_edits_without_asking():
    expression = """(() => {
      guarded = true;
      editDataLimit({device_id: "alice", profile_id: "slow", limit_bytes: 1048576, reset_mode: "manual",
                     reset_interval_seconds: 0, reset_times: [], enabled: true});
      editGroupDataLimit({group_id: "family", profile_id: "slow", limit_bytes: 1048576, reset_mode: "manual",
                          reset_interval_seconds: 0, reset_times: [], enabled: true});
      return [calls, element("data-limit-device").value];
    })()"""
    result = run(["editDataLimit", "editGroupDataLimit", "setQuotaAmount", "syncQuotaReset"], expression,
                 setup=edit_setup())
    assert result == [[["guard", "data-limit-form"], ["guard", "group-data-limit-form"]], ""]


def test_edit_fills_a_group_limit_form_in_the_unit_it_was_saved_in():
    expression = """(() => {
      editGroupDataLimit({group_id: "family", profile_id: "slow", limit_bytes: 2147483648, reset_mode: "interval",
                          reset_interval_seconds: 5400, reset_times: [], enabled: true});
      return fields("group-data-limit", ["group", "profile", "amount", "unit", "reset-mode", "interval", "times", "enabled"]);
    })()"""
    result = run(["editGroupDataLimit", "setQuotaAmount", "syncQuotaReset"], expression, setup=edit_setup())
    assert result == ["family", "slow", 2, "1073741824", "interval", 90, "", True]


def test_edit_fills_a_service_limit_form_with_its_weight_and_chosen_names():
    setup = edit_setup() + """
      let observedDomainsByDevice = {alice: ["a.example", "b.example"]};
      let savedQuotaDomains = {};
    """
    expression = """(() => {
      element("service-quota-device").value = "alice";
      editServiceQuota({id: "video", name: "Video", device_id: "alice", profile_id: "slow", limit_bytes: 314572800,
                        weight_percent: 110, domains: ["b.example"], reset_mode: "manual",
                        reset_interval_seconds: 0, reset_times: [], enabled: true});
      return {
        values: fields("service-quota", ["id", "name", "device", "profile", "amount", "unit", "weight", "reset-mode", "enabled"]),
        typed: element("service-quota-id").dataset.typed,
        chosen: element("service-quota-domains").children.map((option) => option.selected === true),
      };
    })()"""
    result = run(
        ["editServiceQuota", "setQuotaAmount", "syncQuotaReset", "populateQuotaDomains",
         "populateServiceQuotaDomains"], expression, setup=setup)
    assert result["values"] == ["video", "Video", "alice", "slow", 300, "1048576", "1.1", "manual", True]
    assert result["typed"] == "true"
    assert result["chosen"] == [False, True]


def test_edit_fills_a_time_limit_form_in_hours_or_minutes():
    setup = edit_setup() + """
      let observedDomainsByDevice = {alice: ["a.example"]};
      let savedQuotaDomains = {};
    """
    expression = """(() => {
      element("time-quota-device").value = "alice";
      const quota = {id: "video-time", name: "Video time", device_id: "alice", profile_id: "slow",
                     limit_seconds: 18000, window_duration_seconds: 43200, domains: ["a.example"],
                     reset_mode: "interval", reset_interval_seconds: 7200, reset_times: [], enabled: true};
      editTimeQuota(quota);
      const hours = fields("time-quota", ["amount", "unit", "window", "reset-mode", "interval"]);
      editTimeQuota(Object.assign({}, quota, {limit_seconds: 5400, window_duration_seconds: 90000}));
      return [hours, fields("time-quota", ["amount", "unit", "window"])];
    })()"""
    result = run(
        ["editTimeQuota", "syncQuotaReset", "populateQuotaDomains", "populateTimeQuotaDomains"],
        expression, setup=setup)
    assert result == [[5, "3600", 720, "interval", 120], [90, "60", 1500]]


def test_a_list_typed_with_commas_loses_its_gaps():
    setup = 'function list(text) { element("l").value = text; return commaList("l"); }'
    result = run(["commaList"], '["a.com, b.com", " a.com ,, b.com,", "", " , ", "one"].map(list)', setup=setup)
    assert result == [["a.com", "b.com"], ["a.com", "b.com"], [], [], ["one"]]


def test_the_days_the_schedule_form_sends_are_the_pressed_buttons():
    setup = """
      const buttons = [0, 1, 2, 3, 4, 5, 6].map((day) => {
        const node = new FakeNode("button"); node.dataset.day = String(day); return node; });
      document.querySelectorAll = (selector) => {
        calls.push(selector);
        return buttons.filter((node) => node.getAttribute("aria-pressed") === "true");
      };
      function press(...days) { buttons.forEach((node, day) => node.setAttribute("aria-pressed", String(days.includes(day)))); }
    """
    expression = "(press(0, 1, 2, 3, 4), [selectedScheduleDays(), (press(6, 0), selectedScheduleDays()), (press(), selectedScheduleDays()), calls[0]])"
    assert run(["selectedScheduleDays"], expression, setup=setup) == [
        [0, 1, 2, 3, 4], [0, 6], [], ".day-toggle[aria-pressed='true']"]
