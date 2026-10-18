# Panic and Reset crash matrix in the kernel lab (2026-10-18)

The unit tests pin Panic with fake `tc` and `nft`. This page records the
end-to-end check: a heavy plan is applied in the kernel lab and interrupted at
every step, then the helper's real Panic or Reset runs, and the result is judged
from the kernel state and from what the router receives. No real Pi or router was
contacted.

Code: `tests/kernel/test_panic_crash_matrix.py`. It runs in the normal
`scripts/local-ci.sh kernel` step (`tests/kernel/run-in-docker.sh`) and adds
about six minutes to it. Lab: the namespaces of `tests/kernel/lab.py` (router,
Pi, two clients), real `tc`, nftables, ifb and Unbound, in the
`python:3.11-slim-bookworm` image on the colima VM `jittersim` (Linux
6.8.0-117-generic).

## What runs

Everything is called in-process, as the other kernel tests do:
`HelperService.handle_payload` with `normal-access` (Panic) and `reset`, which
run `_handle_reset`, `NetworkApplier.reset` and `AccessManager.disable` (the
household release). A restart builds a new `HelperService`, `NetworkApplier` and
`AccessManager` on the same state files and calls `restore_access_at_start` like
`run_helper` does. The Panic stop record is a real `PanicGuard` in a test folder.

A command shim between the helper and the lab counts every command that changes
state (reads such as `tc ... show`, `nft list`, `dig` are not counted) and at a
chosen step can:

- crash: raise `SimulatedCrash`, a `BaseException`, so no cleanup code runs (a
  killed helper process)
- fail: answer like a failed command, so the applier's own rollback runs
- hang: run `sleep 60` instead, so only the command timeout and the Panic
  deadline (`networking/deadlines.py`) end it
- refuse `ip link add ... type ifb`, like a kernel without the ifb module

### The plans

- heavy: three devices (one with delay 60 ms, jitter 10 ms, 5 % burst loss,
  reorder 30 %, duplicate 30 %, corruption 30 % and a 512 kbit/s cap; one
  blackhole at 100 % loss; one blocked device), Everyone else with the same
  profile, an active service quota, a browser service profile, a service list,
  a website filter, a global block, an experiment MAC deny rule, a protected
  device, and household rules (a website block with the DNS redirect to Unbound,
  20 % DNS failures, a MAC block of the second client). 110 state-changing
  steps: the household change (Unbound restart, access table, conntrack, guard),
  the ifb device, the experiment nft tables, the uplink queues, the ifb queues,
  the Wi-Fi intake (ingress qdisc and redirect filters) and the Wi-Fi queues.
- everyone: the profile for Everyone (download duplication on) with the second
  client protected. 26 steps.

### What "clean" means

After each case:

- no JitterSim qdisc on `eth0` or `wlan0` (only the kernel default `noqueue`),
  no ingress qdisc or ingress filter, no `jittersim-ifb` device
- no `inet jittersim`, `netdev jittersim_download` or
  `inet jittersim_access_transition` table, and the household table holds only
  its open chains and the rule that keeps the resolver port private
- the Panic or Reset answer is `ok`
- traffic (after every third case and the last one; the state check runs after
  every case): 40 fresh pings from each of 192.168.50.10, .11, .12 and .20 and
  200 numbered UDP datagrams from .10, with a raw capture on the router port.
  Every ping answered, no duplicate replies, average round trip under 10 ms (the
  lab answers in well under 1 ms, the profile adds 60 ms), the router gets every
  echo request and at least 199 datagrams, no copies, in order, and every frame
  passes the checks of `tests/kernel/test_upstream_safety.py` (the Pi's MAC and
  address, valid IPv4 and ICMP checksums).

Each case prints a `MATRIX|plan|inject|stop|step|command|apply|answer|problems|verdict`
line into the kernel log.

## Results

Run on head `d15c9c7b` plus the final edits of this branch (the same code under
test). Every case below was clean.

| Matrix | Cases | Result | Time |
| --- | --- | --- | --- |
| heavy: crash before each step, Panic in the same helper | 111 | 111 clean (traffic checked in 38) | 107 s |
| heavy: crash before each step, restart, Panic | 111 | 111 clean | 42 s |
| heavy: crash before each step, restart, Reset | 111 | 111 clean (household rules stay by design, see below) | 44 s |
| heavy: a failed command at each step, rollback, Panic | 111 | 111 clean (44 applies went on past an ignored failure) | 41 s |
| everyone: crash before each step, Panic | 27 | 27 clean | 24 s |
| heavy: crash at each of the 11 steps of Panic, restart, no second Panic | 11 | 11 clean | about 40 s |
| Panic twice, then Panic with nothing running | 3 | clean | |
| no ifb module: apply then Panic | 1 | clean, no damaging setting on the uplink | |
| a stalled `tc` during Panic | 3 | see below | 46 s |

