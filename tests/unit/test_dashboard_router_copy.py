"""Run the dashboard's router Wi-Fi copy buttons and clipboard helper in node."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
HTML = DASHBOARD_HTML.split("<script>")[0]
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
PASSWORD = "browser-wifi-password"
NAME = "Browser home"


def function_body(name):
    """Return the source of one top-level dashboard function."""

    match = re.search(r"\n    (?:async )?function " + re.escape(name) + r"\(", SCRIPT)
    assert match, name
    following = re.search(r"\n    (?:async )?function \w+\(", SCRIPT[match.end() :])
    end = match.end() + following.start() if following else len(SCRIPT)
    return SCRIPT[match.start() : end]


def copy_functions():
    start = SCRIPT.index("async function copyText(")
    return SCRIPT[start : SCRIPT.index("function renderRouterWifi(", start)]


CLIPBOARD_ITEM = """
  const ClipboardItem = class { constructor(items) { this.items = items; } };
"""


def run_node(body, *, clipboard_item=True):
    """Run a scenario inside one function scope that holds the real dashboard functions."""

    program = (
        "(async () => {\n"
        + copy_functions()
        + (CLIPBOARD_ITEM if clipboard_item else "")
        + "\nconst log = [];\n"
        + body
        + "\n})().then(() => {}, (error) => { console.error(error); process.exit(1); });\n"
    )
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


FAKE_DOCUMENT = """
  function fakeDocument(copyResult) {
    const area = {
      value: "", style: {}, attrs: {},
      setAttribute(name, value) { this.attrs[name] = value; },
      select() { log.push("select"); },
      setSelectionRange() {}
    };
    return {
      area,
      activeElement: null,
      createElement(tag) { log.push("createElement:" + tag); return area; },
      body: {
        appendChild(node) { log.push("appendChild"); },
        removeChild(node) { log.push("removeChild"); }
      },
      execCommand(command) {
        log.push("execCommand:" + command + ":" + area.value);
        if (copyResult instanceof Error) throw copyResult;
        return copyResult;
      }
    };
  }
