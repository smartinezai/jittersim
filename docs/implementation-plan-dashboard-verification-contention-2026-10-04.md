# Repair dashboard device-verification contention

## Human review summary

A single dashboard can send overlapping identity checks. The helper rejects
checks whenever its shared access lock is held. A bounded live reproduction
on the installed 081b2ab release succeeded twice sequentially then rejected
three of four simultaneous checks with the owner's exact busy message.
The longest successful check took 31 ms. This is a demonstrated concurrency
problem and does not establish insufficient Pi hardware.

Repair overlapping reads without weakening token or device verification.
Dashboard status checks now wait at most 0.5 seconds for the existing access
lock. This lets a short burst take turns while each check still reads current
grants under the same lock used by serialized access changes. A timeout stays
fail-closed. Temporary helper contention has its own HTTP code and leaves the
token in place for a manual retry. No automatic retry or new cache is added.

## Evidence and current operating state

- Installed release: 0.2.0.dev+081b2ab. Helper and dashboard services are active.
- Management access was restored through router Wi-Fi at 192.168.0.34.
- The owner renewed administrator access with sudo -v. No network setting
  was changed during these diagnostics.
- Evidence is in test-results/release-handoff-2026-10-04/
  pi-only-accesspoint-verification/device-verification-contention-probe.json.
- Recent logs also show helper-busy responses on upstream status reads and
  failed traffic-counter reads. Trace these separately before attributing
  every failure to the access lock. Stopped counters must not be assumed to
  exist or used as proof of a hardware limit.
- General and Everyone profiles are unset. There are no groups or schedules.
- KPMac is assigned low, named daily climb manual. Its station is associated
  but its current address is missing. Its assignment is paused as unverified.
- KPiPhone remains assigned bad-test and is not in the current station list.
- The low profile has upload 9204 kbit/s and download 3680 kbit/s. Both
  bandwidth probabilities are 100 percent and homepage bounce is zero.
- Air and controlling Mac protection remain saved. Profiles were not started.

## Proposed work

1. Trace dashboard sign-in and refresh requests through API authorization,
   helper admission, access locking and identity observations. Measure the
   existing limits and request deadlines before choosing wait durations.
2. Handle concurrent read-only verification using bounded waiting or shared
   in-flight work. Preserve current peer and station checks. Cache only
   suitable identity observations and reevaluate grants after access changes.
   Never return an old allowed decision after removal or identity change.
3. Keep enrollment, removal and address moves serialized. Preserve Reset,
   Panic and control revisions. Do not broaden permissions or disable checks.
4. Prevent sign-in and polling from creating avoidable duplicate work. Retain
   the token on temporary helper-busy responses and explain a bounded retry.
   Use no indefinite retries or per-device polling expansion.
5. Review helper admission across ordinary dashboard reads. Ensure a burst of
   status work cannot starve identity checks or network stop actions.
6. Validate cold and warm caches, one and multiple peers, concurrent removal,
   stale addresses, failed commands, timeouts and exhausted admission capacity.
   Add focused HTTP and browser regressions for one-tab sign-in and refresh.
7. Check the complete change then run relevant local CI counterparts and an
   installed Linux package test. Repeat the small physical concurrency probe.
   Use the official guarded deployment with a checked private recovery copy.

## Implemented design and review checks

- Only the `dashboard-access` status operation waits on access-lock contention.
  It waits no more than 0.5 seconds. Other access operations keep their
  immediate busy response and continue to use the same lock, so access writes
  remain serialized. The existing eight helper request slots still cap helper
  concurrency.
- Identity observations and their one-second peer cache are unchanged. Every
  status call still checks the saved grant list while holding the access lock.
  A status check queued behind a successful removal therefore sees the removal
  before it can return an allowed result.
- Only helper-busy from `/api/dashboard-access/status` becomes HTTP 409 with
  code `helper-busy`. The browser keeps the entered token, keeps private panels
  locked and offers a manual retry. It does not retry in the background. Bad
  bearer tokens still receive HTTP 401.
- The installer approves a fixed hash bundle for the Panic guard, networking
  helper and access module. This change therefore adds the exact new helper
  hash as an approved bundle while keeping the Panic guard and access module
  hashes unchanged. The full Panic-source approval tests must pass before the
  release can proceed.
- Focused regressions cover four overlapping status checks, a bounded wait,
  immediate serialized writes, current grants after removal, HTTP auth and the
  browser's temporary-error state. Full local suite, CI and physical acceptance
  remain release gates until their results are recorded.

## Release and recovery gates

This is a high-risk authorization and network-control repair. Inspect the
repository PR template before publication and require the appropriate security
review. No remote publication or unrelated merge is authorized by this plan.
Keep router management Wi-Fi available during deployment and physical checks.
Retain previous source and settings for rollback. Tokens, Wi-Fi passwords and
private keys must not enter logs or evidence.

Verify sign-in and refresh on both the protected Air and controlling Mac
through Pi Wi-Fi before another final router-Wi-Fi cutover. A healthy public
health endpoint alone does not establish reliable authorized dashboard access.
Do not activate the requested device experiment until both current client
identities are verified and KPiPhone's assignment is changed to low through
a supported control. Preserve the owner's chosen gradual bandwidth changes.
