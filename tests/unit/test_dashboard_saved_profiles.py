"""Run the dashboard's profile summary and profile form functions in node."""

import json
import re
import shutil
import subprocess
from dataclasses import asdict
from html.parser import HTMLParser

import pytest

from jittersim.api.server import DASHBOARD_HTML
from jittersim.config.models import Impairment, Profile, Randomness

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
HIDDEN_FIELDS = {
    "reroll_interval_randomness",
    "outage_duration_randomness",
    "outage_interval_randomness",
    "website_randomness",
}
FEATURES = ("delay", "jitter", "loss", "bandwidth", "reorder", "duplicate", "corrupt")


def served_helpers():
    start = SCRIPT.index("function optionalNumber(")
    return SCRIPT[start : SCRIPT.index("function timeToMinute(", start)]


def served_profile_functions():
    start = SCRIPT.index("function profileSummary(")
    return SCRIPT[start : SCRIPT.index('element("profile-form").addEventListener("submit"', start)]


class DefaultsParser(HTMLParser):
    """Collect the value every profile form field has after form.reset()."""

    def __init__(self):
        super().__init__()
        self.values = {}
        self.select = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and "id" in attributes:
            self.values[attributes["id"]] = attributes.get("value", "")
        elif tag == "select" and "id" in attributes:
            self.select = attributes["id"]
        elif tag == "option" and self.select and self.select not in self.values:
            self.values[self.select] = attributes.get("value", "")

    def handle_endtag(self, tag):
        if tag == "select":
            self.select = None


def form_defaults():
    form = re.search(r'<form id="profile-form">(.*?)</form>', DASHBOARD_HTML, re.DOTALL).group(1)
    parser = DefaultsParser()
    parser.feed(form)
    return parser.values


def run_node(body):
    program = (
        "const store = {};\n"
        "const element = (id) => (store[id] = store[id] || {value: \"\"});\n"
        + served_helpers()
        + "\n"
        + served_profile_functions()
        + "\nObject.entries("
        + json.dumps(form_defaults())
        + ").forEach(([id, value]) => { element(id).value = value; });\n"
        + body
    )
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def direction(**values):
    return dict(
        {"delay_ms": 0, "jitter_ms": 0, "loss_percent": 0, "bandwidth_kbit": None}, **values
    )


SUMMARIES = {
    "delay_jitter_loss_and_mbit": (
        direction(delay_ms=50, jitter_ms=10, loss_percent=2, bandwidth_kbit=5000),
        direction(delay_ms=50, jitter_ms=10, loss_percent=2, bandwidth_kbit=5000),
        "↕ 50 ms ±10 · 2% loss · 5 Mbit/s",
    ),
    "unequal_directions_use_two_arrows": (
        direction(delay_ms=50, jitter_ms=10, loss_percent=2, bandwidth_kbit=5000),
        direction(delay_ms=20),
        "↑ 50 ms ±10 · 2% loss · 5 Mbit/s · ↓ 20 ms",
    ),
    "one_and_a_half_mbit": (
        direction(bandwidth_kbit=1500), direction(bandwidth_kbit=1500), "↕ 1.5 Mbit/s",
    ),
    "exactly_one_mbit": (direction(bandwidth_kbit=1000), direction(bandwidth_kbit=1000), "↕ 1 Mbit/s"),
    "kbit_below_one_mbit": (
        direction(bandwidth_kbit=800), direction(bandwidth_kbit=800), "↕ 800 kbit/s",
    ),
    "kbit_just_below_one_mbit": (
        direction(bandwidth_kbit=999), direction(bandwidth_kbit=999), "↕ 999 kbit/s",
    ),
    "all_zero_direction": (direction(), direction(), "↕ no impairment"),
    "one_direction_is_clean": (
        direction(loss_percent=3), direction(), "↑ 3% loss · ↓ no impairment",
    ),
    "delay_without_jitter": (direction(delay_ms=20), direction(delay_ms=20), "↕ 20 ms"),
    "jitter_without_delay": (direction(jitter_ms=5), direction(jitter_ms=5), "↕ 0 ms ±5"),
}


@NEEDS_NODE
@pytest.mark.parametrize("name", SUMMARIES)
def test_profile_summary(name):
    upload, download, expected = SUMMARIES[name]
    body = (
        "const profile = " + json.dumps({"upload": upload, "download": download}) + ";\n"
        "console.log(JSON.stringify(profileSummary(profile)));\n"
    )
    assert run_node(body) == expected


