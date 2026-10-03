import os
import shutil
import sys
import pathlib
import subprocess
import time


if os.environ.get('JITTERSIM_OFFLINE_SECRET_PROOF') != '1' or not pathlib.Path('/.dockerenv').exists():
    raise SystemExit('Run only in an explicitly allocated disposable offline Docker fixture')
if {item.name for item in pathlib.Path('/sys/class/net').iterdir()} != {'lo'}:
    raise SystemExit('The fixture must use --network none with no radio or host networking')

sys.path.insert(0, '/proof/src')
from jittersim.networking.executor import SubprocessExecutor, CommandExecutionError
from jittersim.networking.commands import Command
from jittersim.networking.wifi import WifiController


def run(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, timeout=10,
                          check=True, **kwargs).stdout.strip()


pathlib.Path('/run/dbus').mkdir(parents=True, exist_ok=True)
pathlib.Path('/etc/NetworkManager/conf.d').mkdir(parents=True, exist_ok=True)
pathlib.Path('/etc/NetworkManager/conf.d/99-offline-proof.conf').write_text(
    '[main]\nplugins=keyfile\ndns=none\n[device-offline]\nmatch-device=*\nmanaged=0\n')
bus = subprocess.Popen(['dbus-daemon', '--system', '--nofork'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
daemon_log = pathlib.Path('/tmp/networkmanager-proof.log')
log = daemon_log.open('w')


def daemon_start():
    daemon = subprocess.Popen(['NetworkManager', '--no-daemon'], stdout=log, stderr=log)
    for _ in range(50):
        result = subprocess.run(['nmcli', '-g', 'VERSION', 'general'],
                                text=True, capture_output=True, timeout=2)
        if result.returncode == 0:
            return daemon
        time.sleep(0.1)
    raise RuntimeError('offline daemon did not start')


daemon = None
try:
    daemon = daemon_start()
    print(run(['nmcli', '--version']))
    run(['nmcli', 'connection', 'add', 'type', 'wifi', 'ifname', 'wlan-proof',
         'con-name', 'jittersim-ap', 'autoconnect', 'no', 'ssid', 'Offline fixture',
         '802-11-wireless.mode', 'ap', 'wifi-sec.key-mgmt', 'sae',
         'wifi-sec.proto', 'rsn', 'wifi-sec.pairwise', 'ccmp',
         'wifi-sec.group', 'ccmp', 'wifi-sec.pmf', '3'])
    fields = 'connection.uuid,connection.interface-name,connection.autoconnect,802-11-wireless.mode,802-11-wireless-security.key-mgmt,802-11-wireless-security.proto,802-11-wireless-security.pmf'
    original = run(['nmcli', '-g', fields, 'connection', 'show', 'id', 'jittersim-ap'])
    passwords = ['ordinary-fixture-value', ' leading and trailing ',
                 'quote"slash\\dollar$backtick`colon:semi;quit', 'x' * 63]
    child_env = dict(os.environ, HOME='/dev/null', XDG_CACHE_HOME='/dev/null')
    for index, password in enumerate(passwords):
        proc = subprocess.Popen(['nmcli', '--colors', 'no', 'connection', 'edit', 'id', 'jittersim-ap'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=child_env)
        commandline = pathlib.Path(f'/proc/{proc.pid}/cmdline').read_bytes()
        environment = pathlib.Path(f'/proc/{proc.pid}/environ').read_bytes()
        assert password.encode() not in commandline
        assert password.encode() not in environment
        stdout, stderr = proc.communicate('quit\n', timeout=10)
        assert proc.returncode == 0, 'editor failure (output withheld)'
        old = subprocess.run(['nmcli', '--escape', 'no', '--show-secrets', '-g', '802-11-wireless-security.psk',
                              'connection', 'show', 'id', 'jittersim-ap'],
                             capture_output=True, text=True, timeout=5, check=True).stdout.removesuffix('\n')
        assert old != password, 'production proof requires a different previous value'
        WifiController(SubprocessExecutor(), 'wlan-proof')._save_password(password)
        saved = run(['nmcli', '--escape', 'no', '--show-secrets', '-g', '802-11-wireless-security.psk',
                     'connection', 'show', 'id', 'jittersim-ap'])
        # strip only nmcli's final newline when comparing spaces
        saved_exact = subprocess.run(['nmcli', '--escape', 'no', '--show-secrets', '-g', '802-11-wireless-security.psk',
                                      'connection', 'show', 'id', 'jittersim-ap'],
                                     capture_output=True, text=True, timeout=5, check=True).stdout.removesuffix('\n')
        assert saved_exact == password, f'saved secret differs at case {index}, lengths {len(saved_exact)}/{len(password)} (values withheld)'
        assert original == run(['nmcli', '-g', fields, 'connection', 'show', 'id', 'jittersim-ap'])
        assert not pathlib.Path('/root/.nmcli-history').exists()
        assert not pathlib.Path('/root/.cache/nmcli-history').exists()
        daemon.terminate()
        daemon.wait(timeout=5)
        daemon = daemon_start()
        persisted = subprocess.run(['nmcli', '--escape', 'no', '--show-secrets', '-g', '802-11-wireless-security.psk',
                                    'connection', 'show', 'id', 'jittersim-ap'],
                                   capture_output=True, text=True, timeout=5, check=True).stdout.removesuffix('\n')
        assert persisted == password, 'restarted secret differs (values withheld)'
        log.flush()
        assert password not in daemon_log.read_text()
    bad = subprocess.run(['nmcli', '--colors', 'no', 'connection', 'edit', 'id', 'jittersim-ap'], input='set 802-11-wireless-security.psk-missing\nunsaved-fixture-value\nsave persistent\nquit\n', capture_output=True, text=True, timeout=10, env=child_env)
    assert bad.returncode == 0, 'expected false-success demonstration'
    unchanged = subprocess.run(['nmcli', '--escape', 'no', '--show-secrets', '-g', '802-11-wireless-security.psk', 'connection', 'show', 'id', 'jittersim-ap'], capture_output=True, text=True, timeout=5, check=True).stdout.removesuffix('\n')
    assert unchanged == passwords[-1], 'failed set changed password (values withheld)'
    print('PASS reproduced editor exit0 after rejected property write, exact persistence check catches it')
    directory = pathlib.Path('/etc/NetworkManager/system-connections')
    backup_directory = directory.with_name('proof-saved-profiles')
    shutil.copytree(directory, backup_directory)
    shutil.rmtree(directory)
    directory.write_text('force ENOTDIR for persistent writes')
    failed_save = subprocess.run(['nmcli', '--colors', 'no', 'connection', 'edit', 'id', 'jittersim-ap'], input='set 802-11-wireless-security.psk\nrefused-save-fixture\nsave persistent\nquit\nyes\n', capture_output=True, text=True, timeout=10, env=child_env)
    fresh = subprocess.run(['nmcli', '--escape', 'no', '--show-secrets', '-g', '802-11-wireless-security.psk', 'connection', 'show', 'id', 'jittersim-ap'], capture_output=True, text=True, timeout=5, check=True).stdout.removesuffix('\n')
    assert fresh == passwords[-1], 'failed disk save updated daemon secret (values withheld)'
    try:
        WifiController(SubprocessExecutor(), 'wlan-proof')._save_password('refused-save-fixture')
    except CommandExecutionError as error:
        assert not error.stdout and not error.stderr and error.__context__ is None
    else:
        raise AssertionError('production executor accepted failed persistent save')
    directory.unlink()
    shutil.copytree(backup_directory, directory)
    shutil.rmtree(backup_directory)
    print('PASS forced persistent-save failure keeps old daemon PSK, exact fresh read rejects new value')
    interrupted_password = 'persisted-before-timeout'
    try:
        SubprocessExecutor().run(Command(
            'nmcli', ('--colors', 'no', 'connection', 'edit', 'id', 'jittersim-ap'),
            'private interrupted write',
            stdin='set 802-11-wireless-security.psk\n' + interrupted_password + '\nsave persistent\n',
            sensitive=True, timeout_seconds=0.5,
        ))
    except CommandExecutionError as error:
        assert not error.stdout and not error.stderr
        assert error.__context__ is None
    else:
        raise AssertionError('EOF without quit unexpectedly completed')
    fresh = subprocess.run(['nmcli', '--escape', 'no', '--show-secrets', '-g', '802-11-wireless-security.psk',
                            'connection', 'show', 'id', 'jittersim-ap'],
                           capture_output=True, text=True, timeout=5, check=True).stdout.removesuffix('\n')
    assert fresh == interrupted_password, 'uncertain-save proof did not persist (values withheld)'
    assert interrupted_password not in daemon_log.read_text()
    print('PASS saved-then-EOF remains a generic failure despite persisted change')
    profiles = list(pathlib.Path('/etc/NetworkManager/system-connections').glob('*'))
    assert len(profiles) == 1
    assert profiles[0].stat().st_mode & 0o777 == 0o600
    print('PASS four literal passwords persisted across daemon restart')
    print('PASS UUID/interface/autoconnect/AP/security unchanged')
    print('PASS no secret in process argv/environment/history/daemon logs')
    print('PASS root-owned profile permissions 0600')
finally:
    if daemon is not None:
        daemon.terminate()
        daemon.wait(timeout=5)
    bus.terminate()
    bus.wait(timeout=5)
    log.close()
