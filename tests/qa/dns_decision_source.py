"""Synthetic loopback-only Unbound proof, with no raw output retained."""
import json
import fcntl
import signal
import os
import select
import socket
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path


def name(value):
    return b''.join(bytes([len(part)]) + part.encode() for part in value.split('.')) + b'\0'


def upstream(stop, server):
    server.settimeout(.05)
    while not stop.is_set():
        try:
            message, peer = server.recvfrom(4096)
        except socket.timeout:
            continue
        offset = 12
        labels = []
        while message[offset]:
            size = message[offset]
            labels.append(message[offset + 1:offset + 1 + size].decode())
            offset += size + 1
        question = message[12:offset + 5]
        qname = '.'.join(labels)
        if qname == 'answer.example':
            response = (message[:2] + struct.pack('!HHHHH', 0x8180, 1, 1, 0, 0)
                        + question + b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 60, 4)
                        + socket.inet_aton('203.0.113.5'))
        else:
            soa = name('ns.example') + name('hostmaster.example') + struct.pack('!IIIII', 1, 60, 60, 60, 60)
            response = (message[:2] + struct.pack('!HHHHH', 0x8183, 1, 0, 1, 0)
                        + question + name('example') + struct.pack('!HHIH', 6, 1, 60, len(soa)) + soa)
        server.sendto(response, peer)


