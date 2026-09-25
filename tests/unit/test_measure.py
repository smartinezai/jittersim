import io
import json
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jittersim.measure import (
    MeasurementError,
    comparison_table,
    load_run,
    main,
    measure,
    measure_download,
    parse_ping_output,
    ping_command,
    summarize,
)

# Reply 5 is lost, reply 3 arrives after reply 4, and reply 4 comes twice.
LINUX_PING = """PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.
64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=12.0 ms
64 bytes from 1.1.1.1: icmp_seq=2 ttl=57 time=14.0 ms
64 bytes from 1.1.1.1: icmp_seq=4 ttl=57 time=13.0 ms
64 bytes from 1.1.1.1: icmp_seq=3 ttl=57 time=40.0 ms
64 bytes from 1.1.1.1: icmp_seq=4 ttl=57 time=13.2 ms (DUP!)

--- 1.1.1.1 ping statistics ---
5 packets transmitted, 4 received, +1 duplicates, 20% packet loss, time 804ms
rtt min/avg/max/mdev = 12.000/19.750/40.000/11.563 ms
"""
MACOS_PING = """PING 1.1.1.1 (1.1.1.1): 56 data bytes
64 bytes from 1.1.1.1: icmp_seq=0 ttl=57 time=12.345 ms
64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=15.002 ms
Request timeout for icmp_seq 2

--- 1.1.1.1 ping statistics ---
3 packets transmitted, 2 packets received, 33.3% packet loss
round-trip min/avg/max/stddev = 12.345/13.674/15.002/1.329 ms
"""
BUSYBOX_PING = """PING 1.1.1.1 (1.1.1.1): 56 data bytes
64 bytes from 1.1.1.1: seq=0 ttl=57 time=12.345 ms

--- 1.1.1.1 ping statistics ---
2 packets transmitted, 1 packets received, 50% packet loss
"""


class FakePing:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.commands = []

    def __call__(self, command, **options):
        self.commands.append(command)
        return self.result


class MeasurementTests(unittest.TestCase):
    def test_reads_replies_from_linux_macos_and_busybox_ping(self) -> None:
        self.assertEqual(
            parse_ping_output(LINUX_PING),
            [(1, 12.0), (2, 14.0), (4, 13.0), (3, 40.0), (4, 13.2)],
        )
        self.assertEqual(parse_ping_output(MACOS_PING), [(0, 12.345), (1, 15.002)])
        self.assertEqual(parse_ping_output(BUSYBOX_PING), [(0, 12.345)])

    def test_summarizes_loss_delay_jitter_and_reordering(self) -> None:
        summary = summarize(parse_ping_output(LINUX_PING), sent=5)

        self.assertEqual(
            summary,
            {
                "sent": 5,
                "received": 4,
                "loss_percent": 20.0,
                "duplicates": 1,
                "reordered": 1,
                "rtt_min_ms": 12.0,
                "rtt_avg_ms": 19.75,
                "rtt_median_ms": 13.5,
                "rtt_p95_ms": 40.0,
                "rtt_max_ms": 40.0,
                # Mean change between replies in send order: (2 + 26 + 27) / 3.
                "jitter_ms": 18.33,
            },
        )

    def test_a_run_without_replies_loses_everything(self) -> None:
        summary = summarize([], sent=10)

        self.assertEqual(summary["loss_percent"], 100.0)
        self.assertIsNone(summary["rtt_avg_ms"])
        self.assertIsNone(summary["jitter_ms"])

    def test_ping_waits_in_seconds_on_linux_and_milliseconds_on_macos(self) -> None:
        self.assertEqual(
            ping_command("1.1.1.1", count=20, interval=0.2, platform="linux"),
            ["ping", "-n", "-c", "20", "-i", "0.2", "-W", "2", "1.1.1.1"],
        )
        self.assertEqual(
            ping_command("1.1.1.1", count=20, interval=0.5, platform="darwin")[-3:],
            ["-W", "2000", "1.1.1.1"],
        )

    def test_a_measurement_records_samples_and_a_summary(self) -> None:
        ping = FakePing(MACOS_PING, returncode=2)

        run = measure("1.1.1.1", count=3, interval=0.2, label="normal", runner=ping, now=100)

        self.assertEqual(run["format"], "jittersim-measurement")
        self.assertEqual(run["label"], "normal")
        self.assertEqual(run["started_at"], 100)
        self.assertEqual(
            run["samples"],
            [{"sequence": 0, "rtt_ms": 12.345}, {"sequence": 1, "rtt_ms": 15.002}],
        )
        self.assertEqual(run["summary"]["loss_percent"], 33.3)
        self.assertEqual(ping.commands[0][:2], ["ping", "-n"])

    def test_ping_errors_are_reported(self) -> None:
        ping = FakePing("", returncode=2, stderr="ping: cannot resolve example.invalid")

        with self.assertRaisesRegex(MeasurementError, "cannot resolve"):
            measure("example.invalid", count=3, interval=0.2, label="x", runner=ping, now=1)

    def test_measures_download_speed(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "250000")
                self.end_headers()
                self.wfile.write(b"x" * 250_000)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            result = measure_download(f"http://127.0.0.1:{server.server_port}/file")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(result["bytes"], 250_000)
        self.assertGreater(result["mbit_per_s"], 0)

    def test_compares_runs_in_a_markdown_table(self) -> None:
        normal = measure("1.1.1.1", count=3, interval=0.2, label="normal",
                         runner=FakePing(MACOS_PING), now=1)
        impaired = measure("1.1.1.1", count=5, interval=0.2, label="3G",
                           runner=FakePing(LINUX_PING), now=2)
        impaired["download"] = {
            "url": "http://x", "bytes": 1, "seconds": 1, "mbit_per_s": 0.78
        }

        table = comparison_table([normal, impaired])

        self.assertEqual(
            table.splitlines(),
            [
                "| Run | Target | Sent | Loss | RTT avg | RTT p95 | Jitter | Reordered "
                "| Download |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                "| normal | 1.1.1.1 | 3 | 33.3% | 13.67 ms | 15.00 ms | 2.66 ms | 0 | — |",
                "| 3G | 1.1.1.1 | 5 | 20.0% | 19.75 ms | 40.00 ms | 18.33 ms | 1 | 0.78 Mbit/s |",
            ],
        )
        self.assertEqual(
            comparison_table([normal], table_format="csv").splitlines()[1],
            "normal,1.1.1.1,3,33.3%,13.67 ms,15.00 ms,2.66 ms,0,—",
        )

    def test_command_line_measures_and_compares(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "normal.json"
            with redirect_stdout(io.StringIO()) as printed:
                code = main(
                    ["measure", "--label", "normal", "--count", "3", "--output", str(output)],
                    runner=FakePing(MACOS_PING),
                )
            self.assertEqual(code, 0)
            self.assertIn("33.3% loss", printed.getvalue())
            self.assertEqual(load_run(output)["label"], "normal")

            with redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(main(["compare", str(output)]), 0)
            self.assertIn("| normal | 1.1.1.1 | 3 | 33.3% |", printed.getvalue())

    def test_refuses_files_that_are_not_measurements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"format": "jittersim-settings", "version": 1}))

            with self.assertRaisesRegex(MeasurementError, "not a JitterSim measurement"):
                load_run(path)


if __name__ == "__main__":
    unittest.main()
