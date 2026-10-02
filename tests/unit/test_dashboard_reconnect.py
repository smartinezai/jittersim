"""Run the dashboard's Wi-Fi restart reconnect logic in node, with a connection that drops."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jittersim.api import server
from jittersim.api.server import DASHBOARD_HTML

SOURCE = Path(server.__file__).with_name("reconnect.js")
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)


def run_node(body):
    """Run a scenario next to the real reconnect functions. Time is simulated."""

    program = (
        "(async () => {\n"
        "let clock = 0; const slept = [];\n"
        "const sleep = async (ms) => { slept.push(ms); clock += ms; };\n"
        "const now = () => clock;\n"
        "const document = undefined;\n"
        + SOURCE.read_text(encoding="utf-8")
        + "\n"
        + body
        + "\n})().then(() => {}, (error) => { console.error(error); process.exit(1); });\n"
    )
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


# --- what the person reads ------------------------------------------------------------------


def test_the_reconnect_code_never_names_the_product():
    """Everything in this file reaches the browser before anyone has signed in."""

    assert "jittersim" not in SOURCE.read_text(encoding="utf-8").lower()


def test_the_page_carries_the_reconnect_code_once():
    assert SCRIPT.count("function followWifiChange(") == 1
    assert SCRIPT.count("async function followWifiChange(") + SCRIPT.count(
        "function followWifiChange(") >= 1


@NEEDS_NODE
def test_a_dropped_connection_never_shows_the_raw_browser_text():
    outcome = run_node(
        """
        const messages = ["Load failed", "Failed to fetch", "NetworkError when attempting to fetch resource.",
                          "The network connection was lost."];
        const converted = messages.map((text) => lostConnectionError(new TypeError(text)));
        const http = new Error("Router login failed"); http.status = 502;
        console.log(JSON.stringify({
          texts: converted.map((error) => error.message),
          lost: converted.map((error) => error.connectionLost === true),
          http: lostConnectionError(http) === http && http.connectionLost !== true,
          decode: lostConnectionError(new SyntaxError("Unexpected end of JSON input")).connectionLost,
        }));
        """
    )
    assert all(outcome["lost"]) and outcome["decode"] is True and outcome["http"] is True
    for text in outcome["texts"]:
        assert not re.search(r"Load failed|Failed to fetch|NetworkError|connection was lost", text)
        assert "Pi" in text


# --- waiting with backoff ------------------------------------------------------------------


@NEEDS_NODE
def test_waiting_backs_off_one_two_three_seconds_and_stops_at_ninety():
    outcome = run_node(
        """
        let tries = 0;
        const found = await waitForPi({ probe: async () => { tries += 1; if (tries < 6) throw new TypeError("x"); },
                                        sleep, now });
        const backoff = slept.splice(0);
        clock = 0; tries = 0;
        const gaveUp = await waitForPi({ probe: async () => { tries += 1; throw new TypeError("x"); }, sleep, now });
        console.log(JSON.stringify({ found, backoff, gaveUp, tries, elapsed: clock, steady: slept.slice(3, 8) }));
        """
    )
    assert outcome["found"] is True
    assert outcome["backoff"][:5] == [1000, 2000, 3000, 3000, 3000]
    assert outcome["gaveUp"] is False
    assert 90000 <= outcome["elapsed"] < 94000
    assert set(outcome["steady"]) == {3000}


# --- following a change across the dropped connection ------------------------------------


@NEEDS_NODE
def test_the_stored_result_is_fetched_after_the_connection_comes_back():
    outcome = run_node(
        """
        const script = [
          { operation: { id: "a", state: "running" } },
          new TypeError("Load failed"),
          new TypeError("Load failed"),
          new TypeError("Load failed"),
          { operation: { id: "a", state: "succeeded", ssid: "Lab", message: "Pi Wi-Fi updated. Join Lab with the new password." } },
        ];
        const events = [];
        const result = await followWifiChange("a", {
          fetchOperation: async (id) => {
            const step = script.shift();
            if (step instanceof Error) throw lostConnectionError(step);
            return step;
          },
          sleep, now,
          onRunning: () => events.push("running"),
          onReconnecting: () => events.push("reconnecting"),
        });
        console.log(JSON.stringify({ result, events, slept, left: script.length }));
        """
    )
    assert outcome["result"]["operation"]["state"] == "succeeded"
    assert outcome["result"]["operation"]["ssid"] == "Lab"
    assert outcome["events"][0] == "running"
    assert outcome["events"].count("reconnecting") == 1
    assert outcome["slept"] == [1000, 1000, 2000, 3000]
    assert outcome["left"] == 0


@NEEDS_NODE
def test_a_device_check_that_lags_behind_the_reconnect_does_not_stop_the_wait():
    outcome = run_node(
        """
        const script = [
          Object.assign(new Error("Allow this device to use the dashboard."), { status: 403, transient: true }),
          { operation: { id: "a", state: "failed", message: "Wi-Fi name reset failed." } },
        ];
        const result = await followWifiChange("a", {
          fetchOperation: async () => { const step = script.shift(); if (step instanceof Error) throw step; return step; },
          sleep, now, onRunning() {}, onReconnecting() {},
        });
        console.log(JSON.stringify({ result }));
        """
    )
    assert outcome["result"]["operation"]["state"] == "failed"


@NEEDS_NODE
def test_giving_up_says_so_and_a_real_refusal_is_passed_on():
    outcome = run_node(
        """
        let gaveUpCalls = 0;
        const gone = await followWifiChange("a", {
          fetchOperation: async () => { throw lostConnectionError(new TypeError("Load failed")); },
          sleep, now, onRunning() {}, onReconnecting() {}, onGivenUp: () => { gaveUpCalls += 1; },
        });
        const refused = Object.assign(new Error("valid bearer authentication is required"), { status: 401 });
        let thrown = null;
        try {
          await followWifiChange("a", { fetchOperation: async () => { throw refused; }, sleep, now,
                                         onRunning() {}, onReconnecting() {} });
        } catch (error) { thrown = error.status; }
        const expired = await followWifiChange("a", {
          fetchOperation: async () => { throw Object.assign(new Error("old"), { status: 410 }); },
          sleep, now, onRunning() {}, onReconnecting() {},
        });
        console.log(JSON.stringify({ gone, gaveUpCalls, thrown, expired }));
        """
    )
    assert outcome["gone"] == {"gaveUp": True}
    assert outcome["gaveUpCalls"] == 1
    assert outcome["thrown"] == 401
    assert outcome["expired"] == {"operation": None, "expired": True}


@NEEDS_NODE
def test_the_banner_says_which_network_to_rejoin_and_never_the_password():
    outcome = run_node(
        """
        const changed = rejoinLines({ state: "succeeded", ssid: "Lab net", password_changed: true });
        const same = rejoinLines({ state: "succeeded", ssid: "Lab net", password_changed: false });
        const failed = rejoinLines({ state: "failed", ssid: null, password_changed: false });
        const copy = rejoinLines({ state: "succeeded", kind: "router-copy", ssid: "Home", password_changed: true });
        console.log(JSON.stringify({ changed, same, failed, copy }));
        """
    )
    assert any("Lab net" in line for line in outcome["changed"])
    assert any("new password" in line for line in outcome["changed"])
    assert any("Lab net" in line for line in outcome["same"])
    assert not any("new password" in line for line in outcome["same"])
    assert any("router" in line for line in outcome["copy"])
    assert not any("new password" in line for line in outcome["copy"])
    assert outcome["failed"] == []


# --- how the page uses it ------------------------------------------------------------------


def function_source(name):
    match = re.search(r"\n    (?:async )?function " + name + r"\(", SCRIPT)
    assert match, name
    following = re.search(r"\n    (?:async )?function \w+\(", SCRIPT[match.end():])
    return SCRIPT[match.start(): match.end() + following.start()] if following else SCRIPT[match.start():]


def test_requests_turn_a_network_error_into_a_plain_reconnect_message():
    source = function_source("request")
    assert "lostConnectionError(" in source


def test_every_restarting_action_announces_the_restart_and_follows_it():
    for handler in ('element("access-point-form").addEventListener("submit"',
                    'element("reset-wifi-name").addEventListener("click"'):
        start = SCRIPT.index(handler)
        block = SCRIPT[start: start + 2600]
        assert "WIFI_RESTART_NOTICE" in block, handler
        assert "followWifiChange" in block or "runWifiChange" in block, handler
    router = function_source("routerAction")
    assert "WIFI_RESTART_NOTICE" in router
    assert "requestAcrossWifiRestart(" in function_source("routerRequest")
    assert "waitForPi(" in SCRIPT[SCRIPT.index("async function requestAcrossWifiRestart("):]


def test_a_lost_connection_does_not_mark_the_token_as_rejected():
    source = function_source("checkAdminToken")
    catch = source[source.index("} catch (error) {"):]
    assert "isConnectionLost(error)" in catch
    assert "forgetCredential" not in source
