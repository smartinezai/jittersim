# Isolate the upload corruption effect

Add one tests-only namespace check at main `3f5499a99a08498e547cac2a26e2475c3fc9456d`.
The existing lab, upstream frame parser, renderer and applier are unchanged
from the earlier `ec403d0f` evidence. No production or shared fixture edits.

The missing assertion is causal: existing upstream safety cases combine loss
with corruption. This test compares three finite phases on one device:
corruption 0%, corruption 100% and corruption 0% again. Loss, delay, jitter,
duplication, reordering, caps, randomization and outages stay off throughout.
Only the upload direction changes. Use the real NetworkApplier and existing
tests.kernel.lab topology. Never recreate the old corrupt-on-uplink layout.

For each phase send 100 numbered ICMP requests with the existing 64-byte
pattern and capture the router port using the existing CAPTURE/inspect_frames
helpers. Also send 20 Pi-originated control pings. Assert:

- Both 0% phases deliver all 100 client requests and all 20 Pi requests.
- At 100% the helper-independent applier reports upload corruption active,
  the installed IFB queue says corrupt 100% and IFB counters show real traffic.
- At 100% fewer than 50 client requests arrive while all 20 Pi requests do.
  This is a broad effect threshold rather than an assertion that every frame
  is lost. Flips in already parsed Ethernet bytes can have no visible effect
  and a new connection's first packet bypasses upload corruption.
- Every phase's captured router-facing frames pass the existing MAC, IPv4,
  NAT-source and ICMP checksum checks. The eth0 qdisc must contain no active
  corruption, duplication or reordering.
- Each phase's process group has finite deadlines. Cap retained capture
  frames with a local variant of CAPTURE and fail on overflow. Register
  namespace cleanup before create_lab and always terminate/reap child
  processes. Reset and namespace deletion run even after assertion failure.

Keep the controls, offered count, delivered count, IFB counters, queue text and
capture-clean result in test output. Do not assert an exact random damage
rate or TCP recovery. Reuse existing no-IFB and protected-device receipts.
Physical Pi receive-offload behavior remains unresolved.

First review this plan and command with the coordinator. Then implement only
tests/kernel/test_corruption_effect.py. Perform compilation and static checks
without load. Freeze exact source and hand one approved applicable run to a
medium runner. Preserve failures without automatic retries.

## Exact single test command for the reserved window

Run from this isolated worktree only after explicit coordinator clearance.
Inspect both live lock holders immediately before entry. This command uses
the existing kernel Dockerfile and privileged namespace execution with its
default command replaced by just the new case. The two locks are the real
exclusive reservation. This is never an ordinary eligible pytest rerun.

The original command is superseded by the complete bounded command in
[REPORT.md](REPORT.md#exact-bounded-command-after-coordinator-go). It uses
nonblocking docker-1 plus docker-2 locks, validates the image ID, names its
container and preserves its cidfile. Build/test and cleanup have finite
deadlines. Do not execute the earlier unbounded form.


The runner records commit/tree, dirty status, image ID, command, lock holders,
start/end times, exit status and cleanup outcome in the unique report. No
Docker build or test starts before a coordinator-approved window. The runner
must validate the built image ID as the existing privileged runner does.
