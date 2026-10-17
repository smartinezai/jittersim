"""Device names on the pages that came with main: devices, schedules, access, activity and tests.

A saved device made from a connected client is called "Client <ip>" until someone names it.
The dashboard shows the saved label or a typed name instead and the address as small text.
"""

import json
import re
import shutil
import subprocess
import time
import unittest
from pathlib import Path

import pytest

from jittersim.api.server import DASHBOARD_HTML, ControlServer
from jittersim.config.models import (
    Device,
    DeviceGroup,
    Impairment,
    Profile,
    Scenario,
    ScenarioRun,
    ScenarioStep,
)
from jittersim.config.storage import ConfigRepository

API = Path(__file__).resolve().parents[2] / "src" / "jittersim" / "api"
SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
WORKFLOWS = (API / "workflows.js").read_text(encoding="utf-8")
ACCESS = (API / "access.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def page_function(name):
    """Return one top-level function of the page script in server.py."""

    match = re.search(r"\n    (?:async )?function " + re.escape(name) + r"\(", SCRIPT)
    assert match, name
    following = re.search(r"\n    (?:async )?function \w+\(", SCRIPT[match.end():])
    end = match.end() + following.start() if following else len(SCRIPT)
    return SCRIPT[match.start():end]


def file_function(source, name):
    """Return one function of workflows.js or access.js. They are not indented."""

    match = re.search(r"^(?:async )?function " + re.escape(name) + r"\(", source, re.MULTILINE)
    assert match, name
    following = re.search(r"^(?:async )?function \w+\(", source[match.end():], re.MULTILINE)
    end = match.end() + following.start() if following else len(source)
    return source[match.start():end]


def run_node(program):
    result = subprocess.run([NODE, "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


NAMES = "".join(
    page_function(name)
    for name in (
        "deviceDisplayName", "deviceTitle", "devicePickerLabel",
        "deviceIsPaused", "deviceProfileCell", "deviceStateCell",
    )
)
LAPTOP = {
    "id": "laptop", "name": "Client 192.168.50.11", "display_name": "Sam laptop",
    "ipv4_address": "192.168.50.11", "mac_address": "aa:aa:aa:aa:aa:11", "profile_id": "slow",
}
SPARE = {
    "id": "spare", "name": "Client 192.168.50.12", "display_name": "Unknown device",
    "ipv4_address": "192.168.50.12", "mac_address": None, "profile_id": "slow",
}
DEVICES = "const devices = " + json.dumps([LAPTOP, SPARE]) + ";\n"


@pytest.mark.skipif(NODE is None, reason="node is required")
def test_a_device_title_is_the_name_and_an_unnamed_device_keeps_its_address():
    assert run_node(
        NAMES + DEVICES
        + "console.log(JSON.stringify(devices.map(deviceTitle).concat([deviceTitle({})])));\n"
    ) == ["Sam laptop", "Unknown device (192.168.50.12)", "Unknown device"]


@pytest.mark.skipif(NODE is None, reason="node is required")
def test_schedules_and_other_targets_name_a_device_by_its_title():
    assert run_node(
        NAMES + DEVICES + "let groups = [];\n" + page_function("deviceName")
        + page_function("scheduleTargetName")
        + "console.log(JSON.stringify([deviceName('laptop'), deviceName('spare'),"
        " deviceName('gone'), scheduleTargetName({device_id: 'laptop'})]));\n"
    ) == ["Sam laptop", "Unknown device (192.168.50.12)", "gone", "Sam laptop"]


FAKE_DOM = """
function textNode(tag, text, className) { return {tag: tag, text: text, className: className || ''}; }
function actionButton(label) { return {tag: 'button', text: label}; }
const cards = {children: [], replaceChildren() { this.children = []; },
  append(...nodes) { this.children.push(...nodes); }};
const document = {createElement: () => ({children: [], dataset: {}, append(...nodes) { this.children.push(...nodes); }})};
function element(id) { return id === 'device-cards' ? cards : null; }
const deviceConditions = {devices: {}};
function appliedProfileCell(name) { return [name, 'Saved profile']; }
function profileName(id) { return id; }
function deviceAccessText() { return 'Network access allowed'; }
function conditionCell() { return '—'; }
function deviceConditionCell() { return ['Normal', 'No network effects']; }
function everyoneElseRow() { return null; }
let workspaceDevice = null;
"""


@pytest.mark.skipif(NODE is None, reason="node is required")
def test_each_device_card_shows_the_name_with_the_address_under_it():
    cards = run_node(
        NAMES + DEVICES + FAKE_DOM + file_function(WORKFLOWS, "renderDeviceCards")
        + "renderDeviceCards();\n"
        "console.log(JSON.stringify(cards.children.map((card) => card.children.map("
        "(node) => [node.tag, node.text, node.className]))));\n"
    )
    assert [card[:2] for card in cards] == [
        [["h3", "Sam laptop", ""], ["p", "192.168.50.11", "form-note"]],
        [["h3", "Unknown device", ""], ["p", "192.168.50.12", "form-note"]],
    ]
    assert [card[-1] for card in cards] == [
        ["button", "Open Sam laptop", None],
        ["button", "Open Unknown device (192.168.50.12)", None],
    ]


@pytest.mark.skipif(NODE is None, reason="node is required")
def test_the_site_rule_picker_names_devices_like_every_other_picker():
    options = run_node(
        NAMES + DEVICES
        + "const groups = [{id: 'lab', name: 'Lab'}];\n"
        "class Option { constructor(text, value) { this.text = text; this.value = value; } }\n"
        "const select = {value: '', options: [], replaceChildren(...items) { this.options = items; }};\n"
        "function element() { return select; }\n"
        + file_function(ACCESS, "populateAccessTargets")
        + "populateAccessTargets();\n"
        "console.log(JSON.stringify(select.options.map((option) => [option.text, option.value])));\n"
    )
    assert options == [
        ["Everyone on the test Wi-Fi", "everyone"],
        ["Sam laptop (192.168.50.11)", "device:laptop"],
        ["Unknown device (192.168.50.12)", "device:spare"],
        ["Group: Lab", "group:lab"],
    ]


def test_the_saved_devices_table_titles_each_row_with_the_display_name():
    assert "[deviceDisplayName(device), device.id]," in page_function("renderDeviceTable")


def test_the_device_controls_title_is_the_display_name():
    body = file_function(WORKFLOWS, "renderWorkspaceState")
    assert "element('workspace-title').textContent = deviceDisplayName(device);" in body


def test_delete_questions_name_the_device():
    assert '"Delete " + deviceTitle(device) + "?' in page_function("deleteDevice")
    assert "'Delete ' + deviceTitle(device) + ' and its" in WORKFLOWS


def test_editing_a_device_says_its_name_but_keeps_the_saved_name_in_the_field():
    body = page_function("fillDeviceForm")
    assert 'element("device-name").value = device.name;' in body
    assert 'startEditing("device", deviceTitle(device));' in body


def test_activity_entries_open_a_device_by_its_title():
    assert 'actionButton("Open " + deviceTitle(device),' in page_function("renderActivity")


def test_the_connected_apply_message_names_the_profile_and_the_device():
    body = page_function("applyProfileToDevice")
    assert (
        'showMessage("Applying " + profileName(profileId) + " to " + '
        '(device.display_name || device.ipv4_address) + "...", "working");'
    ) in body


def test_site_rules_and_device_access_rows_use_the_display_name():
    assert "deviceTitle(devices.find(d => d.id === rule.device_id))" in file_function(
        ACCESS, "renderSiteRules"
    )
    assert "[d.mac_address, {...d, name: deviceTitle(d)}]" in file_function(
        ACCESS, "renderAccessDevices"
    )


def test_an_open_test_session_says_which_device_it_tests():
    body = file_function(WORKFLOWS, "openTestSession")
    assert "'Target: ' + (data.session.target_name || data.session.target) + ' · ' +" in body


@pytest.mark.parametrize(
    "old",
    [
        "textNode('h3', device.name)",
        "'Open ' + device.name",
        "'Delete ' + device.name",
        "element('workspace-title').textContent = device.name",
        "new Option(device.name, 'device:' + device.id)",
        "?.name || rule.device_id",
        "[device.name, device.id]",
        '"Open " + device.name',
        '"Delete " + device.name',
        "return device ? device.name : deviceId;",
        '"Applying " + profileId + " to " + device.ipv4_address',
    ],
)
def test_the_raw_saved_names_are_gone(old):
    assert old not in DASHBOARD_HTML


class MainPagePayloadTests(unittest.TestCase):
    LAPTOP_MAC = "aa:aa:aa:aa:aa:11"

    def setUp(self) -> None:
        self.repository = ConfigRepository(":memory:")
        self.addCleanup(self.repository.close)
        self.repository.save_profile(Profile(id="slow", name="Slow", download=Impairment(delay_ms=300)))
        self.repository.save_device(
            Device("laptop", "Client 192.168.50.11", "192.168.50.11", "slow", self.LAPTOP_MAC)
        )
        self.repository.save_connected_device_label(
            mac_address=self.LAPTOP_MAC, display_name="Sam laptop", device_type="laptop"
        )
        self.repository.save_device(Device("spare", "Client 192.168.50.12", "192.168.50.12", "slow"))
        self.repository.save_device_group(DeviceGroup("lab", "Lab", ("spare",)))
        self.repository.save_scenario(Scenario("commute", "Commute", (ScenarioStep("slow", 600),)))
        self.server = ControlServer(("127.0.0.1", 0), self.repository)
        self.addCleanup(self.server.server_close)

    def read(self, path):
        status, _, body = self.server.response_for(path)
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_a_running_scenario_names_its_device_like_the_dashboard(self) -> None:
        now = int(time.time())
        for run in (
            ScenarioRun("run-laptop", "commute", now, device_id="laptop"),
            ScenarioRun("run-spare", "commute", now, device_id="spare"),
            ScenarioRun("run-lab", "commute", now, group_id="lab"),
        ):
            self.repository.start_scenario_run(run)

        runs = {run["id"]: run["target_name"] for run in self.read("/api/scenarios")["runs"]}

        self.assertEqual(
            runs,
            {
                "run-laptop": "Sam laptop",
                "run-spare": "Unknown device (192.168.50.12)",
                "run-lab": "Group Lab",
            },
        )

    def test_a_test_session_and_its_report_name_the_target(self) -> None:
        status, _, body = self.server.write_response_for(
            "/api/test-sessions/start", {"name": "Laptop run", "target": "device:laptop"}
        )
        self.assertEqual(status, 200, body)
        session_id = json.loads(body)["session"]["id"]

        data = self.read("/api/test-sessions/" + session_id)
        listed = self.read("/api/test-sessions")["sessions"][0]

        self.assertEqual(data["session"]["target"], "device:laptop")
        self.assertEqual(data["session"]["target_name"], "Sam laptop (192.168.50.11)")
        self.assertEqual(listed["target_name"], "Sam laptop (192.168.50.11)")
        self.assertIn("\nTarget: Sam laptop (192.168.50.11)\n", data["report"])
        self.assertNotIn("device:laptop", data["report"].split("## Settings")[0])

    def test_other_session_targets_get_plain_names(self) -> None:
        from jittersim.config.workflows import target_title

        self.assertEqual(target_title(self.repository, "device:spare"), "Unknown device (192.168.50.12)")
        self.assertEqual(target_title(self.repository, "group:lab"), "Group Lab")
        self.assertEqual(target_title(self.repository, "everyone-else"), "Everyone else")
        self.assertEqual(target_title(self.repository, "device:gone"), "Deleted device (gone)")
        self.assertEqual(target_title(self.repository, "group:gone"), "Deleted group (gone)")