Steps by group for the heavy plan (step numbers of the matrix):

| Steps | Commands | Result after Panic |
| --- | --- | --- |
| 0 to 5 | household: guard on, Unbound restart, `jittersim_access` table, conntrack, guard off | clean |
| 6, 7 | `ip link add jittersim-ifb type ifb`, `ip link set ... up` | clean |
| 8 | `nft -f` with `jittersim` and `jittersim_download` | clean |
| 9 to 38 | uplink root prio, fair queue, netem children and their filters | clean |
| 39 to 64 | ifb root, corruption netems and filters | clean |
| 65 to 79 | Wi-Fi ingress qdisc, protected MAC pass, connmark restore, redirect filters | clean |
| 80 to 109 | Wi-Fi root, children and filters | clean |
| 110 | none: the apply completed | clean |

The steps of Panic that were crashed one by one (the restarted helper finished
each one): `nft delete table inet jittersim`, `nft delete table netdev
jittersim_download`, `tc qdisc del` of both roots, the Wi-Fi intake, `ip link del
jittersim-ifb`, the household `nft -f`, the guard removal, both conntrack
clean-ups and the resolver stop. The restarted helper read the stop record
(restart latch), released the household rules at start, removed the rest in its
first gateway check without a second Panic, and refused a plain apply.

### A stalled tc during Panic

| What stalls | Answer | Time | Left after Panic | Fresh traffic | Second Panic |
| --- | --- | --- | --- | --- | --- |
| `tc qdisc del dev eth0 root` | not fully restored | 10.1 s | the uplink queues only | normal | clean |
| `tc qdisc del dev wlan0 ... ingress` | not fully restored | 10.1 s | the ingress qdisc and its filters only | normal | clean |
| every `tc` command | not fully restored | 12.6 s | all queues and the intake | normal | clean |

Every answer came well inside `PANIC_ANSWER_SECONDS` (26 s). The household
release and the nftables removals ran in all three cases. Panic answered that
it is not confirmed, so the dashboard asks for a retry, and the retry cleaned up.
Fresh flows were normal even with every queue left, because the queues classify
by marks that only the removed nftables tables set. This was not measured for a
connection that was already open: its conntrack mark stays and a remaining
Wi-Fi intake can restore it, so such a connection can stay shaped until the
retried Panic.

## Defects

### D1: a restart after an interrupted apply keeps a plan that nothing owns

Severity S3. File: `src/jittersim/networking/helper.py` (Panic-pinned). Fixed
by the follow-up branch `fix/release-interrupted-apply-at-start`, which also
removes the `expectedFailure` marker below. The fix differs from the patch here
in four ways: an apply that failed without a rollback keeps the mark, a release that fails at start holds the unconfirmed Panic stop
(so profiles do not start until Panic is retried), a mark that cannot be removed
after a good apply is only logged, and Reset and Panic clear the mark.

`_handle_apply` writes `active-plan.json` only after a complete apply. When the
helper dies during an apply (killed, out of memory), the kernel keeps what was
already applied and systemd restarts the helper (`Restart=on-failure`). The new
helper has no published plan and reports that nothing runs, while devices stay
shaped. `run_helper` removes nothing at start. In the restart matrix, 103 of
110 crash points left between 1 and 23 JitterSim objects with no plan file
before anyone pressed Panic. Once step 8 has run (the experiment table is
loaded) they shape traffic. Panic and Reset remove all of it, so this is not a Panic
failure, but nobody is told to press them.

Test: `test_a_restart_after_an_interrupted_apply_removes_what_nothing_owns`,
marked `expectedFailure`. With the patch below it passes (checked in a separate
image: "unexpected success"), and the helper and Panic unit tests pass except
the hash pins in `tests/unit/test_update_panic_source.py`, which need a new
`PANIC_APPROVED_SOURCES` entry. The patched `helper.py` has sha256
`77b8985db62cf15389cbccb3b5dd0e01deef5a5338c4c5ca872fdf4e6924ed68`.

Proposed patch: mark an apply in progress next to the published plan, remove
the mark once the plan is published or rolled back, and at start remove the
state of an apply whose mark is still there.

