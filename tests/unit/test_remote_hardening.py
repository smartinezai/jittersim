"""What a hostile or broken dashboard answer, a pasted secret or a stale certificate can do."""

import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim.cli import main
from jittersim.remote_client import (
    EXPIRED_MESSAGE,
    NOT_YET_VALID_MESSAGE,
    Connection,
    DashboardClient,
    RemoteError,
    certificate_message,
    printable,
    read_config,
    validate_url,
    write_config,
)
from tests.remote_support import (
    PROFILES,
    TEST_TOKEN,
    client,
    make_certificate,
    run,
    serve_status,
)

pytestmark = pytest.mark.usefixtures("remote_environment")

ROOT = Path(__file__).resolve().parents[2]
ESCAPES = ("\x1b[2J", "\x9b2J", "‮", "\x07", "⁦")
HOSTILE = "Lab\x1b[2J\x9b2J‮evil\x07⁦"


def assert_no_escapes(text):
    for escape in ("\x1b", "\x9b", "‮", "\x07", "⁦"):
        assert escape not in text


# one helper for every printed field -------------------------------------------------------


def test_printable_replaces_controls_bidi_and_keeps_letters_and_emoji_joins():
    assert printable("Ärger 🙂‍👍") == "Ärger 🙂‍👍"
    assert printable(HOSTILE) == "Lab?[2J?2J?evil??"
    assert printable("a b") == "a b"


def test_status_never_prints_escapes_from_names(dashboard, capsys):
    hostile_profile = {**PROFILES[0], "name": HOSTILE}
    serve_status(dashboard, general=hostile_profile, active=True,
                 clients=[client(HOSTILE, "02:00:00:00:00:01", assigned_device_id="d",
                                 assigned_profile_id="slow")],
                 lists=[{"name": HOSTILE, "enabled": True}],
                 release={"installed": HOSTILE, "available": None})
    code, out, err, _ = run(dashboard, capsys, "status")
    assert code == 0
    assert_no_escapes(out + err)
    assert "Lab?[2J?2J?evil??" in out


def test_json_escapes_c1_and_bidi_but_keeps_the_values(dashboard, capsys):
    serve_status(dashboard, general={**PROFILES[0], "name": HOSTILE}, active=True,
                 clients=[client("Ärger", "02:00:00:00:00:01", assigned_device_id="d",
                                 assigned_profile_id="slow")])
    code, out, _, _ = run(dashboard, capsys, "status", "--json")
    assert code == 0
    assert_no_escapes(out)
    assert "\\u009b" in out and "\\u202e" in out and "Ärger" in out
    assert json.loads(out)["general_profile"]["name"] == HOSTILE


def test_profiles_traffic_wifi_and_impact_never_print_escapes(dashboard, capsys):
    dashboard.answer("GET", "/api/profiles", {"profiles": [{**PROFILES[0], "name": HOSTILE}]})
    dashboard.answer("GET", "/api/general-profile", {"profile": None, "active": False})
    code, out, err, _ = run(dashboard, capsys, "profiles", "list")
    assert code == 0
    assert_no_escapes(out + err)

    observation = {"domain": "a.example", "query_count": 2, "first_seen": 1, "last_seen": 2,
                   "label": {"name": HOSTILE, "kind": "app", "category": HOSTILE,
                             "known": True},
                   "group": {"key": "label:x", "name": HOSTILE, "kind": "app"},
                   "seen_with": [HOSTILE], "globally_blocked": False}
    dashboard.answer("GET", "/api/observations", {
        "available": True, "total_domains": 1, "devices": [
            {"display_name": HOSTILE, "ipv4_address": "192.168.50.10",
             "domains": [observation, {**observation, "domain": "b.example"}]}]})
    code, out, err, _ = run(dashboard, capsys, "traffic", "--expand")
    assert code == 0
    assert_no_escapes(out + err)

    dashboard.answer("POST", "/api/router/wifi-credentials",
                     {"result": {"ssid": HOSTILE, "password": HOSTILE}})
    code, out, err, _ = run(dashboard, capsys, "wifi", "show", "--password",
                            prompts=(TEST_TOKEN, "router-pass"))
    assert code == 0
    assert_no_escapes(out + err)

    queue = {"device_id": "lab", "direction": "upload", "interface": "eth0", "handle": "11:",
             "kind": "netem", "name": HOSTILE, "packets": 0, "bytes": 0, "drops": 0,
             "overlimits": 0, "requeues": 0, "backlog_bytes": 0, "qlen": 0,
             "generation": "g-1", "impairment": {"loss_percent": 5}}
    answers = iter([
        {"available": True, "sampled_at": 100.0, "queues": [queue], "protected": [HOSTILE]},
        {"available": True, "sampled_at": 101.0, "queues": [{**queue, "packets": 90,
                                                              "drops": 10}],
         "protected": [HOSTILE]},
    ])
    with patch("jittersim.remote_cli.DashboardClient.get", side_effect=lambda path, **_: next(
            answers)), patch("jittersim.remote_cli.time.sleep"):
        code, out, err, _ = run(dashboard, capsys, "impact", "--wait", "1")
    assert code == 0
    assert_no_escapes(out + err)


