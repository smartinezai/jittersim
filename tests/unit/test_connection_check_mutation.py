"""Behavioral contracts for real survivors of the PR161 mutation run."""

import csv
import io
import json

import pytest

from jittersim import connection_check as check


def test_statistics_keep_fractional_rtts_and_do_not_bridge_a_lost_probe():
    stats = check.probe_statistics([
        {"rtt_ms": 1.234, "copies": 1, "arrival": 0},
        {"rtt_ms": 3.456, "copies": 2, "arrival": 2},
        {"rtt_ms": None, "copies": 0, "arrival": None},
        {"rtt_ms": 9.876, "copies": 3, "arrival": 1},
    ])
    # Sorted RTTs: 1.234, 3.456, 9.876; nearest-rank p95 is the maximum.
    # Only the first two sends are adjacent answers.
    assert stats == {
        "sent": 4, "received": 3, "lost": 1, "answered_twice": 2,
        "extra_copies": 3, "reordered": 1, "rtt_min_ms": 1.23,
        "rtt_mean_ms": 4.86, "rtt_median_ms": 3.46, "rtt_p95_ms": 9.88,
        "rtt_max_ms": 9.88, "jitter_ms": 2.22, "jitter_pairs": 1,
    }


def test_one_probe_still_has_a_finite_wilson_interval():
    assert check.wilson_interval(0, 1, 1.96) == pytest.approx((0, 0.7934567))
    assert check.wilson_interval(1, 1, 1.96) == pytest.approx((0.2065433, 1))


@pytest.mark.parametrize("share, burst, expected", [
    (0.1, 2, 2.6), (0.0, 2, 1.0), (1.0, 2, 1.0), (0.9, 2, 1.0),
])
def test_burst_variance_at_two_packets_and_degenerate_loss(share, burst, expected):
    assert check.burst_inflation(share, burst) == pytest.approx(expected)


def test_zero_delay_range_starts_at_zero_and_minimum_probe_bandwidth_is_allowed():
    assert check.expected_delay_range(0, 0, None) == (0, 30)
    assert check.window_limit(0, 0, check.MIN_JUDGED_BANDWIDTH_KBIT) is None


@pytest.mark.parametrize("count", [0, 1])
def test_timing_without_a_pair_has_zero_jitter(count):
    assert check.simulated_timing(10, 0, 0, count=count) == {
        "jitter_ms": 0.0, "reorder_share": 0.0,
    }


def test_a_nonwaiting_prober_keeps_the_send_and_grace_pace():
    clock = [10.0]
    sleeps = []

    class Prober:
        sent = 0

        def send(self):
            self.sent += 1

        def collect(self, until):
            pass

        def counts(self):
            return self.sent, 0

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    assert check.run_probe_burst(
        Prober(), 2, interval=0.25, grace=0.5,
        clock=lambda: clock[0], sleep=sleep,
    )
    assert sleeps == [0.25, 0.25, 0.25, 0.25]
    assert clock[0] == 11.0


