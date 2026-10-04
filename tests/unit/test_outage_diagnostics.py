import json
import logging
import sqlite3
import sys
import threading
import time
from unittest.mock import patch

import pytest

from jittersim import diagnostic_export as export
from jittersim import diagnostic_records as records
from jittersim.diagnostic_privacy import JournalHandler, SafeFormatter
from jittersim.api.wifi_operations import WifiOperations, WifiOutcome


def event(**changes):
    return {"format": 1, "operation": "a" * 32, "source": "helper", "action": "apply",
            "phase": "outcome", "outcome": "succeeded", "elapsed_ms": 3, **changes}


def entry(**changes):
    return {"_BOOT_ID": "b" * 32, "__REALTIME_TIMESTAMP": "12", "__MONOTONIC_TIMESTAMP": "5",
            "_SYSTEMD_UNIT": "jittersim-helper.service", "SYSLOG_IDENTIFIER": "jittersim-diagnostics",
            "JITTERSIM_EVENT": json.dumps(event()), **changes}


@pytest.mark.parametrize("changes", [
    {"request": "secret"}, {"operation": "secret"}, {"source": "secret"}, {"source": []},
    {"action": "secret"}, {"action": []}, {"outcome": ["succeeded"]}, {"elapsed_ms": True},
    {"elapsed_ms": -1}, {"elapsed_ms": 2**53}, {"configuration_version": "secret"}, {"format": True},
])
def test_schema_rejects_arbitrary_fields_and_values(changes):
    assert records.validate_event(event(**changes)) is None


def test_unknown_journal_fields_and_message_text_are_never_exported():
    secret = "fake-password fake-token https://private.example/history 192.168.50.4 aa:bb:cc:dd:ee:ff"
    safe = export.summarize(entry(MESSAGE=secret, PASSWORD=secret, _CMDLINE=secret))
    assert safe["event"] == event()
    assert secret not in json.dumps(safe)
    assert export.summarize(entry(_SYSTEMD_UNIT="unrelated.service")) is None
    assert export.summarize(entry(JITTERSIM_EVENT=json.dumps(event(password=secret)))) is None


def test_network_and_kernel_summaries_omit_all_message_contents():
    secret = "fake-secret https://history.example/device"
    network = export.summarize(entry(_SYSTEMD_UNIT="NetworkManager.service",
                                     MESSAGE="device (wlan0): state change: activated -> disconnected " + secret))
    kernel = export.summarize(entry(_TRANSPORT="kernel", SYSLOG_IDENTIFIER="kernel",
                                    MESSAGE="Out of memory: " + secret))
    assert network["event"] == {"category": "network-state-change"}
    assert kernel["event"] == {"category": "memory-or-kernel-failure"}
    assert secret not in json.dumps([network, kernel])


def test_exception_text_and_string_arguments_never_enter_formatted_logs():
    secret = "fake-password-and-token"
    try:
        raise ValueError(secret)
    except ValueError:
        record = logging.LogRecord("jittersim.networking.helper", logging.ERROR, __file__, 1,
                                   "network helper apply failed: %s", (secret,), sys.exc_info())
    formatted = SafeFormatter().format(record)
    assert "network helper apply failed" in formatted
    assert secret not in formatted
    record.msg, record.args = secret, ()
    assert secret not in SafeFormatter().format(record)


def test_native_packet_terminates_every_field_including_the_event():
    packets = []
    class Sink:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setblocking(self, value): assert value is False
        def sendto(self, raw, address): packets.append(raw)
    with patch.object(records.socket, "socket", return_value=Sink()), \
         patch.object(records, "_clock", return_value=100), patch.object(records, "_window", 100), \
         patch.object(records, "_important_count", 0), patch.object(records, "_dropped", 0):
        records._emit(event())
    packet = packets[0]
    assert packet.endswith(b"\n")
    fields = dict(line.split(b"=", 1) for line in packet.splitlines())
    assert json.loads(fields[b"JITTERSIM_EVENT"]) == event()
    assert len(packet) <= records.MAX_MESSAGE


def test_native_delivery_is_nonblocking_and_contains_no_exception_details():
    calls = []
    class BusySocket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setblocking(self, value): calls.append(value)
        def sendto(self, raw, address):
            assert address == records.JOURNAL_SOCKET
            raise BlockingIOError("fake-secret")
    with patch.object(records.socket, "socket", return_value=BusySocket()), patch.object(records, "_count", 0):
        assert records.send_fields({"MESSAGE": "safe"}) is False
    assert calls == [False]


