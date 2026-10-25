# Protected devices and global blocked sites

Prepare the existing reviewed local protected-device patch for current main
`b061d2d84439bf906f1da8ca9ae306cfa0b27f52`. Protection bypasses only global
Blocked Sites. Allow-only policy, named/universal SiteBlock rules, MAC controls,
DNS failure settings and separately opted-in protected-device ad blocking remain
effective. No automatic release or deployment is part of this work.

Reuse the complete `40346b827bac5132ba5b2644643712acabc230ed` lineage based on
`49b37078`, not its final kernel-only commit in isolation. It includes protected
access-plan MACs, per-view private DNS listeners, source-MAC/IP routing and direct
listener privacy guards, household refresh failure handling and proxy parity.
Its prior native8682/123skip and kernel126/16skip receipts are historical evidence.
They do not establish correctness on the new base.

1. Claim sole ownership of networking/access.py and networking/renderer.py.
   Coordinate engine access/proxy/ad-blocking paths before editing. Backend owner
   controls storage/API and supplies or applies the narrow protected-settings and
   refresh hooks. No concurrent pinned helper edit is permitted.
2. Extract only feature and regression deltas from the existing lineage. Apply
   current-base-compatible hunks preserving intervening cache, DNS, API and safety
   fixes. Exclude the old updates.py bundle entry and historical shared docs.
3. Verify allow-only still precedes protected address bypass, global blocked
   destinations follow that bypass and DNS protected selection cannot be reached
   directly by unprotected, uplink or spoofed-source callers. Keep mDNS5353 separate
   from private resolver listeners. Preserve optional ad filtering.
4. Add/reuse meaningful current-delta tests for DNS/view wiring, protected address
   bypass, unprotected blocking, proxy identity, policy refresh failure and Panic
   neutral privacy guards. Run applicable targeted native tests through local CI.
   Coordinate the existing exclusive Docker wrapper for applicable kernel cases.
5. Freeze a local commit and request independent exact-head review. Preserve
   actual failures and current receipts, pin hashes and explicit unrun limits.
   Review any later outcome-hook proposals before this sole writer integrates
   them in the claimed networking files.

The only authorised pinned work is local preparation and tests. Do not push pinned
access/helper changes or a new bundle without concrete action-time owner approval.
Do not change PANIC_APPROVED_SOURCES in this slice. A pin test failure caused by
unapproved local access.py must be reported honestly, not bypassed. No Pi/router,
live network settings, secrets, signing, main merge or release action.

Rollback is reverting this local feature before deployment. Current process keeps
the running release and source checkout untouched. Publication and any two-stage
approved-source transition remain coordinator/owner decisions. Stop at2%reserve.
