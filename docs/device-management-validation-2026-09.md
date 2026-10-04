# Device management checks

## Summary

The approved change adds a persistent unaffected-device list and profile or
group actions for detected Wi-Fi clients. Protection uses the device's Wi-Fi
MAC during packet handling. It bypasses all profile behavior and quota usage
while preserving global access rules. Existing assignments resume when
protection is removed.

The review also found and fixed reused-IP identity errors, lost quota baselines,
global website rules mixed with profile rules, progression website blocks that
could block every destination, edits to an existing profile that could retain
old runtime settings and manual changes that could restart another client's
byte progression.

## Checks completed before release

- The full Mac suite passes with 269 tests. The new cases cover identity,
  persistence, restart, backup contents, concurrent writes at the saved-list
  limit, authentication, group conflicts, stale selections and failed applies.
- Protection is covered across direct profiles, groups, schedules, timers,
  progressions, random outages, service profiles and quota fallbacks.
- Quota checks cover usage before protection, while protected and after
  removal. Scheduled resets still run and offline group members do not freeze
  the remaining clients' usage.
- An installed package in a fresh Debian container passes the existing tests
  and the Linux network smoke check. Real routed Ethernet packet checks cover
  both directions, 100% loss on the affected client, protected traffic, IP reuse
  after apply, multiple addresses per MAC and zero protected quota usage.
  Global MAC rules and website allowlists still apply to protected traffic.
- Browser proxy tests keep protected tunnels open across unrelated plan
  changes and close them after protection or destination permission changes.
- Browser checks use two simulated clients. They cover profile assignment,
  joining a group, saved protection, disabled profile controls, pending form
  choices during refresh and honest override feedback. Desktop and 390-pixel
  layouts fit the page without horizontal overflow.
- Focused Python lint and installer shell syntax checks pass.

## Pi release acceptance

Build and deploy the committed source. Keep the database, credentials and AP
settings in root-only backups. The installer probes the outgoing Ethernet hook
on the actual Pi kernel before replacing code. Verify source hashes, readable
installed packages, active services and authenticated dashboard endpoints.

Two real devices on JitterSim-Test are required for Wi-Fi traffic acceptance.
Keep one protected and apply a test profile to the other. Verify upload and
download behavior plus group or timed changes. Container packet tests and
authenticated dashboard responses do not substitute for this hardware check.

To roll back to a release without protection, first apply and verify a normal
network reset with this release. Replacing code alone can ignore the saved
unaffected list.

## Remaining gaps and useful follow-ups

1. Show the effective profile and its reason beside every client continuously.
   Save feedback explains overrides now, but a timer or quota can change the
   result later.
2. Add bulk client selection, group moves and saved-assignment removal. Current
   controls avoid address entry but moving a client requires editing its old
   group first. Show remaining capacity against the 15 combined device and
   service-rule limit.
3. Bound background website resolution time. Domain counts and cache size are
   bounded, but the system DNS lookup can still delay an apply request.
4. Router Wi-Fi client discovery would need a separate router integration.
   The current connected list accurately reports clients on the Pi's Wi-Fi.

Private Wi-Fi MAC changes require updating protection. Website rules remain
best-effort IPv4 destination rules. These are existing network constraints,
not claims that every application protocol can be identified.

## Upgrade checks after live sign-in

The authenticated Pi check found an older protection table with a `name`
column. The new migration preserves saved identities, normalizes their MACs
and adds an unknown creation time of zero. Invalid or excessive entries roll
back the migration. The connected-client list and compiled plan depend on
this upgrade.

The router import now has a separate 90-second helper timeout because router
login and AP activation can exceed the ordinary eight-second request limit.
Ordinary helper reads keep their existing timeout. Reset mode no longer polls
removed traffic tables. Failed or edited tokens release all private loading
states. The retrieval tooltip includes a terminal for the sudo password prompt.

The full Mac suite passes with 277 tests. New regressions cover legacy database
upgrade, rollback, repeated opening, slow router import, reset counter handling,
served dashboard authentication transitions and packet-check isolation.
Focused lint and whitespace checks pass. A fresh installation of the committed
package in a disposable Linux container also passes all 277 tests, the network
smoke check and the routed packet protection check. Authenticated Pi checks
remain the release gate.