def test_dashboard_messages_in_errors_never_carry_escapes(dashboard, capsys):
    dashboard.answer("GET", "/api/observations", {"available": False, "message": HOSTILE})
    code, out, err, _ = run(dashboard, capsys, "traffic")
    assert code == 1
    assert_no_escapes(out + err)
    dashboard.answer("GET", "/api/profiles", {"error": HOSTILE}, status=500)
    code, out, err, _ = run(dashboard, capsys, "profiles", "list")
    assert code == 1
    assert_no_escapes(out + err)


# status keeps working without the device list ---------------------------------------------


def test_status_shows_the_rest_when_the_device_list_fails(dashboard, capsys):
    serve_status(dashboard, general=PROFILES[0], active=True)
    dashboard.answer("GET", "/api/connected-devices",
                     {"error": "could not inspect connected clients"}, status=502)
    code, out, _, _ = run(dashboard, capsys, "status")
    assert code == 0
    assert "Connected devices unavailable" in out
    assert "General profile: Slow connection · active" in out
    assert "Running." in out


def test_status_says_when_the_device_list_is_unavailable(dashboard, capsys):
    serve_status(dashboard, available=False,
                 clients=[client("Phone", "02:00:00:00:00:01", assigned_device_id="p",
                                 assigned_profile_id="slow")])
    code, out, _, _ = run(dashboard, capsys, "status", "--json")
    data = json.loads(out)
    assert code == 0
    assert data["devices_summary"] == "Connected devices unavailable"
    assert data["connected_devices_available"] is False


def test_status_still_stops_on_a_refused_token_for_the_device_list(dashboard, capsys):
    serve_status(dashboard)
    dashboard.answer("GET", "/api/connected-devices", {"error": "no"}, status=401)
    code, _, err, _ = run(dashboard, capsys, "status")
    assert code == 1
    assert "refused the admin token" in err


# impact judges the time it measured ------------------------------------------------------


def impact_answers(first_at, second_at, packets=90, drops=10, impairment=None):
    queue = {"device_id": "lab", "direction": "upload", "interface": "eth0", "handle": "11:",
             "kind": "netem", "name": "Lab", "packets": 0, "bytes": 0, "drops": 0,
             "overlimits": 0, "requeues": 0, "backlog_bytes": 0, "qlen": 0,
             "generation": "g-1", "impairment": impairment or {"loss_percent": 5}}
    return iter([
        {"available": True, "sampled_at": first_at, "queues": [queue]},
        {"available": True, "sampled_at": second_at,
         "queues": [{**queue, "packets": packets, "drops": drops}]},
    ])


def run_impact(dashboard, capsys, answers, *arguments):
    with patch("jittersim.remote_cli.DashboardClient.get",
               side_effect=lambda path, **_: next(answers)), \
            patch("jittersim.remote_cli.time.sleep") as sleep:
        result = run(dashboard, capsys, "impact", *arguments)
    return (*result, sleep)


@pytest.mark.parametrize("gap, words", [(10.2, "in the last 10 seconds"),
                                        (1.0, "in the last 1 second")])
def test_impact_words_the_verdict_for_the_measured_time(dashboard, capsys, gap, words):
    code, out, _, _, _ = run_impact(dashboard, capsys, impact_answers(100.0, 100.0 + gap))
    assert code == 0
    assert f"Lab · Upload · Affected {words}" in out
    assert "last minute" not in out
    code, out, _, _, _ = run_impact(dashboard, capsys,
                                    impact_answers(100.0, 100.0 + gap, impairment={
                                        "delay_ms": 50}, drops=0))
    assert f"90 packets passed {words}" in out and "last minute" not in out


