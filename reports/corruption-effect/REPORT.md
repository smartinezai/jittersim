# Corruption-only namespace check

Base: `3f5499a99a08498e547cac2a26e2475c3fc9456d`.
Plan approved by coordinator on 24 October 2026. Scope is the single new
`tests/kernel/test_corruption_effect.py` plus its own plan and this report.
No production, pinned or shared fixture changes. No TCP recovery claim.

Execution status: first approved attempt FAILED. After the separately
reviewed count correction the one authorized corrected-head run PASSED.
Both receipts are preserved. No automatic retry occurred.

## Corrected-head result

Executed commit: `76a5ea49742724efa236820d65b82f57ab9e5649`.
Tree: `d186d6254bdce9c28b75a74c96aa3d780b7b87e1`.
Image: `sha256:f17adf9e2202a7aa93ebfd1eafec046a90f2347b40232030e219af949ad17ffb`.
Exactly one test passed with zero skips in 12.458 seconds. Terminal exit 0.
The medium runner confirmed no tracked changes before or after execution.

| Phase | Offered | Router received | Pi controls received | Router frames clean |
| --- | ---: | ---: | ---: | --- |
| 0% | 100 | 100 | 20 | true |
| 100% | 100 | 12 | 20 | true |
| Recovery 0% | 100 | 100 | 20 | true |

The IFB netem queue reported `corrupt 100%` with 99 packets and 10494 bytes
sent. Its zero queue-drop count does not mean corruption had no effect:
damaged packets can be rejected after leaving that queue. This test observes
the resulting delivery reduction at the router with other impairments off.
It does not measure an exact bit-flip percentage or prove TCP recovery.

Full corrected evidence is in `test-results/jittersim-corruption-effect-78090/`:
`result.json`, `test.log`, `build.log`, `cleanup.log`, `locks.log`,
`final-evidence.json`, `image.id` and `container.id`. Additional preflight
and terminal logs use prefix `corruption-effect-preflight-20261024T174523Z`.

Namespace cleanup passed. Container removal and subsequent exact-name absence
were confirmed. No owned processes remained and both locks were free.
The unrelated `b2744e7e3a5b` container remained running and untouched.
All runner activity ended before this report update. Physical Pi driver and
offload behavior remain unresolved. No production or pinned files changed.

## Preserved first failure

Reviewed and executed commit: `5e56a5edc073b755cda07367d810a128d9074a1a`.
Tree: `308eb4f1f61da4182b40e3539993d286a245d67d`.
Image: `sha256:de4cebf4c786cbae945b3172a2f322fb2611402fd05ad4c8f25bce14f7171d92`.
The medium runner confirmed no tracked changes before or after execution.

The build succeeded. The single case failed with zero skips. The first 0%
phase delivered 100/100 client requests and 20/20 Pi control requests with
clean router-facing frames. In the 100% phase ping reported 166 transmitted
and 31 received within its two second deadline. The assertion requiring
exactly 100 transmitted failed at test line 117. The corruption phase's
remaining capture and IFB assertions and the recovery 0% phase were not
completed. This is a failed test sampling/count contract. It does not prove
that production corruption is broken or that this causal check passed.

Full retained evidence is in
`test-results/jittersim-corruption-effect-77760/`: `result.json`, `test.log`,
`build.log`, `cleanup.log`, `locks.log`, `final-evidence.json`, `image.id` and
`container.id`. Additional preflight and terminal logs are in test-results
with prefix `corruption-effect-preflight-20261024T174109Z`.

Cleanup is confirmed: namespace removal printed success, the runner recorded
container_removed=true and a subsequent exact-name Docker query returned no
container. Both locks were free and no owned runner process remained. The
unrelated `maia-skills-combined-check` container `b2744e7e3a5b` remained running.
The first run was not retried. Physical Pi/offload behavior is still
unresolved. This report was updated only after all runner activity ended.

## Authorized count correction

The coordinator reviewed the first failure against the primary
[iputils ping manual](https://www.man7.org/linux/man-pages/man8/ping.8.html)
and authorized one narrow correction. With `-c` and `-w` together ping waits
for the requested reply count or deadline rather than enforcing a fixed
send count. The test now uses `-c` with `-W 1` and keeps its existing outer
five second subprocess timeout. `-W` controls response waiting rather than
the total command runtime. Only the two option arguments and their comment
changed. All offered counts, effect thresholds, IFB evidence, frame checks
and three phases are unchanged. The first failed receipt remains intact.
Compilation and Ruff passed on corrected source. The coordinator separately
reviewed the exact correction and authorized only the corrected execution
recorded above.

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
second deadlines. Ping uses fixed send counts with `-W 1` for response waiting
and an outer five second timeout. Capture readiness and completion have deadlines. Its child is
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