```diff
--- a/src/jittersim/networking/helper.py
+++ b/src/jittersim/networking/helper.py
@@ -1458,6 +1458,17 @@
                 # household devices still cannot join.
                 return {"ok": False, "error": incomplete + " Retry before choosing Start.",
                         "reason": "release-incomplete"}
+        mark = None
+        if self._active_plan_path is not None:
+            # A helper that dies during the apply leaves part of the plan in the
+            # kernel and no published plan. The mark tells the next start to
+            # remove it (release_interrupted_apply_at_start).
+            mark = interrupted_apply_mark(self._active_plan_path)
+            try:
+                mark.touch(mode=0o600)
+            except OSError:
+                LOGGER.exception("could not mark the apply as in progress")
+                return {"ok": False, "error": "network plan state could not be published"}
         try:
             self._forget_changed_lists(plan)
             self._service_addresses.expire()
@@ -1479,6 +1490,9 @@
                     LOGGER.exception("could not clear the active plan after apply failure")
             if error.cleanup_attempted:
                 self._active_plan = None
+            if mark is not None and reset_error is None and not state_cleanup_failed:
+                # Rolled back (or nothing changed): nothing is left for a restart.
+                mark.unlink(missing_ok=True)
             cleanup_result = "completed" if error.cleanup_attempted else "not started"
             if reset_error is not None:
                 cleanup_result = (
@@ -1512,6 +1526,8 @@
                     LOGGER.exception("network cleanup failed after plan publication error")
                 self._active_plan = None
                 return {"ok": False, "error": "network plan state could not be published"}
+            # The published plan owns the kernel state from here on.
+            mark.unlink(missing_ok=True)
         self._active_plan = plan
         if not report.counters_preserved:
             self._plan_count += 1
@@ -3134,6 +3150,35 @@
                   or "Some household restrictions could not be cleared.")
     LOGGER.error("household release is incomplete: %s", incomplete)
     return incomplete
+
+
+def interrupted_apply_mark(active_plan_path: Path) -> Path:
+    """The file that exists while an apply runs, next to the published plan."""
+
+    return active_plan_path.with_name("apply-in-progress")
+
+
+def release_interrupted_apply_at_start(service: "HelperService") -> bool:
+    """Remove what an apply left in the kernel when the helper died during it.
+
+    The published plan is written only after a complete apply, so nothing owns
+    that state: the dashboard shows no running plan while devices stay shaped.
+    Returns True when it was removed. A failure is logged. Panic and Reset still
+    remove it.
+    """
+
+    path = service._active_plan_path
+    if path is None or not interrupted_apply_mark(path).exists():
+        return False
+    try:
+        service._applier.reset()
+        path.unlink(missing_ok=True)
+        interrupted_apply_mark(path).unlink(missing_ok=True)
+    except (CommandExecutionError, OSError):
+        LOGGER.exception("the state of an interrupted apply could not be removed at start")
+        return False
+    LOGGER.warning("removed the queues and tables of an apply the helper did not finish")
+    return True
 
 
 def restore_access_at_start(access: AccessManager, *, panic_stopped=False) -> bool:
@@ -3199,6 +3244,7 @@
         panic_guard=PanicGuard(),
         homepage_database=homepage_database,
     )
+    release_interrupted_apply_at_start(service)
     restored = restore_access_at_start(access, panic_stopped=service._household_stop_latched)
     if not restored:
         service._block_unconfirmed_intent()
```

## Pre-existing, out of scope

- The kernel refuses a duplicating netem next to any other netem on one
  interface ("netem: cannot mix duplicating netems with other netems in tree",
  `check_netem_in_tree`, in the Linux 6.8 kernel of this lab. Whether the Pi's
  kernel has the same check was not checked).
  The renderer already knows this for changes in place, but a plan whose
  download duplicates on one device while another device has any netem on the
  same interface cannot apply at all: the first heavy plan of this check failed
  at step 80 with that error and rolled back cleanly. The heavy plan here
  therefore duplicates only uploads (where duplication is withheld anyway) and
  the Everyone plan duplicates downloads. Not changed here.
- Reset stops the experiment but keeps household rules by design: after Reset
  the MAC-blocked client stays blocked and its traffic is not checked. Panic is
  the action that releases household rules.

## Not covered

- A `tc` stuck in the kernel (uninterruptible sleep) cannot be killed by a
  timeout. The stall here is a process that can be killed.
- The real Pi kernel, hostapd and NetworkManager. The lab replaces systemd for
  Unbound (`tests/kernel/test_access.py`'s `DnsServiceExecutor`) and has no
  Wi-Fi radio.
- Power loss: a reboot clears all kernel state anyway.