def test_impact_json_words_and_numbers(dashboard, capsys):
    code, out, _, _, _ = run_impact(dashboard, capsys, impact_answers(100.0, 110.0, drops=0,
                                                                      packets=100), "--json")
    data = json.loads(out)
    assert code == 0
    assert data["rows"][0]["verdict_text"] == "No drops or cap hit seen"
    assert isinstance(data["rows"][0]["loss_percent"], float)
    assert isinstance(data["window_seconds"], float)


def test_impact_sleeps_the_wait_and_refuses_more_than_fifty_seconds(dashboard, capsys):
    code, _, _, _, sleep = run_impact(dashboard, capsys, impact_answers(100.0, 150.0),
                                      "--wait", "50")
    assert code == 0
    sleep.assert_called_once_with(50)
    with pytest.raises(SystemExit) as stop:
        main(["impact", "--wait", "51"])
    assert stop.value.code == 2


@pytest.mark.parametrize("second_at, reason", [
    (100.0, "clock did not move forward"), (90.0, "clock did not move forward"),
    (161.0, "more than the minute"),
])
def test_impact_says_why_two_samples_cannot_be_compared(dashboard, capsys, second_at, reason):
    code, out, err, _, _ = run_impact(dashboard, capsys, impact_answers(100.0, second_at))
    assert code == 1
    assert reason in err
    assert "Measured over -" not in out and "replaced" not in out + err


def test_a_replaced_queue_is_named_as_the_reason(dashboard, capsys):
    answers = impact_answers(100.0, 110.0)
    first = next(answers)
    second = next(answers)
    second["queues"][0]["generation"] = "g-2"
    code, out, _, _, _ = run_impact(dashboard, capsys, iter([first, second]))
    assert code == 0
    assert "Collecting" in out and "A queue was replaced or changed" in out


# certificates: say what is wrong ----------------------------------------------------------


def certificate_for(directory, san):
    certificate, key = directory / "other.crt", directory / "other.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=other", "-addext", f"subjectAltName={san}",
         "-addext", "extendedKeyUsage=serverAuth", "-keyout", str(key), "-out",
         str(certificate)], check=True, capture_output=True)
    return certificate, key


@contextmanager
def tls_server(certificate, key, respond):
    """A one-thread HTTPS server that answers each request with `respond(connection)`."""

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(certificate), str(key))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    listener.settimeout(0.2)
    stop = threading.Event()
    requests = []

    def serve():
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            try:
                secure = context.wrap_socket(connection, server_side=True)
                secure.settimeout(5)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = secure.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                requests.append(data)
                respond(secure, stop)
            except (OSError, ssl.SSLError):
                pass
            finally:
                connection.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield f"https://127.0.0.1:{listener.getsockname()[1]}", requests
    finally:
        stop.set()
        worker.join(timeout=10)
        listener.close()


def answer_json(body):
    def respond(connection, _stop):
        connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                           b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                           b"Connection: close\r\n\r\n" + body)
    return respond


def test_a_certificate_for_another_address_names_the_address(tmp_path):
    certificate, key = certificate_for(tmp_path, "DNS:pi.example.invalid")
    with tls_server(certificate, key, answer_json(b"{}")) as (url, requests):
        dashboard = DashboardClient(Connection(url, certificate), TEST_TOKEN)
        with pytest.raises(RemoteError) as error:
            dashboard.get("/api/state")
    assert "does not cover 127.0.0.1" in error.value.message
    assert "run the installer on the Pi again" in error.value.message
    assert not any(TEST_TOKEN.encode() in request for request in requests)


def test_an_unknown_certificate_still_names_the_fingerprint(tmp_path):
    trusted, _ = make_certificate(tmp_path, "trusted-only")
    certificate, key = certificate_for(tmp_path, "IP:127.0.0.1")
    with tls_server(certificate, key, answer_json(b"{}")) as (url, requests):
        with pytest.raises(RemoteError) as error:
            DashboardClient(Connection(url, trusted), TEST_TOKEN).get("/api/state")
    assert "SHA-256 fingerprint" in error.value.message
    assert requests == [] or not any(TEST_TOKEN.encode() in item for item in requests)


