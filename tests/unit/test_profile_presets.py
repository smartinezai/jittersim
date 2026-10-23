"""The dashboard's profile presets say where their numbers come from.

The presets live in the dashboard script. This file keeps the user guide and the kernel
measurement script in step with them, and pins the numbers that come from a published source.
"""

from dataclasses import asdict
from pathlib import Path

import pytest

from tests.dashboard_script import NODE, constant_source, run_node
from tests.kernel import measure_profiles

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required to read the presets")

USER_GUIDE = (Path(__file__).parents[2] / "docs" / "user-guide.md").read_text(encoding="utf-8")
FIELDS = ("delay_ms", "jitter_ms", "loss_percent", "loss_burst_packets", "bandwidth_kbit")


@pytest.fixture(scope="module")
def presets():
    found = run_node([constant_source("PROFILE_PRESETS")], "PROFILE_PRESETS")
    return {preset["name"]: preset for preset in found}


def _guide_rows():
    section = USER_GUIDE.split("### Profile presets and where they come from", 1)[1]
    section = section.split("\n#", 1)[0]
    lines = [line for line in section.splitlines() if line.startswith("| ")]
    return dict(line.strip("| ").split(" | ", 1) for line in lines[2:])


def test_every_preset_has_a_source_and_the_user_guide_repeats_it(presets):
    assert _guide_rows() == {name: preset["source"] for name, preset in presets.items()}
    for preset in presets.values():
        assert preset["source"].endswith(".")


@pytest.mark.parametrize(
    "name, upload, download",
    [
        # Apple Network Link Conditioner presets as a published table lists them.
        ("DSL", {"delay_ms": 5, "bandwidth_kbit": 256}, {"delay_ms": 5, "bandwidth_kbit": 2000}),
        ("3G", {"delay_ms": 100, "bandwidth_kbit": 330}, {"delay_ms": 100, "bandwidth_kbit": 780}),
        ("EDGE (2G)", {"delay_ms": 440, "bandwidth_kbit": 200},
         {"delay_ms": 400, "bandwidth_kbit": 240}),
        ("Very bad network", {"delay_ms": 500, "loss_percent": 10, "bandwidth_kbit": 1000},
         {"delay_ms": 500, "loss_percent": 10, "bandwidth_kbit": 1000}),
        ("Offline", {"loss_percent": 100}, {"loss_percent": 100}),
        # The targets in the Chrome DevTools source: about 500 kbit/s and a 400 ms round trip.
        ("Slow 3G (Chrome DevTools)", {"delay_ms": 200, "bandwidth_kbit": 500},
         {"delay_ms": 200, "bandwidth_kbit": 500}),
    ],
)
def test_published_numbers_stay_as_published(presets, name, upload, download):
    for published, shown in ((upload, presets[name]["upload"]),
                             (download, presets[name]["download"])):
        assert {key: shown.get(key) for key in published} == published


def test_the_kernel_measurement_script_uses_the_dashboard_numbers(presets):
    measured = [profile for profile in measure_profiles.PROFILES if profile.name in presets]
    assert len(measured) == 4
    for profile in measured:
        for direction in ("upload", "download"):
            made = asdict(getattr(profile, direction))
            shown = presets[profile.name][direction]
            for field in FIELDS:
                # The dashboard leaves a zero out and the kernel script writes bandwidth as None.
                left_out = None if field == "bandwidth_kbit" else 0
                assert made[field] == shown.get(field, left_out), field
