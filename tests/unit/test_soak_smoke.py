"""The soak harness: a short real run, and its judgement on made-up numbers.

The long run is for the owner (python -m tests.soak.harness --minutes 240). This file proves in
a few seconds that the harness still starts the real dashboard, that every owner action still
works, and that the judgement fails on the growth it is meant to catch.
"""

import sqlite3

from tests.soak import harness, instrument


def _sample(t, **values):
    base = {"t": t, "rss_kb": 60000, "heap_blocks": 200000, "gc_objects": 90000, "fds": 10, "sockets": 3, "threads": 6, "native_threads": 6,
            "db_bytes": 400000, "journal_bytes": 0, "wal_bytes": 0, "state_bytes": 410000,
            "helper_calls": 0, "network_commands": 0, "writes": 0, "blocks_out": 0,
            "cpu_user": t / 100, "cpu_system": 0, "helper_accepted": 5, "helper_closed": 5}
    base.update(values)
    return base


def _judge(samples, results=None):
    return harness.judge(samples, {"sample": samples[-1]}, results or harness.Results(),
                         warmup=10, limits=dict(harness.DEFAULT_LIMITS), seconds=3600)


def test_steady_numbers_pass():
    problems, figures = _judge([_sample(t) for t in range(0, 600, 10)])
    assert problems == []
    assert figures["rss_kb"]["per_hour"] == 0


def test_growth_after_the_warmup_fails():
    samples = [_sample(t, fds=10 + t // 30, rss_kb=60000 + t * 100, threads=6 + t // 60,
                       heap_blocks=200000 + t * 200)
               for t in range(0, 600, 10)]
    problems, _ = _judge(samples)
    text = " ".join(problems)
    assert "open descriptors grew" in text
    assert "resident memory grew" in text
    assert "Python threads grew" in text
    assert "Python heap grew" in text


def test_growth_during_the_warmup_alone_passes():
    samples = [_sample(t, rss_kb=50000 if t < 10 else 60000) for t in range(0, 600, 10)]
    assert _judge(samples)[0] == []


def test_helper_connections_left_open_fail():
    samples = [_sample(t) for t in range(0, 600, 10)]
    samples[-1] = _sample(590, helper_accepted=40, helper_closed=20)
    assert any("helper connections" in problem for problem in _judge(samples)[0])


def test_server_errors_fail_and_busy_reads_are_counted():
    results = harness.Results()
    for _ in range(100):
        results.record("GET", "/api/state", 200, 1.0)
    results.record("GET", "/api/connected-devices", 502, 1.0)
    problems, figures = _judge([_sample(t) for t in range(0, 600, 10)], results)
    assert problems == []
    assert figures["busy_reads"] == {"GET /api/connected-devices": 1}
    results.record("POST", "/api/apply", 500, 1.0, b"boom")
    assert any("server errors" in problem for problem in _judge(
        [_sample(t) for t in range(0, 600, 10)], results)[0])


def test_instrument_books_changed_and_unchanged_commits(tmp_path):
    real_open = open
    instrument.install()
    try:
        before = {(row["kind"], row["task"]): row["count"] for row in instrument.snapshot()}
        connection = sqlite3.connect(str(tmp_path / "x.db"))
        connection.execute("CREATE TABLE t (a)")
        with connection:
            connection.execute("INSERT INTO t VALUES (1)")
        with connection:
            connection.execute("DELETE FROM t WHERE a = 5")
        connection.close()
    finally:
        instrument.uninstall()
    assert open is real_open and sqlite3.connect.__module__ != instrument.__name__
    after = {(row["kind"], row["task"]): row["count"] for row in instrument.snapshot()}
    grew = {key[0] for key in after if after[key] > before.get(key, 0)}
    assert {"db commits changed", "db commits unchanged", "sql write statements"} <= grew


def test_short_real_soak(tmp_path):
    report = harness.run(
        minutes=0.2, browsers=3, out=tmp_path, speed=10, actions_every=0.1, sample_seconds=0.5,
        warmup_seconds=2, page_seconds=2,
        # Seconds of run cannot show a trend, and a loaded CI machine is slow: only the
        # leaks that show at once fail here.
        limits={"rss_mb": 64.0, "latency_factor": 20.0, "latency_slack_ms": 2000.0,
                "busy_share": 0.2, "state_mb": 16.0, "db_mb": 8.0})
    assert report["problems"] == [], harness.summary(report)
    succeeded = {name for name, outcomes in report["actions"].items()
                 if any(outcome == "200" for outcome in outcomes)}
    assert {"save profile", "save device", "apply", "backup", "panic", "import"} <= succeeded, \
        report["actions"]
    assert report["figures"]["requests"] > 100
    assert report["helper_actions"], "the dashboard never reached the helper"