@pytest.mark.parametrize("code, message", [
    (10, EXPIRED_MESSAGE), (9, NOT_YET_VALID_MESSAGE), (18, "SHA-256 fingerprint"),
    (20, "SHA-256 fingerprint"), (62, "does not cover pi.local"),
    (64, "does not cover pi.local"),
])
def test_each_verify_code_gets_its_sentence(code, message):
    error = ssl.SSLCertVerificationError(1, "certificate verify failed")
    error.verify_code = code
    assert message in certificate_message(error, "pi.local")


def test_only_the_given_certificate_is_trusted_and_old_tls_is_refused(tmp_path):
    certificate, _ = make_certificate(tmp_path)
    dashboard = DashboardClient(Connection("https://127.0.0.1:1", certificate), None)
    assert dashboard.context.cert_store_stats()["x509"] + dashboard.context.cert_store_stats()[
        "x509_ca"] == 1
    assert dashboard.context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert dashboard.context.verify_mode == ssl.CERT_REQUIRED
    assert dashboard.context.check_hostname is True


# bounded answers ---------------------------------------------------------------------------


def endless(status):
    def respond(connection, stop):
        connection.sendall(f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                           "Transfer-Encoding: chunked\r\n\r\n".encode())
        chunk = b"10000\r\n" + b" " * 0x10000 + b"\r\n"
        while not stop.is_set():
            connection.sendall(chunk)
    return respond


def trickle(connection, stop):
    connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                       b"Content-Length: 1000\r\n\r\n")
    for _ in range(1000):
        if stop.is_set():
            return
        connection.sendall(b" ")
        time.sleep(0.2)


def finishes(call, seconds=15):
    outcome = {}

    def target():
        try:
            outcome["value"] = call()
        except RemoteError as error:
            outcome["error"] = error

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), "the client did not stop reading"
    return outcome


@pytest.mark.parametrize("status, message", [
    ("200 OK", "too large"), ("500 Internal Server Error", "HTTP 500"),
])
def test_an_endless_answer_is_cut_off(tmp_path, status, message):
    certificate, key = make_certificate(tmp_path)
    with tls_server(certificate, key, endless(status)) as (url, _):
        dashboard = DashboardClient(Connection(url, certificate), TEST_TOKEN, timeout=5,
                                    max_bytes=256 * 1024)
        outcome = finishes(lambda: dashboard.get("/api/state"))
    assert message in outcome["error"].message


def test_a_trickling_answer_stops_at_the_deadline(tmp_path):
    certificate, key = make_certificate(tmp_path)
    with tls_server(certificate, key, trickle) as (url, _):
        dashboard = DashboardClient(Connection(url, certificate), TEST_TOKEN, timeout=1)
        started = time.monotonic()
        outcome = finishes(lambda: dashboard.get("/api/state"))
    assert "did not finish its answer within 1 seconds" in outcome["error"].message
    assert time.monotonic() - started < 5


def test_deeply_nested_json_is_a_plain_failure(tmp_path):
    certificate, key = make_certificate(tmp_path)
    with tls_server(certificate, key, answer_json(b"[" * 200000)) as (url, _):
        with pytest.raises(RemoteError) as error:
            DashboardClient(Connection(url, certificate), TEST_TOKEN).get("/api/state")
    assert "not valid JSON" in error.value.message


# arguments, addresses, files ----------------------------------------------------------------


@pytest.mark.parametrize("arguments", [
    ["wifi", "show", "--pass=hunter2"], ["status", "--json=hunter2"],
    ["traffic", "--exp=hunter2"], ["traffic", "--expand=hunter2"],
    ["impact", "--wai=hunter2"], ["profiles", "list", "--js=hunter2"],
])
def test_abbreviations_are_off_and_values_are_never_echoed(capsys, arguments):
    with pytest.raises(SystemExit) as stop:
        main(arguments)
    output = capsys.readouterr()
    assert stop.value.code == 2
    assert "hunter2" not in output.out + output.err


@pytest.mark.parametrize("url", ["https://[::1", "https://[::1]x:80", "https://pi:08080",
                                 "https://pi:", "https://pi.local:8080x"])
def test_odd_addresses_are_usage_errors(url):
    with pytest.raises(RemoteError) as error:
        validate_url(url)
    assert error.value.exit_code == 2


@pytest.mark.parametrize("url", ["https://[::1]:8080", "https://Pi.Local:8080",
                                 "https://192.168.0.17"])
def test_plain_addresses_still_work(url):
    assert validate_url(url) == url


