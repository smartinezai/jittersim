# Upstream-safe impairments: the Panic-gated part (10 October 2026)

This is the part of PR 125 (`fix/upstream-safe-impairments`) that touches
Panic-gated files. The branch is `fix/upstream-safe-impairments-gated`, cut from
`origin/integrate/gated-batch-2026-10-09`. No `PANIC_APPROVED_SOURCES` entry is
added: the coordinator arranges the independent review and the approved
bundle. Until then, the Panic hash tests fail as expected.

**Status (10 October 2026):** section 1 is part of the combined Panic batch. Section 2 (the rollback cleanup) was written and reviewed but is NOT merged and not implemented in main, because the reviews found open S2 problems. See docs/carry-forward-panic-batch-2026-10-10.md for the open problems and the owner action after a rollback (reboot the Pi).

## 1. Helper apply response (implemented on this branch)

`src/jittersim/networking/helper.py`, `_handle_apply` return:
`"upload_corruption_applied": getattr(report, "upload_corruption_applied", False) is True`.

PR 125's `NetworkApplier.apply` returns `ApplyReport.upload_corruption_applied`
(True only when upload corruption runs on `jittersim-ifb` before the Pi
forwards). The dashboard (`server._upstream_warning`) already prefers this
field. Without it, the dashboard reads `/sys/class/net/jittersim-ifb`. The
`getattr` keeps this branch correct before and after PR 125 merges.

Tests: `tests/unit/test_helper.py::UpstreamSafeApplyResponseTests` (true,
false, and an older applier without the field).

## 2. Rollback and failed-update restoration (review finding P4, NOT implemented)

**Problem.** An older package's reset only removes the uplink and Wi-Fi root
queues and the two nft tables. It does not know the new intake on the test
interface (`tc qdisc ... handle ffff: ingress`, with `matchall action connmark
continue` and `fw ... action mirred egress redirect dev jittersim-ifb`) or the
`jittersim-ifb` device. `jittersim-helper.service` has no `ExecStop` cleanup.
After a rollback, a running marked connection can keep being redirected
through the ifb netem with corruption, under a helper that cannot see or
remove it. The damage still happens before the Pi forwards, so the router
gets no damaged headers. But the old dashboard reports the profile as stopped
while corruption continues, and Panic in the old package does not stop it.

**Change needed in `src/jittersim/updates.py`.** There are three sites, each
right after the writers are stopped and `writers_stopped(...)` has passed, and
before `restore()` or before starting an older package:

- the installer path: after `run(["systemctl", "stop", *names], ...)` and its
  `writers_stopped` check (around line 3671 on this base);
- the requested rollback: after the stop and `writers_stopped` near line
  4248, before `restore()` when `request.get("rollback")`;
- the failed-update recovery: after the stop near line 4390, before
  `restore()`.

At each site, run the same cleanup the new applier does, as root, and verify
it:

```text
tc -j qdisc show dev <test_interface> ingress  -> if an ingress qdisc exists:
    tc qdisc del dev <test_interface> handle ffff: ingress
ip -j link show dev jittersim-ifb              -> if it exists:
    ip link del dev jittersim-ifb
verify: both lookups now report nothing; otherwise raise UpdateError
        ("the upload intake could not be removed before recovery")
```

The interface name comes from `/etc/jittersim/runtime.env`
(`JITTERSIM_TEST_INTERFACE`, as the service-watch unit uses). Each step is
tried even if the other fails, and the first failure is reported afterwards,
like `NetworkApplier.reset` in PR 125. A missing interface ("Cannot find
device") counts as removed.

Alternative with the same effect and less update code: add
`ExecStopPost=/opt/jittersim/.venv/bin/jittersim-helper --remove-upload-intake`
to `jittersim-helper.service`, so every stop removes the intake. A rollback
copies the old unit file back, though, so `updates.py` must still clean up
once.

**Tests to add with that change** (`tests/unit/test_updates*.py` style, fake
`run`):

1. A rollback with an existing intake and ifb removes both after
   `writers_stopped` and before `restore()`. Check the order in the recorded
   command list.
2. A failed-update recovery does the same.
3. If the intake removal fails, ifb removal is still attempted and the update
   fails with the intake error, before `restore()`.
4. Kernel (Docker lab): apply a corrupting plan with PR 125, keep a marked UDP
   flow running, run the cleanup, then apply with the old renderer.
   Expect no ingress qdisc, no ifb device, and no frame through the old Wi-Fi
   intake (tc -s shows no redirect counters).

## 3. Review finding P2 (skipped here)

DNS capture under the helper operation lock can delay Panic. This is covered by
the gated Panic batch (normal-access no longer takes the operation lock). It is
not duplicated here.