def every_field_set_profile():
    def impairment(offset, bandwidth):
        values = {
            "delay_ms": 20 + offset,
            "jitter_ms": 3 + offset,
            "loss_percent": 4 + offset,
            "bandwidth_kbit": bandwidth,
            "reorder_percent": 5 + offset,
            "duplicate_percent": 6 + offset,
            "corrupt_percent": 7 + offset,
        }
        for index, feature in enumerate(FEATURES):
            values[feature + "_randomness"] = Randomness(
                variation_percent=10 + offset + index, enabled_probability=90 - offset - index
            )
        return Impairment(**values)

    return Profile(
        id="every-field",
        name="Every field",
        upload=impairment(0, 2500),
        download=impairment(30, None),
        random_seed=99,
        reroll_interval_seconds=45,
        reroll_interval_randomness=Randomness(variation_percent=7, enabled_probability=80),
        outage_probability=12,
        outage_duration_seconds=33,
        outage_duration_randomness=Randomness(variation_percent=11, enabled_probability=60),
        outage_interval_seconds=77,
        outage_interval_randomness=Randomness(variation_percent=11, enabled_probability=60),
        website_mode="allow_only",
        website_domains=("a.example.com", "b.example.com"),
        website_randomness=Randomness(variation_percent=13, enabled_probability=40),
    )


def as_json(profile):
    return json.loads(json.dumps(asdict(profile)))


def test_the_round_trip_profile_sets_every_field_to_a_non_default_value():
    profile = as_json(every_field_set_profile())
    default = as_json(Profile(id="every-field", name="Every field"))
    default_impairment = as_json(Profile(id="x", name="x"))["upload"]
    assert set(profile) == set(default)
    for key in profile:
        if key in ("id", "name", "upload", "download"):
            continue
        assert profile[key] != default[key], key
    for side in ("upload", "download"):
        for key in default_impairment:
            if side == "download" and key == "bandwidth_kbit":
                continue
            assert profile[side][key] != default_impairment[key], (side, key)
    assert profile["download"]["bandwidth_kbit"] is None


@NEEDS_NODE
def test_fill_then_read_returns_the_original_profile():
    profile = as_json(every_field_set_profile())
    body = (
        "const profile = " + json.dumps(profile) + ";\n"
        "fillProfileForm(profile);\n"
        "console.log(JSON.stringify(profileFormPayload(profile)));\n"
    )
    assert run_node(body) == profile


@NEEDS_NODE
def test_without_a_base_only_the_four_hidden_fields_change():
    profile = as_json(every_field_set_profile())
    body = (
        "const profile = " + json.dumps(profile) + ";\n"
        "fillProfileForm(profile);\n"
        "console.log(JSON.stringify(profileFormPayload(null)));\n"
    )
    payload = run_node(body)
    assert {key for key in profile if payload[key] != profile[key]} == HIDDEN_FIELDS
    assert set(payload) == set(profile)
    assert payload["reroll_interval_randomness"] == {
        "variation_percent": 7, "enabled_probability": 100,
    }
    assert payload["outage_duration_randomness"] == {
        "variation_percent": 0, "enabled_probability": 100,
    }
    assert payload["outage_interval_randomness"] == {
        "variation_percent": 0, "enabled_probability": 100,
    }
    assert payload["website_randomness"] == {"variation_percent": 0, "enabled_probability": 40}


@NEEDS_NODE
def test_a_new_profile_keeps_the_hard_coded_hidden_defaults():
    body = (
        'element("profile-id").value = "fresh";\n'
        'element("profile-name").value = "Fresh";\n'
        "console.log(JSON.stringify(profileFormPayload(null)));\n"
    )
    payload = run_node(body)
    assert payload == as_json(Profile(id="fresh", name="Fresh"))
    assert payload["reroll_interval_randomness"] == {
        "variation_percent": 0, "enabled_probability": 100,
    }
    assert payload["website_randomness"] == {"variation_percent": 0, "enabled_probability": 100}


@NEEDS_NODE
def test_filling_a_profile_with_no_seed_or_bandwidth_clears_those_fields():
    profile = as_json(every_field_set_profile())
    profile["random_seed"] = None
    profile["upload"]["bandwidth_kbit"] = None
    body = (
        "const profile = " + json.dumps(profile) + ";\n"
        'element("profile-seed").value = "5";\n'
        'element("upload-bandwidth").value = "9";\n'
        "fillProfileForm(profile);\n"
        "console.log(JSON.stringify([element(\"profile-seed\").value,"
        ' element("upload-bandwidth").value, profileFormPayload(profile)]));\n'
    )
    seed, bandwidth, payload = run_node(body)
    assert (seed, bandwidth) == ("", "")
    assert payload == profile


def test_the_submit_handler_builds_its_body_with_the_payload_function():
    submit = SCRIPT[SCRIPT.index('element("profile-form").addEventListener("submit"') :]
    submit = submit[: submit.index('element("device-form").addEventListener("submit"')]
    assert "profileFormPayload(editingProfile)" in submit