"""


# --- copyText ------------------------------------------------------------------------------


@NEEDS_NODE
def test_the_clipboard_item_write_starts_before_the_text_has_arrived():
    outcome = run_node(
        """
        let release;
        const text = new Promise((resolve) => { release = resolve; });
        const navigator = { clipboard: {
          write: async (items) => {
            log.push("write:" + Object.keys(items[0].items).join(","));
            const blob = await items[0].items["text/plain"];
            log.push("blob:" + blob.type + ":" + await blob.text());
          },
          writeText: async () => { log.push("writeText"); }
        } };
        const pending = copyText(text, { navigator: navigator, document: {} });
        log.push("returned");
        release("hello");
        const result = await pending;
        console.log(JSON.stringify({ result, log }));
        """
    )
    assert outcome["result"] == "clipboard-item"
    assert outcome["log"] == ["write:text/plain", "returned", "blob:text/plain:hello"]


@NEEDS_NODE
def test_a_plain_string_is_written_the_same_way():
    outcome = run_node(
        """
        const navigator = { clipboard: {
          write: async (items) => { log.push(await (await items[0].items["text/plain"]).text()); }
        } };
        const result = await copyText("plain", { navigator: navigator, document: {} });
        console.log(JSON.stringify({ result, log }));
        """
    )
    assert outcome == {"result": "clipboard-item", "log": ["plain"]}


@NEEDS_NODE
def test_without_clipboard_items_it_waits_for_the_text_then_uses_write_text():
    outcome = run_node(
        """
        const navigator = { clipboard: {
          write: async () => { log.push("write"); },
          writeText: async (text) => { log.push("writeText:" + text); }
        } };
        const result = await copyText(Promise.resolve("hello"), { navigator: navigator, document: {} });
        console.log(JSON.stringify({ result, log }));
        """,
        clipboard_item=False,
    )
    assert outcome == {"result": "write-text", "log": ["writeText:hello"]}


@NEEDS_NODE
def test_a_rejected_clipboard_item_write_falls_back_to_write_text():
    outcome = run_node(
        """
        const navigator = { clipboard: {
          write: async () => { log.push("write"); throw new Error("NotAllowedError"); },
          writeText: async (text) => { log.push("writeText:" + text); }
        } };
        const result = await copyText(Promise.resolve("hello"), { navigator: navigator, document: {} });
        console.log(JSON.stringify({ result, log }));
        """
    )
    assert outcome == {"result": "write-text", "log": ["write", "writeText:hello"]}


@NEEDS_NODE
def test_without_a_clipboard_it_uses_a_temporary_textarea_and_removes_it():
    outcome = run_node(
        FAKE_DOCUMENT
        + """
        const doc = fakeDocument(true);
        const result = await copyText(Promise.resolve("hello"), { navigator: {}, document: doc });
        console.log(JSON.stringify({
          result, log, readonly: "readonly" in doc.area.attrs, leftover: doc.area.value
        }));
        """
    )
    assert outcome["result"] == "exec-command"
    assert outcome["log"] == [
        "createElement:textarea", "appendChild", "select", "execCommand:copy:hello", "removeChild",
    ]
    assert outcome["readonly"] is True
    assert outcome["leftover"] == ""


@NEEDS_NODE
@pytest.mark.parametrize("copy_result", ["false", "new Error('SecurityError')"])
def test_the_textarea_is_removed_even_when_the_copy_command_fails(copy_result):
    outcome = run_node(
        FAKE_DOCUMENT
        + """
        const doc = fakeDocument(%s);
        let message = null;
        try {
          await copyText("hello", { navigator: {}, document: doc });
        } catch (error) { message = error.message; }
        console.log(JSON.stringify({ message, log, leftover: doc.area.value }));
        """
        % copy_result
    )
    assert outcome["message"] == "Copy failed"
    assert outcome["log"][-1] == "removeChild"
    assert outcome["log"].count("removeChild") == 1
    assert outcome["leftover"] == ""


@NEEDS_NODE
def test_it_fails_plainly_when_nothing_can_copy():
    outcome = run_node(
        """
        let message = null;
        try {
          await copyText("hello", { navigator: {}, document: {} });
        } catch (error) { message = error.message; }
        console.log(JSON.stringify({ message }));
        """
    )
    assert outcome == {"message": "Copy failed"}


@NEEDS_NODE
@pytest.mark.parametrize("clipboard_item", [True, False])
def test_a_rejected_text_promise_throws_its_own_error_and_touches_no_fallback(clipboard_item):
    outcome = run_node(
        FAKE_DOCUMENT
        + """
        const doc = fakeDocument(true);
        const navigator = { clipboard: {
          write: async (items) => { log.push("write"); await items[0].items["text/plain"]; },
          writeText: async () => { log.push("writeText"); }
        } };
        const failure = new Error("Router login failed.");
        failure.status = 502;
        let caught = null;
        try {
          await copyText(Promise.reject(failure), { navigator: navigator, document: doc });
        } catch (error) { caught = error; }
        console.log(JSON.stringify({
          same: caught === failure, status: caught && caught.status, log
        }));
        """,
        clipboard_item=clipboard_item,
    )
    assert outcome["same"] is True
    assert outcome["status"] == 502
    assert "writeText" not in outcome["log"]
    assert not any(entry.startswith(("createElement", "execCommand")) for entry in outcome["log"])


@NEEDS_NODE
@pytest.mark.parametrize("value", ["undefined", "null", "''", "42"])
def test_text_that_is_not_a_non_empty_string_is_never_copied(value):
    outcome = run_node(
        """
        const navigator = { clipboard: {
          write: async (items) => { log.push("write"); await items[0].items["text/plain"]; },
          writeText: async (text) => { log.push("writeText:" + text); }
        } };
        let message = null;
        try {
          await copyText(Promise.resolve(%s), { navigator: navigator, document: {} });
        } catch (error) { message = error.message; }
        console.log(JSON.stringify({ message, log }));
        """
        % value
    )
    assert outcome["message"] == "Nothing to copy"
    assert "writeText:undefined" not in outcome["log"]
    assert not any(entry.startswith("writeText") for entry in outcome["log"])


# --- routerCopyState -----------------------------------------------------------------------

READY = {
    "helperConfigured": True,
    "adminTokenVerified": True,
    "routerPasswordRejected": False,
    "busy": False,
    "hasRouterPassword": True,
}

STATE_CASES = {
    "ready_for_the_name": ({}, {"disabled": False, "title": "Copy the router Wi-Fi name",
                                 "needsPassword": False}),
    "ready_for_the_password": (
        {"kind": "password"},
        {"disabled": False, "title": "Copy the router Wi-Fi password", "needsPassword": False},
    ),
    "token_not_checked": (
        {"adminTokenVerified": False},
        {"disabled": True, "title": "Check the admin token first", "needsPassword": False},
    ),
    "no_helper": (
        {"helperConfigured": False},
        {"disabled": True, "title": "Needs the Pi helper", "needsPassword": False},
    ),
    "password_rejected": (
        {"routerPasswordRejected": True},
        {"disabled": True, "title": "Router password rejected. Enter it again.",
         "needsPassword": False},
    ),
    "busy": (
        {"busy": True},
        {"disabled": True, "title": "Working…", "needsPassword": False},
    ),
    "no_router_password_stays_clickable": (
        {"hasRouterPassword": False},
        {"disabled": False, "title": "Enter the router password first", "needsPassword": True},
    ),
    "token_comes_before_the_missing_password": (
        {"adminTokenVerified": False, "hasRouterPassword": False},
        {"disabled": True, "title": "Check the admin token first", "needsPassword": True},
    ),
    "rejected_comes_before_busy": (
        {"routerPasswordRejected": True, "busy": True},
        {"disabled": True, "title": "Router password rejected. Enter it again.",
         "needsPassword": False},
    ),
}


@NEEDS_NODE
@pytest.mark.parametrize("name", STATE_CASES)
def test_router_copy_state(name):
    changes, expected = STATE_CASES[name]
    outcome = run_node(
        "console.log(JSON.stringify(routerCopyState(%s)));" % json.dumps({**READY, **changes})
    )
    assert outcome == expected


# --- copyRouterWifi, with the real helpers around it ---------------------------------------

HARNESS = """
  const written = [];
  const messages = [];
  const opened = [];
  const requests = [];
  const timers = [];
  const elements = {};
  function makeElement(id) {
    let text = "";
    return {
      id: id, disabled: false, value: "", attrs: {},
      get textContent() { return text; },
      set textContent(value) { text = value; written.push(String(value)); },
      setAttribute(name, value) { this.attrs[name] = value; },
      removeAttribute(name) { delete this.attrs[name]; }
    };
  }
  function element(id) { return elements[id] || (elements[id] = makeElement(id)); }
  const window = {
    setTimeout(callback, delay) {
      timers.push({ callback, delay, cancelled: false });
      return timers.length;
    },
    clearTimeout(id) { if (timers[id - 1]) timers[id - 1].cancelled = true; }
  };
  let adminTokenRevision = 0;
  let routerPasswordRevision = 0;
  let routerPasswordRejected = false;
  let routerPasswordVerified = false;
  let routerActionPending = false;
  let routerPasswordChecking = false;
  let routerCopyPending = false;
  let statusUpdates = 0;
  function requireAdminToken() { return true; }
  function openCredentials(kind, opener) { opened.push(kind); }
  function showMessage(message, kind) { messages.push([message, kind]); }
  function updateCredentialStatus() { statusUpdates += 1; }
  let script = null;
  function request(path, method, body) {
    requests.push({ path, method, body: JSON.parse(JSON.stringify(body)) });
    return script();
  }
  const clipboardWrites = [];
  let clipboardMode = "works";
  const navigator = { clipboard: {
    write: async (items) => {
      if (clipboardMode === "denied") throw new Error("NotAllowedError");
      const blob = await items[0].items["text/plain"];
      clipboardWrites.push(await blob.text());
    },
    writeText: async (text) => {
      if (clipboardMode === "denied") throw new Error("NotAllowedError");
      clipboardWrites.push(text);
    }
  } };
  function failure(message, status) {
    const error = new Error(message);
    error.status = status;
    return error;
  }
  function snapshot() {
    return {
      clipboardWrites, messages, opened, requests, written, routerPasswordRejected,
      routerPasswordVerified, routerCopyPending,
      nameLabel: element("copy-wifi-name").textContent,
      passwordLabel: element("copy-wifi-password").textContent,
      timers: timers.map((timer) => timer.delay),
      cancelled: timers.map((timer) => timer.cancelled)
    };
  }
  element("copy-wifi-name").textContent = "Copy Wi-Fi name";
  element("copy-wifi-password").textContent = "Copy Wi-Fi password";
  written.length = 0;
  element("router-admin-password").value = "router-secret";