def test_held_logging_lock_does_not_hold_apply_or_panic():
    @records.recorded("apply", "dashboard")
    def apply(): return 200, "json", b"ok"
    @records.recorded("panic", "dashboard")
    def panic(): return 200, "json", b"ok"
    handler = JournalHandler()
    handler.acquire()
    records._gate.acquire()
    answers = []
    def work():
        answers.extend((apply(), panic()))
        handler.handle(logging.LogRecord("test", logging.ERROR, __file__, 1, "fake-secret", (), None))
    thread = threading.Thread(target=work)
    try:
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert [a[0] for a in answers] == [200, 200]
    finally:
        records._gate.release()
        handler.release()
        thread.join(timeout=1)


def test_logging_failure_does_not_change_return_or_exception():
    emitted = []
    @records.recorded("reset", "helper")
    def failed():
        raise ValueError("fake-secret")
    with patch.object(records, "send_fields", side_effect=OSError("fake-secret")), \
         patch.object(records, "_emit", wraps=records._emit):
        with pytest.raises(ValueError, match="fake-secret"):
            failed()
    @records.recorded("apply", "dashboard")
    def odd(): return "unexpected", "json", b"unchanged"
    with patch.object(records, "_emit", side_effect=lambda e: emitted.append(e)):
        assert odd() == ("unexpected", "json", b"unchanged")
    assert emitted[-1]["outcome"] == "uncertain"


def test_wifi_outcome_is_recorded_without_polling_or_saving_history():
    emitted = []
    operations = WifiOperations(delay=0)
    try:
        with patch.object(records, "_emit", side_effect=lambda e: emitted.append(e)):
            identifier = operations.begin("access-point")
            assert operations.ready(identifier, delayed=False)
            operations.finish(identifier, WifiOutcome(200, "fake-password", ssid="private-name"))
            assert [e["phase"] for e in emitted] == ["start", "outcome"]
            assert emitted[-1]["outcome"] == "succeeded"
            assert "fake-password" not in json.dumps(emitted)
            assert "private-name" not in json.dumps(emitted)
    finally:
        operations.close()


def test_repeated_messages_have_a_fixed_window_limit():
    class Sink:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setblocking(self, value): pass
        def sendto(self, raw, address): pass
    with patch.object(records.socket, "socket", return_value=Sink()), patch.object(records, "_clock", return_value=100), \
         patch.object(records, "_window", 100), patch.object(records, "_count", 0), patch.object(records, "_dropped", 0):
        assert sum(records.send_fields({"MESSAGE": "safe"}) for _ in range(200)) == records.MAX_MESSAGES
        assert records._dropped == 100


def test_root_check_happens_before_private_reads():
    with patch.object(export.os, "geteuid", return_value=1000), patch.object(export, "_actions") as actions:
        with pytest.raises(PermissionError): export.build_export()
        actions.assert_not_called()


def test_actions_use_existing_format_without_request_or_personal_fields(tmp_path):
    database = tmp_path / "history.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE action_history (id INTEGER PRIMARY KEY, happened_at INTEGER, action TEXT, status_code INTEGER)")
        connection.executemany("INSERT INTO action_history VALUES (?,?,?,?)", [(1, 10, "/api/apply", 200),
                                (2, 11, "/api/fake-secret", 500)])
    values, status = export._actions(database, time.monotonic() + 1)
    assert status == "complete"
    assert values == [{"happened_at": 10, "action": "/api/apply", "status": 200}]


def test_export_has_a_strict_output_budget_and_reports_boot_gaps(tmp_path):
    def reader(boot, deadline, accept):
        count = 0
        for _ in range(3000):
            if not accept(export.summarize(entry())): break
            count += 1
        return {"boot_offset": boot, "read": count, "status": "output-limit"}
    with patch.object(export.os, "geteuid", return_value=0):
        raw = export.build_export(tmp_path / "missing", reader=reader)
    assert len(raw) <= export.MAX_BYTES
    assert len(json.loads(raw)["entries"]) < 6000


def _child(output, pause=False):
    return lambda command, **kwargs: subprocess_for_test(output, pause)