def test_a_certificate_file_others_can_change_is_refused(dashboard, capsys, tmp_path):
    serve_status(dashboard)
    loose = tmp_path / "loose.crt"
    loose.write_bytes(dashboard.certificate.read_bytes())
    loose.chmod(0o666)
    with patch("jittersim.remote_client.getpass.getpass", return_value=TEST_TOKEN) as prompt:
        code = main(["status", "--url", dashboard.url, "--ca-cert", str(loose)])
    output = capsys.readouterr()
    assert code == 2
    assert "chmod 644" in output.err
    prompt.assert_not_called()
    assert dashboard.requests == []
    linked = tmp_path / "linked.crt"
    linked.symlink_to(dashboard.certificate)
    with patch("jittersim.remote_client.getpass.getpass", return_value=TEST_TOKEN):
        assert main(["status", "--url", dashboard.url, "--ca-cert", str(linked)]) == 0


def test_a_symlinked_connection_file_is_refused(tmp_path):
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"url": "https://pi:8080", "ca_cert": "/x"}))
    real.chmod(0o600)
    link = tmp_path / "cli.json"
    link.symlink_to(real)
    with pytest.raises(RemoteError):
        read_config(link)


def test_saving_never_follows_a_planted_link(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep me")
    path = tmp_path / "config" / "cli.json"
    path.parent.mkdir()
    (path.parent / "cli.json.tmp").symlink_to(victim)
    certificate, _ = make_certificate(tmp_path)
    with pytest.raises(OSError):
        write_config(path, Connection("https://pi:8080", certificate))
    assert victim.read_text() == "keep me"


# block and unblock say when nothing changed -------------------------------------------------


@pytest.mark.parametrize("word, listed, text", [
    ("unblock", [], "was not on the global block list. Nothing changed."),
    ("block", ["tracker.example"], "is already on the global block list. Nothing changed."),
])
def test_block_and_unblock_without_a_change_post_nothing(dashboard, capsys, word, listed, text):
    dashboard.answer("GET", "/api/policies", {"websites": {"blocked_domains": listed}})
    code, out, _, _ = run(dashboard, capsys, "traffic", word, "tracker.example")
    assert code == 0
    assert f"tracker.example {text}" in out
    assert [method for method, _ in dashboard.paths()] == ["GET"]


# --json failures and the light start --------------------------------------------------------


def test_json_failures_are_json_on_stdout(dashboard, capsys):
    dashboard.answer("GET", "/api/health", {"status": "ok"})
    dashboard.answer("GET", "/api/state", {"error": "no"}, status=401)
    code, out, err, _ = run(dashboard, capsys, "status", "--json")
    assert code == 1
    assert json.loads(out) == {"error": "The dashboard refused the admin token. Check it and "
                                        "try again.", "exit_code": 1}
    assert err == ""


@pytest.mark.parametrize("arguments", [["connect", "--help"], ["traffic", "--help"],
                                       ["wifi", "show", "--help"], ["status", "--json",
                                                                    "--help"]])
def test_mac_commands_never_load_the_pi_networking_code(arguments):
    program = (
        "import sys\n"
        "from jittersim.command import main\n"
        "try:\n"
        f"    main({arguments!r})\n"
        "except SystemExit:\n"
        "    pass\n"
        "loaded = [name for name in sys.modules if name.startswith(('requests', 'urllib3', "
        "'jittersim.networking', 'jittersim.cli'))]\n"
        "print('LOADED', loaded)\n"
    )
    result = subprocess.run([sys.executable, "-W", "default", "-c", program],
                            capture_output=True, text=True, cwd=str(ROOT),
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, timeout=60)
    assert "LOADED []" in result.stdout, result.stdout + result.stderr
    assert "Warning" not in result.stderr


def test_the_command_entry_point_is_the_light_one():
    assert 'jittersim = "jittersim.command:main"' in (ROOT / "pyproject.toml").read_text()


def test_pi_commands_still_reach_the_pi_cli(capsys):
    from jittersim.command import main as command_main

    assert command_main(["--help"]) == 0
    assert "Headless commands on the Pi" in capsys.readouterr().out


def test_an_unknown_status_option_is_never_repeated_even_without_a_connection(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["status", "--tok=hunter2"])
    output = capsys.readouterr()
    assert stop.value.code == 2
    assert "hunter2" not in output.out + output.err


def test_status_help_on_a_mac_explains_both_ways(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["status", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert stop.value.code == 0
    assert "Runs on your Mac" in text and "sudo jittersim status" in text