def query(domain, transport='udp', source='127.0.0.1', qid=1234):
    data = struct.pack('!HHHHHH', qid, 0x0100, 1, 0, 0, 0) + name(domain) + struct.pack('!HH', 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM if transport == 'udp' else socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.bind((source, 0))
        if transport == 'udp':
            client.sendto(data, ('127.0.0.1', 10535))
            reply = client.recv(4096)
        else:
            client.connect(('127.0.0.1', 10535))
            client.sendall(struct.pack('!H', len(data)) + data)
            size = struct.unpack('!H', client.recv(2))[0]
            reply = b''
            while len(reply) < size:
                reply += client.recv(size - len(reply))
        return struct.unpack_from('!H', reply, 2)[0] & 15


with tempfile.TemporaryDirectory() as directory:
    config = Path(directory) / 'unbound.conf'
    config.write_text('''server:
  interface: 127.0.0.1@10535
  username: ""
  chroot: ""
  directory: "/"
  pidfile: ""
  do-daemonize: no
  use-syslog: no
  logfile: ""
  verbosity: 0
  log-identity: "jittersim-dns-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  log-queries: no
  log-replies: yes
  log-tag-queryreply: yes
  log-local-actions: yes
  do-ip6: no
  do-not-query-localhost: no
  module-config: "iterator"
  num-threads: 1
  access-control: 127.0.0.0/8 allow
  local-zone: "blocked.example." always_nxdomain
  local-zone: "example." nodefault
forward-zone:
  name: "."
  forward-addr: 127.0.0.1@10536
''')
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(('127.0.0.1', 10536))
    stop = threading.Event()
    worker = threading.Thread(target=upstream, args=(stop, server), daemon=True)
    worker.start()
    read_fd, write_fd = os.pipe2(os.O_NONBLOCK)
    process = subprocess.Popen(['/usr/sbin/unbound', '-d', '-c', str(config)],
                               stderr=write_fd, stdout=subprocess.DEVNULL)
    os.close(write_fd)
    try:
        deadline = time.monotonic() + 3
        while True:
            try:
                assert query('blocked.example') == 3
                break
            except (OSError, AssertionError):
                if time.monotonic() >= deadline:
                    raise RuntimeError('isolated resolver did not start')
                time.sleep(.02)
        assert query('answer.example') == 0
        assert query('answer.example', 'tcp') == 0
        assert query('missing.example') == 3
        assert query('private.blocked.example', source='127.0.0.2') == 3
        # Give the single resolver thread time to finish writing the last reply.
        time.sleep(.05)
        events = []
        raw = b''
        for _ in range(8):
            if not select.select([read_fd], [], [], .02)[0]:
                break
            raw += os.read(read_fd, 4096)
            assert len(raw) <= 8192
        lines = raw.decode().splitlines()
        # Protected identity is excluded before retaining event data.
        public = [line for line in lines if '127.0.0.2' not in line]
        blocks = [line for line in public if ' always_nxdomain ' in line]
        replies = [line for line in public if '] reply:' in line]
        assert len(blocks) == 1
        assert any('answer.example. A IN NOERROR' in line for line in replies)
        assert any('missing.example. A IN NXDOMAIN' in line for line in replies)
        assert not any('missing.example' in line for line in blocks)
        assert any('answer.example. A IN NOERROR' in line and ' 1 ' in line for line in replies)
        # Feed the ACTUAL captured bytes through the shipped parser and RAM store.
        # This fixture owns its loopback clients. Production identity admission still
        # refuses loopback and is tested separately with real helper-shaped rows.
        from jittersim.config.dns_outcomes import DnsOutcomeStore
        from jittersim.networking.dns_decisions import DecisionLines
        from jittersim.networking.helper import _merge_connected_device_output
        parsed = DecisionLines().feed(raw)
        assert parsed and all(item.generation == 'a' * 64 for item in parsed)
        helper_rows = _merge_connected_device_output(
            "Station 02:00:00:00:00:01 (on wlan0)\n",
            "127.0.0.1 dev wlan0 lladdr 02:00:00:00:00:01 REACHABLE\n",
            "", observed_at=int(time.time()))
        assert helper_rows[0]['neighbor_state'] == 'reachable'
        fixture_identities = {row['ipv4_address']: row['mac_address'] for row in helper_rows}
        store = DnsOutcomeStore()
        window_start = min(item.event.observed_at for item in parsed)
        window_end = max(item.event.observed_at for item in parsed)
        store.begin(epoch='e' * 32, session='f' * 32, generation='a' * 64,
                    producer_started_ns=time.monotonic_ns(), version=0,
                    identities=fixture_identities, now=window_start)
        store.add([item.event for item in parsed], epoch='e' * 32, session='f' * 32,
                  generation='a' * 64, version=0, identities=fixture_identities, now=window_end)
        counted, health = store.snapshot(version=0, identities=fixture_identities, now=window_end)
        assert counted[('127.0.0.1', '02:00:00:00:00:01', 'blocked.example')]['blocked_decisions'] == 1
        assert counted[('127.0.0.1', '02:00:00:00:00:01', 'answer.example')]['answered_noerror'] == 2
        assert counted[('127.0.0.1', '02:00:00:00:00:01', 'missing.example')]['other_dns_errors'] == 1
        assert not any(key[0] == '127.0.0.2' for key in counted)
        assert health['coverage_complete'] is False and health['dropped_events'] is None
        def nonblocking_child_stderr():
            content = Path(f'/proc/{process.pid}/fdinfo/2').read_text()
            flags = next(line.split()[1] for line in content.splitlines() if line.startswith('flags:'))
            return bool(int(flags, 8) & os.O_NONBLOCK)

        assert nonblocking_child_stderr()
        # The reader stalls while the kernel pipe reaches capacity. DNS still answers.
        capacity = fcntl.fcntl(read_fd, fcntl.F_GETPIPE_SZ)
        started = time.monotonic()
        for index in range(500):
            assert query('blocked.example', qid=index) == 3
        assert time.monotonic() - started < 5
        queued = len(os.read(read_fd, capacity))
        assert queued > capacity // 2
        # A separate collector process may disappear. Unbound remains independent.
        reader = subprocess.Popen(['python3', '-c', 'import time; time.sleep(30)'],
                                  pass_fds=(read_fd,), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        reader.kill()
        reader.wait(timeout=2)
        assert query('blocked.example') == 3
        config.write_text(config.read_text().replace('jittersim-dns-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                                                     'jittersim-dns-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'))
        process.send_signal(signal.SIGHUP)
        time.sleep(.1)
        assert query('blocked.example') == 3
        assert nonblocking_child_stderr()
        reload_stream = b''
        for _ in range(8):
            if not select.select([read_fd], [], [], .02)[0]:
                break
            reload_stream += os.read(read_fd, 4096)
            assert len(reload_stream) <= 32768
        assert b'jittersim-dns-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb[' in reload_stream
        # No read descriptor survives this close. SIGPIPE must not kill the resolver.
        os.close(read_fd)
        read_fd = None
        for _ in range(5):
            assert query('blocked.example') == 3
        assert process.poll() is None
        print(json.dumps({'actual_unbound': subprocess.check_output(['/usr/sbin/unbound', '-V'], text=True).splitlines()[0], 'udp_policy_block_action': len(blocks),
                          'dns_noerror_udp_tcp_and_cache': 'passed',
                          'ordinary_nxdomain_not_policy_block': 'passed',
                          'protected_raw_stream_presence': any('127.0.0.2' in line for line in lines),
                          'protected_events_retained': 0, 'nonblocking_pipe': True,
                          'raw_lines_retained_on_disk': False,
                          'actual_bytes_real_parser_ram_counter_pipeline': 'passed',
                          'saturated_pipe_queries_answered': 500,
                          'sender_loss_count': None,
                          'collector_kill_does_not_stop_dns': True,
                          'nonblocking_across_exec_and_reload': True,
                          'reload_generation_in_event_identity': True,
                          'closed_reader_sigpipe_resolver_alive': True}))
    finally:
        process.terminate()
        process.wait(timeout=3)
        if read_fd is not None:
            os.close(read_fd)
        stop.set()
        worker.join(timeout=1)
        server.close()

# Exercise the actual supervisor and authenticated Linux receiver, rather than
# a sleeping stand-in. Loopback queries are intentionally unknown client identity.
from jittersim.config.storage import ConfigRepository
from jittersim.networking.dns_decision_ipc import DecisionReceiver

with tempfile.TemporaryDirectory() as directory, ConfigRepository(":memory:") as repository:
    root = Path(directory)
    private = root / 'private'
    private.mkdir(mode=0o700)
    pid_path = root / 'producer.json'
    socket_path = private / 'events.sock'
    receiver = DecisionReceiver(repository, socket_path, pid_path, trusted_uid=os.getuid())
    config = root / 'unbound.conf'
    generation = repository.access_plan().digest()
    config.write_text(f'''server:
  interface: 127.0.0.1@10535
  username: ""
  chroot: ""
  directory: "/"
  pidfile: ""
  do-daemonize: no
  use-syslog: no
  logfile: ""
  verbosity: 0
  log-queries: no
  log-replies: yes
  log-tag-queryreply: yes
  log-local-actions: yes
  log-identity: "jittersim-dns-{generation}"
  do-ip6: no
  module-config: "iterator"
  access-control: 127.0.0.0/8 allow
  local-zone: "blocked.example." always_nxdomain
''')
    command = [
        'python3', '-c',
        'import sys; from jittersim.networking.dns_decisions import supervise_resolver; '
        'raise SystemExit(supervise_resolver(*sys.argv[1:], expected_socket_uid=0))',
        str(config), str(socket_path), str(pid_path),
    ]
    wrapper = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    fresh = {'association_status_available': True, 'devices': [{
        'ipv4_address': '192.168.50.10', 'mac_address': '02:00:00:00:00:01',
        'association_verified': True, 'neighbor_state': 'reachable',
    }]}
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                assert query('blocked.example') == 3
                receiver.poll(fresh, int(time.time()))
                if receiver.active:
                    break
            except (OSError, AssertionError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('actual DNS supervisor did not establish its private epoch')
            time.sleep(.02)
        assert receiver.peer[1] == 0
        children = Path(f'/proc/{wrapper.pid}/task/{wrapper.pid}/children').read_text().split()
        assert len(children) == 1
        child_pid = int(children[0])
        for _ in range(10):
            assert query('blocked.example') == 3
            receiver.poll(fresh, int(time.time()))
        assert repository._dns_outcomes.rows == {}  # Unknown loopback identities never aggregate.
        receiver.close()
        for index in range(500):
            assert query('blocked.example', qid=index) == 3
        wrapper.terminate()
        assert wrapper.wait(timeout=3) == 0
        assert not Path(f'/proc/{child_pid}').exists()
        stdout, stderr = wrapper.communicate(timeout=1)
        assert not stdout and not stderr
        assert not pid_path.exists()
        print(json.dumps({'actual_supervisor_root_credentials_nonce': 'passed',
                          'unknown_loopback_identity_never_aggregated': 'passed',
                          'collector_close_500_queries_answered': 'passed',
                          'supervisor_sigterm_reaped_actual_unbound': 'passed',
                          'private_pid_record_cleanup': 'passed',
                          'supervisor_raw_logs': 0}))
    finally:
        receiver.close()
        if wrapper.poll() is None:
            os.killpg(wrapper.pid, signal.SIGKILL)
            wrapper.wait(timeout=2)
