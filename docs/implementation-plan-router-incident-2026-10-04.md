# Vodafone router outage investigation

## Human review summary

Two household outages left the Vodafone router's PHONE light red. Internet
access and router Wi-Fi association remained broken after removing the Pi.
Only restarting the router recovered service. Treat this as a critical incident.

The cause is not established. The current dashboard starts authenticated router
checks about every 15 seconds while visible. Remove automatic router contact
and require an explicit check. Keep local dashboard status reads harmless.
Preserve Pi network settings and avoid router writes during investigation.

Risk is high because router controls affect household access. This change is
containment rather than proof that polling caused the outage. Installed source,
router event history and incident timing still need verification. The explicit
urgent repair request permits immediate scoped implementation. Broader feature
work and deployment stay with their existing owners.

## Evidence and investigation

1. Record the installed release and read bounded service logs without credentials.
2. Verify wired and test Wi-Fi address ranges, DNS/DHCP binding and queue scope.
3. Inspect every router login, read, write, retry and legacy form path.
4. Compare router PHONE and INTERNET state with Vodafone's device guide.
5. Obtain outage times and preceding actions. Do not reproduce a household outage.

## Containment design

- Automatic status requests return only an existing snapshot. They never log in.
- A deliberate status refresh runs at most one router operation and respects the
  shared minimum interval. Failed attempts cannot start a recurring retry loop.
- Provide a clear explicit refresh action. Expired state disables the switch.
- Existing router actions retain their confirmation and operation ownership.
- Keep operation polling bounded to completion of a user-requested action.
- Retain no router passwords in logs, evidence or committed documents.

## Verification and recovery

Add regression checks for zero router calls from repeated ordinary status reads,
one call from an explicit refresh, shared limits across tabs, failure handling
and stale credentials. Exercise the served browser script so opening a page,
entering remembered credentials and changing visibility do not contact the
router. Run existing router, helper and dashboard tests then relevant linting.

No dependency, build configuration, migration or saved configuration change is
needed. Test the release's normal Python versions through existing checks where
available. Local checks do not establish physical router stability.

Before deployment compare the installed revision, preserve a recovery copy and
verify management access. Restart only the dashboard if the final change permits
it. Do not change router radios, restart the router, alter client connections or
activate test profiles. Roll back by restoring the previous dashboard release.
Code rollback cannot recover a router that has already stopped serving clients.

## Deferred learning check

At the next safe checkpoint explain why an HTTP status read can still disturb
an embedded router when it creates repeated authenticated sessions. Correctness
tests and identifying the physical outage's cause are separate requirements.

## Current release integration

Adapt the scoped change onto release source d43f2d1e3f5987dde78d83416c55d3607cc85716.
Place the new control and event handling in the extracted dashboard files.
Preserve request ownership, credential generations and recovery deadlines.
Keep the original repair and its receipts on their separate branch.

Verify the current served script, router API and worker behavior. Run the router
copy and phone layout browser checks against the same source then the full local
suite and configured lint checks. Send the resulting commit and exact receipts
to the release owner. Publishing and live deployment remain with that owner.

A dashboard restart currently reapplies the saved general profile. The release
owner must establish a safe saved state before any restart. Read router status
and available event logs with the user's permission. Preserve household access.
