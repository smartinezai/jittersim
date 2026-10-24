# Corruption-only namespace check

Base: `3f5499a99a08498e547cac2a26e2475c3fc9456d`.
Plan approved by coordinator on 24 October 2026. Scope is the single new
`tests/kernel/test_corruption_effect.py` plus its own plan and this report.
No production, pinned or shared fixture changes. No TCP recovery claim.

Execution status: NOT RUN. Await final source review and an exclusive window.

Static checks: Python 3.9 compilation passed with a workspace cache directory.
Ruff E/F/I passed. The bounded runner Python block compiles. The first
compilation attempt could not write macOS's default cache location and was
repeated with a workspace cache path. No kernel test was executed by these
checks.

The case applies 0%, 100% and 0% upload corruption through the production
applier in the existing namespace lab. Equality assertions on every compiled
queue require all other impairment values to be zero/default. Both control
phases require all 100 requests to arrive. The corruption-only phase requires
fewer than 50, an installed 100% IFB queue and actual IFB packet counters.
Every phase requires all 20 Pi-originated controls and clean router frames.
Existing no-IFB and protected-device evidence is reused rather than repeated.

Capture retention is capped at 1024 frames. Setup/removal commands have five
second deadlines. Ping has a two second deadline and an outer five second
timeout. Capture readiness and completion have deadlines. Its child is
terminated and reaped in finally. Namespace removal is registered before
creation and checks that each owned namespace disappeared even after a
partial failure. Docker removal adds the final containment boundary.

## Exact bounded command after coordinator GO

Inspect actual lock holders immediately before running. Both nonblocking
locks must be acquired or this attempt exits without starting Docker. The
300-second outer deadline covers build and test execution. Build is capped
at 180 seconds and test execution at 75 seconds. Cleanup has its own finite
deadline. Only the named container and process groups started here are removed.
Logs and the cidfile remain in test-results. No automatic retry.

```sh
DOCKER_CONTEXT=colima-jittersim lockf -k -t 0 /private/tmp/claude-501/jittersim-slots/docker-1.lock lockf -k -t 0 /private/tmp/claude-501/jittersim-slots/docker-2.lock python3 - <<'PY'
import datetime
import json
import os
import pathlib
import re
import signal
import subprocess
import time

started = datetime.datetime.now(datetime.timezone.utc).isoformat()
name = 'jittersim-corruption-effect-' + str(os.getpid())
out = pathlib.Path('test-results') / name
out.mkdir(parents=True, exist_ok=False)
owned = []
status = {'started': started, 'container': name, 'result': 'FAILED', 'exit': 1}

def stop(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    process.wait(timeout=2)

def run(label, args, timeout):
    status[label + '_command'] = args
    with (out / (label + '.log')).open('wb') as log:
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        owned.append(process)
        try:
            code = process.wait(timeout=timeout)
            if code:
                raise RuntimeError(label + ' exited ' + str(code))
        finally:
            stop(process)

def deadline(signum, frame):
    raise TimeoutError('whole build/test deadline exceeded')

signal.signal(signal.SIGALRM, deadline)
signal.alarm(300)
try:
    for key, args in (
        ('commit', ['git', 'rev-parse', 'HEAD']),
        ('tree', ['git', 'rev-parse', 'HEAD^{tree}']),
        ('dirty', ['git', 'status', '--short']),
    ):
        status[key] = subprocess.check_output(args, text=True, timeout=5).strip()
    run('build', ['docker', 'build', '-q', '-f', 'tests/kernel/Dockerfile',
                 '--iidfile', str(out / 'image.id'), '.'], 180)
    image = (out / 'image.id').read_text().strip()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise RuntimeError('invalid image ID')
    status['image'] = image
    run('test', ['docker', 'run', '--rm', '--privileged', '--memory', '1g',
                '--name', name, '--cidfile', str(out / 'container.id'), image,
                'python', '-u', '-m', 'unittest', '-v',
                'tests.kernel.test_corruption_effect.CorruptionEffectTests.'
                'test_upload_corruption_only_changes_delivery_and_keeps_router_clean'], 75)
    status.update(result='PASS', exit=0)
except BaseException as error:
    status['error'] = repr(error)
finally:
    signal.alarm(0)
    for process in owned:
        stop(process)
    try:
        run('cleanup', ['docker', 'rm', '-f', name], 10)
    except BaseException as error:
        status['cleanup_note'] = repr(error)
    try:
        remaining = subprocess.check_output(
            ['docker', 'ps', '-aq', '--filter', 'name=^/' + name + '$'],
            text=True, timeout=10).strip()
        status['container_removed'] = not remaining
        if remaining:
            status.update(result='FAILED', exit=1)
    except BaseException as error:
        status.update(result='FAILED', exit=1, cleanup_unconfirmed=repr(error))
    status['finished'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    (out / 'result.json').write_text(json.dumps(status, indent=2) + '\n')
    print(json.dumps(status, indent=2), flush=True)
raise SystemExit(status['exit'])
PY
```

The existing Dockerfile supplies all dependencies and uses the established
namespace fixtures. The command overrides its full-matrix default with only
the new case. The runner must save the live lock snapshot alongside its result.
Physical Pi driver/offload behavior remains untested regardless of the outcome.