def test_export_keeps_each_measurement_and_profile_setting_in_its_own_column():
    result = {
        "id": "check-one", "started_at": 100, "finished_at": 120,
        "device_name": "Phone", "mac_address": "02:00:00:00:00:01", "label": "Before",
        "status": "complete", "source": "device", "protected": False,
        "profile": {"profile_name": "Slow"},
        "summary": {
            "expected": {"loss_percent": 3, "delay_ms": 40, "jitter_ms": 7,
                         "duplicate_percent": 8, "reorder_percent": 9,
                         "corrupt_percent": 2, "bandwidth_kbit": 512, "loss_burst_packets": 4},
            "probes": {"planned": 100, "sent": 99, "received": 96, "loss_percent": 3.03,
                       "answered_twice": 5, "reordered": 6},
            "rtt": {"rtt_min_ms": 31.11, "rtt_mean_ms": 40.22, "rtt_median_ms": 39.33,
                    "rtt_p95_ms": 46.44, "rtt_max_ms": 49.55, "jitter_ms": 6.66},
            "verdict": "borderline",
            "metrics": [{"id": "loss", "status": "matches"},
                        {"id": "delay", "status": "more"},
                        {"id": "jitter", "status": "less"},
                        {"id": "duplicate", "status": "inconclusive"},
                        {"id": "reorder", "status": "borderline"}],
        },
    }
    expected = {
        "id": "check-one", "started_at": 100, "finished_at": 120,
        "device_name": "Phone", "mac_address": "02:00:00:00:00:01", "label": "Before",
        "status": "complete", "source": "device", "protected": False, "profile_name": "Slow",
        "set_loss_percent": 3, "set_delay_ms": 40, "set_jitter_ms": 7,
        "set_duplicate_percent": 8, "set_reorder_percent": 9, "set_corrupt_percent": 2,
        "set_bandwidth_kbit": 512, "set_burst_packets": 4, "probes_planned": 100,
        "probes_sent": 99, "probes_received": 96, "loss_percent": 3.03,
        "answered_twice": 5, "reordered": 6, "rtt_min_ms": 31.11, "rtt_mean_ms": 40.22,
        "rtt_median_ms": 39.33, "rtt_p95_ms": 46.44, "rtt_max_ms": 49.55, "jitter_ms": 6.66,
        "verdict": "borderline", "loss_status": "matches", "delay_status": "more",
        "jitter_status": "less", "duplicate_status": "inconclusive", "reorder_status": "borderline",
    }
    assert check.check_export_row(result) == expected
    assert list(csv.DictReader(io.StringIO(check.check_export_csv([result])))) == [
        {key: str(value) for key, value in expected.items()}
    ]
    assert json.loads(check.check_export_json([result])) == {
        "format": "jittersim-connection-checks", "version": 1, "results": [result],
    }


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_neutralizes_each_formula_prefix(prefix):
    rows = list(csv.DictReader(io.StringIO(check.check_export_csv([{"label": prefix + "1"}]))))
    assert rows[0]["label"] == "'" + prefix + "1"
    assert rows[0]["profile_name"] == ""


@pytest.mark.parametrize("label, expected", [
    ("\r1", "'\r1"), ("before\rafter", "before\rafter"),
    ("before\r\nafter", "before\r\nafter"),
])
def test_csv_preserves_carriage_returns_inside_one_quoted_record(label, expected):
    exported = check.check_export_csv([{"label": label}])
    rows = list(csv.DictReader(io.StringIO(exported, newline="")))
    assert len(rows) == 1
    assert rows[0]["label"] == expected
    assert rows[0]["profile_name"] == ""


def test_summary_carries_probe_numbers_expected_settings_and_runtime_metadata():
    setting = {"loss_percent": 10, "delay_ms": 40, "jitter_ms": 7,
               "duplicate_percent": 8, "reorder_percent": 9, "corrupt_percent": 2,
               "bandwidth_kbit": 512, "loss_burst_packets": 4}
    result = check.build_summary(
        [{"rtt_ms": 41.234, "copies": 2, "arrival": 0},
         {"rtt_ms": None, "copies": 0, "arrival": None}],
        planned=100, source="device", impairment=setting, burst_packets=4,
        seconds=12.5, grace=3.5, profile_unchanged=True,
    )
    assert result["probes"] == {
        "planned": 100, "sent": 2, "received": 1, "lost": 1, "loss_percent": 50.0,
        "answered_twice": 1, "extra_copies": 1, "reordered": 0,
    }
    assert result["rtt"] == {
        "rtt_min_ms": 41.23, "rtt_mean_ms": 41.23, "rtt_median_ms": 41.23,
        "rtt_p95_ms": 41.23, "rtt_max_ms": 41.23, "jitter_ms": None,
    }
    assert result["expected"] == setting
    assert result["seconds"] == 12.5
    assert result["grace_seconds"] == 3.5
    assert result["profile_unchanged"] is True
    assert result["burst_packets"] == 4
    assert result["probe_status"] == "measured"
    loss = next(row for row in result["metrics"] if row["id"] == "loss")
    assert loss["label"] == "Packet loss"
    assert loss["set"] == "10%, in bursts of about 4"
    assert loss["measured"] == "50% (1 of 2 lost, 95% range 2 to 98%)"


def test_json_export_refuses_nonfinite_measurements():
    with pytest.raises(ValueError):
        check.check_export_json([{"summary": {"rtt": {"rtt_mean_ms": float("nan")}}}])