def subprocess_for_test(output, pause):
    import subprocess
    return subprocess.Popen([sys.executable, "-c", "import sys,time;sys.stdout.write(" + repr(output) + ");sys.stdout.flush();" + ("time.sleep(10)" if pause else "")],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def test_reader_bounds_oversized_input_and_reaps_the_child():
    children = []
    def popen(*args, **kwargs):
        p = subprocess_for_test("X" * (export.MAX_LINE + 4096), True)
        children.append(p)
        return p
    result = export._journal(0, time.monotonic() + 1, lambda e: True, popen=popen)
    assert result["status"] == "line-limit"
    assert children[0].poll() is not None


def test_reader_deadline_covers_a_stalled_process():
    children = []
    def popen(*args, **kwargs):
        p = subprocess_for_test("", True)
        children.append(p)
        return p
    started = time.monotonic()
    result = export._journal(0, started + .2, lambda e: True, popen=popen)
    assert result["status"] in ("deadline", "cleanup-pending")
    assert time.monotonic() - started < .5
    children[0].wait(timeout=1)


def test_reader_never_accepts_more_than_the_entry_limit():
    line = json.dumps(entry()) + "\n"
    values = []
    result = export._journal(-1, time.monotonic() + 2, lambda e: values.append(e) or True,
                             popen=_child(line * (export.MAX_ENTRIES + 10)))
    assert len(values) == export.MAX_ENTRIES
    assert result["status"] == "entry-limit"


def test_invalid_or_oversized_native_fields_are_rejected_before_encoding():
    with patch.object(records.socket, "socket") as create:
        assert not records.send_fields({"MESSAGE": "X" * 100000})
        assert not records.send_fields({"MESSAGE\nPASSWORD": "fake-secret"})
        assert not records.send_fields({"MESSAGE": ["fake-secret"]})
        create.assert_not_called()


def test_completed_history_storage_failure_does_not_stop_next_recovery():
    from types import SimpleNamespace
    from jittersim.api.server import ControlServer
    from jittersim.config.storage import StorageError
    server = object.__new__(ControlServer)
    calls = []
    def broken(**kwargs):
        calls.append(kwargs)
        raise StorageError("fake-secret")
    server.repository = SimpleNamespace(record_action=broken)
    server.wifi_operations = SimpleNamespace(completed_action=lambda: ("/api/access-point/reset-name", 200))
    server.router_operations = SimpleNamespace(completed_action=lambda: ("/api/router/wifi", 200))
    server._record_wifi_outcome()
    server._record_router_outcome()
    assert len(calls) == 2


def test_non_root_command_cannot_export_private_journals(capsys):
    from jittersim.cli import main
    with patch.object(export.os, "geteuid", return_value=1000), patch.object(export, "_journal") as journal:
        assert main(["diagnostics", "export"]) == 1
        journal.assert_not_called()
    assert "sudo jittersim diagnostics export" in capsys.readouterr().err


def test_scheduler_log_pressure_cannot_use_the_manual_recovery_budget():
    class Sink:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setblocking(self, value): pass
        def sendto(self, raw, address): pass
    with patch.object(records.socket, "socket", return_value=Sink()), patch.object(records, "_clock", return_value=100), \
         patch.object(records, "_window", 100), patch.object(records, "_count", records.MAX_MESSAGES), \
         patch.object(records, "_important_count", 0):
        assert not records.send_fields({"MESSAGE": "safe"})
        assert records.send_fields({"MESSAGE": "safe"}, important=True)
        assert records._important_count == 1


def test_worker_errors_and_uncaught_failures_never_print_private_details(capsys):
    from jittersim.diagnostic_privacy import SafeServerErrors, _unhandled_failure
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter())
    logger = logging.getLogger("jittersim")
    with patch.object(logger, "handlers", [handler]), patch.object(logger, "propagate", False):
        try:
            raise ValueError("fake-password fake-token https://private.example/history")
        except ValueError:
            SafeServerErrors().handle_error(None, ("192.168.50.4", 1234))
            _unhandled_failure(*sys.exc_info())
    text = capsys.readouterr().err
    assert "Request worker failed" in text
    assert "Unexpected service failure" in text
    assert "fake-password" not in text
    assert "192.168.50.4" not in text
    assert "Traceback" not in text


def test_wall_clock_changes_cannot_change_operation_duration():
    emitted = []
    with patch.object(records, "_clock", side_effect=[100.0, 105.0]), \
         patch.object(records.time, "time", side_effect=AssertionError("wall clock is irrelevant")), \
         patch.object(records, "_emit", side_effect=lambda value: emitted.append(value)):
        operation = records.Operation("reset", "helper")
        operation.finish("succeeded")
    assert emitted[-1]["elapsed_ms"] == 5000
    assert emitted[0]["operation"] == emitted[-1]["operation"]