"""


def run_copy(body):
    helpers = (
        function_body("isRouterLoginFailure")
        + function_body("routerPasswordBlocked")
        + function_body("setButtonBusy")
    )
    return run_node(HARNESS + helpers + body)


@NEEDS_NODE
def test_the_name_button_asks_for_the_name_only_and_copies_it():
    outcome = run_copy(
        """
        script = async () => ({ result: { ssid: "Browser home" } });
        await copyRouterWifi("name");
        const during = snapshot();
        timers[0].callback();
        console.log(JSON.stringify({ during, after: snapshot() }));
        """
    )
    during, after = outcome["during"], outcome["after"]
    assert during["requests"] == [{
        "path": "/api/router/wifi-credentials", "method": "POST",
        "body": {"admin_password": "router-secret", "include_password": False},
    }]
    assert during["clipboardWrites"] == [NAME]
    assert during["nameLabel"] == "Copied"
    assert during["passwordLabel"] == "Copy Wi-Fi password"
    assert during["timers"] == [2000]
    assert after["nameLabel"] == "Copy Wi-Fi name"
    assert during["routerCopyPending"] is False
    assert during["routerPasswordVerified"] is True
    assert during["messages"][-1] == ["Router Wi-Fi name copied.", "success"]


@NEEDS_NODE
def test_the_password_button_asks_for_the_password_and_copies_it_without_showing_it():
    outcome = run_copy(
        """
        script = async () => ({ result: { ssid: "Browser home", password: "browser-wifi-password" } });
        await copyRouterWifi("password");
        console.log(JSON.stringify(snapshot()));
        """
    )
    assert outcome["requests"][0]["body"] == {
        "admin_password": "router-secret", "include_password": True,
    }
    assert outcome["clipboardWrites"] == [PASSWORD]
    assert outcome["passwordLabel"] == "Copied"
    assert outcome["nameLabel"] == "Copy Wi-Fi name"
    assert outcome["messages"][-1] == [
        "Router Wi-Fi password copied. It stays on your clipboard until you copy something else.",
        "success",
    ]
    # Nothing the page shows or announces may hold the copied value.
    shown = json.dumps([outcome["written"], outcome["messages"], outcome["opened"]])
    assert PASSWORD not in shown
    assert "router-secret" not in shown


@NEEDS_NODE
def test_the_clipboard_write_starts_inside_the_click_before_the_router_answers():
    outcome = run_copy(
        """
        let release;
        const startedAfterRequests = [];
        navigator.clipboard.write = async (items) => {
          startedAfterRequests.push(requests.length);
          const blob = await items[0].items["text/plain"];
          clipboardWrites.push(await blob.text());
        };
        script = () => new Promise((resolve) => { release = resolve; });
        const finished = copyRouterWifi("password");
        const beforeAnswer = {
          startedAfterRequests: startedAfterRequests.slice(), writes: clipboardWrites.length
        };
        release({ result: { ssid: "Browser home", password: "browser-wifi-password" } });
        await finished;
        console.log(JSON.stringify({ beforeAnswer, writes: clipboardWrites.slice() }));
        """
    )
    # Started synchronously after the one request and before the router had answered.
    assert outcome["beforeAnswer"] == {"startedAfterRequests": [1], "writes": 0}
    assert outcome["writes"] == [PASSWORD]


@NEEDS_NODE
def test_without_a_router_password_a_click_opens_the_panel_and_calls_nothing():
    outcome = run_copy(
        """
        element("router-admin-password").value = "";
        script = async () => { throw new Error("must not be called"); };
        await copyRouterWifi("name");
        await copyRouterWifi("password");
        console.log(JSON.stringify(snapshot()));
        """
    )
    assert outcome["opened"] == ["router", "router"]
    assert outcome["requests"] == []
    assert outcome["clipboardWrites"] == []
    assert outcome["routerCopyPending"] is False


@NEEDS_NODE
def test_a_rejected_router_password_disables_the_buttons_until_it_changes():
    outcome = run_copy(
        """
        script = async () => {
          throw failure("Router login failed. Check the router admin password and try again.", 502);
        };
        await copyRouterWifi("password");
        console.log(JSON.stringify(snapshot()));
        """
    )
    assert outcome["routerPasswordRejected"] is True
    assert outcome["routerPasswordVerified"] is False
    assert outcome["clipboardWrites"] == []
    assert outcome["messages"][-1] == [
        "Could not copy. Router login failed. Check the router admin password and try again.",
        "error",
    ]
    assert outcome["passwordLabel"] == "Copy Wi-Fi password"
    assert outcome["routerCopyPending"] is False


@NEEDS_NODE
def test_a_second_attempt_after_a_rejection_does_not_call_the_router_again():
    outcome = run_copy(
        """
        script = async () => {
          throw failure("Router login failed. Check the router admin password and try again.", 502);
        };
        await copyRouterWifi("password");
        await copyRouterWifi("name");
        console.log(JSON.stringify(snapshot()));
        """
    )
    assert len(outcome["requests"]) == 1
    assert outcome["opened"] == ["router"]


@NEEDS_NODE
@pytest.mark.parametrize(
    "status,message",
    [
        (502, "Could not contact the router. Check the Pi's wired connection and try again."),
        (502, "The networking helper is out of date. Restart the JitterSim services or reinstall the release."),
        (503, "network helper is not configured"),
        (400, "router admin password is required"),
    ],
)
def test_other_failures_do_not_mark_the_router_password_as_rejected(status, message):
    outcome = run_copy(
        """
        script = async () => { throw failure(%s, %d); };
        await copyRouterWifi("name");
        console.log(JSON.stringify(snapshot()));
        """
        % (json.dumps(message), status)
    )
    assert outcome["routerPasswordRejected"] is False
    assert outcome["messages"][-1] == ["Could not copy. " + message, "error"]
    assert outcome["clipboardWrites"] == []


@NEEDS_NODE
def test_a_rejection_for_an_older_password_is_not_recorded_against_a_new_one():
    outcome = run_copy(
        """
        script = async () => {
          routerPasswordRevision += 1;
          throw failure("Router login failed. Check the router admin password and try again.", 502);
        };
        await copyRouterWifi("name");
        console.log(JSON.stringify(snapshot()));
        """
    )
    assert outcome["routerPasswordRejected"] is False


@NEEDS_NODE
def test_a_blocked_clipboard_is_explained_without_the_value():
    outcome = run_copy(
        """
        clipboardMode = "denied";
        script = async () => ({ result: { ssid: "Browser home", password: "browser-wifi-password" } });
        await copyRouterWifi("password");
        console.log(JSON.stringify(snapshot()));
        """
    )
    assert outcome["messages"][-1] == [
        "Could not copy. The browser did not allow it.", "error",
    ]
    assert outcome["routerPasswordRejected"] is False
    assert PASSWORD not in json.dumps(outcome["messages"])
    assert outcome["passwordLabel"] == "Copy Wi-Fi password"


@NEEDS_NODE
def test_a_second_click_while_one_copy_is_running_is_ignored():
    outcome = run_copy(
        """
        let release;
        script = () => new Promise((resolve) => { release = resolve; });
        const first = copyRouterWifi("name");
        const second = copyRouterWifi("password");
        const pendingWhileRunning = snapshot().routerCopyPending;
        release({ result: { ssid: "Browser home" } });
        await first;
        await second;
        console.log(JSON.stringify({ ...snapshot(), pendingWhileRunning }));
        """
    )
    assert outcome["pendingWhileRunning"] is True
    assert len(outcome["requests"]) == 1
    assert outcome["clipboardWrites"] == [NAME]
    assert outcome["routerCopyPending"] is False


@NEEDS_NODE
def test_the_copied_label_is_restored_by_the_latest_click_only():
    outcome = run_copy(
        """
        script = async () => ({ result: { ssid: "Browser home" } });
        await copyRouterWifi("name");
        await copyRouterWifi("name");
        const cancelled = snapshot().cancelled;
        const labelBefore = element("copy-wifi-name").textContent;
        for (const timer of timers) if (!timer.cancelled) timer.callback();
        console.log(JSON.stringify({ cancelled, labelBefore, labelAfter: element("copy-wifi-name").textContent }));
        """
    )
    assert outcome == {
        "cancelled": [True, False], "labelBefore": "Copied", "labelAfter": "Copy Wi-Fi name",
    }


# --- the page around the buttons -----------------------------------------------------------


def test_the_sidebar_has_both_buttons_in_their_own_row_under_the_router_wifi_switch():
    match = re.search(
        r'<div class="router-wifi-control">(.*?)\n        </div>\n'
        r'        <div class="router-wifi-copy">(.*?)\n        </div>\n      </div>',
        HTML,
        re.DOTALL,
    )
    assert match
    control, copy = match.groups()
    assert 'id="router-wifi-toggle"' in control
    assert "copy-wifi" not in control
    assert 'id="copy-wifi-name" type="button">Copy Wi-Fi name</button>' in copy
    assert 'id="copy-wifi-password" type="button">Copy Wi-Fi password</button>' in copy
    assert HTML.count('id="copy-wifi-name"') == 1
    assert HTML.count('id="copy-wifi-password"') == 1


def test_the_existing_switch_row_keeps_its_flex_behaviour():
    rule = re.search(r"\.router-wifi-control \{(.*?)\}", HTML, re.DOTALL).group(1)
    assert "wrap" not in rule
    assert "display: flex" in rule
    copy_rule = re.search(r"\.router-wifi-copy \{(.*?)\}", HTML, re.DOTALL).group(1)
    assert "grid-template-columns: 1fr 1fr" in copy_rule


def test_both_buttons_are_wired_to_the_copy_helper():
    assert 'element("copy-wifi-name").addEventListener("click", () => copyRouterWifi("name"));' in SCRIPT
    assert (
        'element("copy-wifi-password").addEventListener("click", () => copyRouterWifi("password"));'
        in SCRIPT
    )


def test_the_render_updates_both_buttons_from_the_copy_state():
    body = function_body("renderRouterWifi")
    assert "routerCopyState(" in body
    assert '"copy-wifi-name"' in body
    assert '"copy-wifi-password"' in body
    assert "routerCopyPending" in body


@pytest.mark.parametrize("name", ["pollRouterWifi", "scheduleRouterWifiPoll", "routerAction",
                                  "checkRouterPassword"])
def test_other_router_work_waits_for_a_running_copy(name):
    assert "routerCopyPending" in function_body(name)


def test_the_copy_flag_is_declared_before_the_first_render():
    assert SCRIPT.index("let routerCopyPending") < SCRIPT.index("function updateCredentialStatus(")


def test_the_response_is_only_returned_from_the_then_callback():
    body = function_body("copyRouterWifi")
    match = re.search(r"const pending = request\(.*?\)\.then\((.*?)\);\n", body, re.DOTALL)
    assert match
    # No block and no assignment: the callback hands the value on and does nothing else.
    assert match.group(1) == (
        "(response) => wantPassword ? response.result.password : response.result.ssid"
    )
    assert re.findall(r"\bresponse\b", body) == ["response"] * 3
    assert re.findall(r"\bpending\b", body) == ["pending", "pending"]


def test_the_copied_value_only_travels_through_the_clipboard_call():
    body = function_body("copyRouterWifi")
    assert body.count(".result.password") == 1
    for forbidden in ("innerHTML", "localStorage", "sessionStorage", "console.", "dataset",
                      ".value =", "title =", "alert(", "prompt("):
        assert forbidden not in body, forbidden
    # Text is only ever set on the clicked button and the credential status line.
    targets = re.findall(r"([\w.()\"-]+)\.textContent =", body)
    assert targets and set(targets) <= {"button", 'element("credential-router-status")'}
    assert "copyText(" in body
    helper = function_body("copyText")
    assert "innerHTML" not in helper
    assert "console." not in helper
