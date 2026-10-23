"""Wi-Fi reset lifecycle gaps found by scratch-copy mutation testing."""

import json
import subprocess

import pytest

from tests.dashboard_script import NODE, function_source

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def reset_scenario(**options):
    program = r"""
const options = OPTIONS;
let wifiResetPending = !!options.resetPending, wifiChangePending = !!options.changePending;
let helperConfigured = options.helper !== false, adminTokenRevision = 1;
const brandInfo = {default_ssid: "Lab"}, WIFI_RESTART_NOTICE = "Restart notice";
const fields = {"wifi-reset-status": {textContent: "original"}, "access-point-ssid": {value: "Typed"}};
const element = (id) => fields[id];
const formDrafts = new Map([["access-point-form", "draft-one"]]);
const buttons = [{disabled: false}, {disabled: false}];
const resetWifiButtons = () => buttons;
const events = [], messages = [];
const clearDraft = (id) => {events.push("clear"); formDrafts.delete(id);};
const setControlsOpen = (...args) => events.push(["sheet", ...args]);
const showMessage = (...args) => messages.push(args);
const settleResetWifiButtons = () => {events.push("settle"); buttons.forEach(b => b.disabled = !helperConfigured);};
let questions = 0, changes = 0, heldAtQuestion;
const confirmFromButton = async () => {
  questions++; heldAtQuestion = wifiResetPending;
  if (options.helperDisappears) helperConfigured = false;
  return options.confirm !== false;
};
const runWifiChange = async (path, payload, callback, name) => {
  changes++;
  events.push(["run", path, payload, name, buttons.map(b => b.disabled)]);
  if (options.newToken) adminTokenRevision++;
  if (options.newDraft) formDrafts.set("access-point-form", "draft-two");
  if (options.newName) fields["access-point-ssid"].value = "New typing";
  if (options.fail) throw new Error("Synthetic failure");
  const operation = {state: options.operationState || "succeeded", ssid: "Lab", message: "Finished"};
  callback(operation);
  return {gaveUp: !!options.gaveUp};
};
FUNCTION
resetWifiName({currentTarget: buttons[0]}).then(() => console.log(JSON.stringify({
  pending: wifiResetPending, fields, drafts: [...formDrafts], buttons, events, messages,
  questions, changes, heldAtQuestion
}))).catch(error => {console.error(error); process.exit(1);});
""".replace("OPTIONS", json.dumps(options)).replace(
        "FUNCTION", "async " + function_source("resetWifiName"))
    result = subprocess.run([NODE, "-"], input=program, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("options", [
    {"resetPending": True}, {"changePending": True}, {"helper": False},
])
def test_reset_cannot_overlap_an_operation_or_start_without_a_helper(options):
    result = reset_scenario(**options)
    assert result["questions"] == result["changes"] == 0
    assert result["events"] == []


@pytest.mark.parametrize("options", [{"confirm": False}, {"helperDisappears": True}])
def test_cancel_or_lost_helper_releases_the_question_without_starting(options):
    result = reset_scenario(**options)
    assert result["heldAtQuestion"] is True
    assert result["pending"] is False
    assert result["changes"] == 0
    assert result["events"] == ["settle"]
    assert result["fields"]["wifi-reset-status"]["textContent"] == "original"


def test_success_disables_both_buttons_closes_sheet_and_clears_the_replaced_draft():
    result = reset_scenario()
    assert result["heldAtQuestion"] is True
    assert result["events"] == [
        ["sheet", False, False],
        ["run", "/api/access-point/reset-name", {}, "Lab", [True, True]],
        "clear", "settle",
    ]
    assert result["fields"]["access-point-ssid"]["value"] == "Lab"
    assert result["drafts"] == []
    assert result["messages"] == [["Finished", "success"]]
    assert result["pending"] is False
    assert result["buttons"] == [{"disabled": False}, {"disabled": False}]


@pytest.mark.parametrize("options, expected_name, expected_draft", [
    ({"newDraft": True}, "Typed", "draft-two"),
    ({"newName": True}, "New typing", "draft-one"),
    ({"operationState": "failed"}, "Typed", "draft-one"),
])
def test_reset_result_preserves_later_edits_and_a_failed_operations_draft(
    options, expected_name, expected_draft
):
    result = reset_scenario(**options)
    assert result["fields"]["access-point-ssid"]["value"] == expected_name
    assert result["drafts"] == [["access-point-form", expected_draft]]
    assert "clear" not in result["events"]
    assert result["pending"] is False


def test_an_old_token_callback_cannot_change_fields_messages_or_controls():
    result = reset_scenario(newToken=True)
    assert result["fields"]["access-point-ssid"]["value"] == "Typed"
    assert result["fields"]["wifi-reset-status"]["textContent"] == "Restoring the Pi Wi-Fi name..."
    assert result["drafts"] == [["access-point-form", "draft-one"]]
    assert result["messages"] == []
    assert "settle" not in result["events"]
    assert result["pending"] is False


@pytest.mark.parametrize("options, message", [
    ({"fail": True}, "Synthetic failure"),
    ({"gaveUp": True}, "The Pi has not answered yet. See the note on this page."),
])
def test_failure_and_giving_up_release_both_buttons(options, message):
    result = reset_scenario(**options)
    assert result["fields"]["wifi-reset-status"]["textContent"] == message
    assert result["pending"] is False
    assert result["buttons"] == [{"disabled": False}, {"disabled": False}]
