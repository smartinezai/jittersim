"""jittersim impact takes two samples and judges them with the dashboard rules."""

import json
from unittest.mock import patch

import pytest

from jittersim.cli import main
from tests.remote_support import (
    run,
)

pytestmark = pytest.mark.usefixtures("remote_environment")


# impact ------------------------------------------------------------------------------------


def impact_answer(sampled_at, packets, drops, *, protected=()):
    impairment = {"delay_ms": 100, "jitter_ms": 20, "loss_percent": 10, "bandwidth_kbit": None,
                  "reorder_percent": 0, "duplicate_percent": 0, "corrupt_percent": 0}
    return {
        "available": True, "paused": False, "message": None, "sampled_at": sampled_at,
        "queues": [{"device_id": "phone", "direction": "upload", "interface": "eth0",
                    "handle": "11:", "kind": "netem", "name": "Fixture phone",
                    "packets": packets, "bytes": packets * 1500, "drops": drops,
                    "overlimits": 0, "requeues": 0, "backlog_bytes": 0, "qlen": 0,
                    "generation": "g-1", "impairment": impairment, "kind_label": "device"}],
        "protected": list(protected),
    }


def test_impact_takes_two_samples_and_uses_the_dashboard_words(dashboard, capsys):
    answers = [impact_answer(1000.0, 100, 11), impact_answer(1010.0, 1100, 122,
                                                            protected=["Alice laptop"])]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        dashboard.answer("GET", "/api/impact", answers[1])

    dashboard.answer("GET", "/api/impact", answers[0])
    with patch("jittersim.remote_cli.time.sleep", side_effect=sleep):
        code, out, err, _ = run(dashboard, capsys, "impact", "--wait", "10")
    assert code == 0
    assert sleeps == [10]
    assert dashboard.paths() == [("GET", "/api/impact"), ("GET", "/api/impact")]
    lines = out.splitlines()
    assert lines[0] == "Fixture phone · Upload · Affected in the last minute"
    assert lines[1] == "  Loss: set 10% · actual 10% (111 of 1111 packets dropped)"
    assert lines[2] == "  Delay: set 100 ms ± 20 · configured, not measured"
    assert "Protected (not affected): Alice laptop" in lines
    assert lines[-1].startswith("Measured over 10 seconds.")
    assert "Measuring for 10 seconds" in err


def test_impact_json(dashboard, capsys):
    dashboard.answer("GET", "/api/impact", impact_answer(5.0, 0, 0))

    def sleep(seconds):
        dashboard.answer("GET", "/api/impact", impact_answer(10.0, 49, 0))

    with patch("jittersim.remote_cli.time.sleep", side_effect=sleep):
        code, out, _, _ = run(dashboard, capsys, "impact", "--json", "--wait", "5")
    assert code == 0
    data = json.loads(out)
    assert data["window_seconds"] == 5.0
    row = data["rows"][0]
    assert (row["verdict"], row["verdict_text"]) == ("too-few", "Too few packets to judge")
    assert (row["offered"], row["drops"], row["loss_percent"]) == (49, 0, 0)
    assert data["protected"] == []


def test_the_longest_wait_stays_inside_the_dashboard_window(dashboard, capsys):
    dashboard.answer("GET", "/api/impact", impact_answer(0.0, 0, 0))
    with patch("jittersim.remote_cli.time.sleep") as sleep:
        run(dashboard, capsys, "impact", "--wait", "60")
    sleep.assert_called_once_with(59)


@pytest.mark.parametrize("wait", ["0", "61", "-1", "ten"])
def test_impact_wait_outside_one_to_sixty_is_a_usage_error(capsys, wait):
    with pytest.raises(SystemExit) as stop:
        main(["impact", "--wait", wait])
    assert stop.value.code == 2


def test_impact_unavailable_shows_the_dashboard_message(dashboard, capsys):
    dashboard.answer("GET", "/api/impact", {
        "available": False, "paused": False, "sampled_at": 1.0, "queues": [], "protected": [],
        "message": "Network is reset. Choose Start with saved settings to start impact checks."})
    with patch("jittersim.remote_cli.time.sleep") as sleep:
        code, _, err, _ = run(dashboard, capsys, "impact")
    assert code == 1
    sleep.assert_not_called()
    assert "Network is reset." in err


def test_impact_with_nothing_slowed_down(dashboard, capsys):
    empty = {"available": True, "paused": False, "message": None, "sampled_at": 1.0,
             "queues": [], "protected": []}
    dashboard.answer("GET", "/api/impact", empty)
    with patch("jittersim.remote_cli.time.sleep"):
        code, out, _, _ = run(dashboard, capsys, "impact", "--wait", "1")
    assert code == 0
    assert out.startswith("Nothing is slowed down right now.")
