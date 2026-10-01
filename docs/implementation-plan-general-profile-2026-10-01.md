# Apply a saved profile to the whole test network

## Human review summary

Add a saved-profile dropdown beside the dashboard's network controls with
**Apply to all** and **Stop general profile** buttons. Applying a profile should
affect every unprotected device on JitterSim Wi-Fi, including clients without
saved assignments and clients that join later. It requires no device selection,
schedule, duration or usage quota. The profile's own network settings still
apply.

Recommend a separate general profile setting that takes priority over saved
device, group, schedule, timer, progression and service profile rules. Keep
those rules saved and resume their current behavior when the general profile
is stopped. Devices in **Unaffected by profiles** always bypass the general
profile. Separately configured global access rules still apply.

Use one shared upload queue and one shared download queue for this mode.
Only unprotected devices share the selected profile's speed limit. Devices in
**Unaffected by profiles** use the normal unrestricted path and do not consume
that shared allowance. The shared speed limit was confirmed during planning.
This covers new arrivals without creating individual assignments or running
into the current 15-rule limit.

**Risk: high.** A rule-ordering mistake could affect every test client or break
protection. Human feedback confirmed that protected devices must also bypass
the shared speed limit. Implementation was authorized on 1 October 2026 after
reviewing the proposed behavior and this clarification. Use the recommended
priority over saved profile rules while general mode is active.

## Learning objective

Reason about which profile wins, how protection remains reliable and how one
network-wide action reaches future clients. The dropdown and form wiring are
routine work. Keep the user's implementation effort focused on the network
behavior and its verification.

## Current evidence

- `src/jittersim/api/server.py` contains the dashboard and its protected routes.
  **Apply current configuration** applies saved assignments. Applying a
  profile beside a connected client saves a device assignment.
- `src/jittersim/engine/eligibility.py` starts with saved devices and removes
  protected clients before profile selection. It does not include unassigned
  clients in the profile plan.
- `src/jittersim/engine/service.py` and `src/jittersim/engine/runtime.py` select
  profiles for individual saved devices. The background runtime can replace a
  manually applied plan when schedules or other rules change.
- `src/jittersim/engine/compiler.py` limits combined device and service rules
  to 15. Copying the selected profile into every device record would both alter
  saved configuration and fail to cover future arrivals.
- `src/jittersim/networking/renderer.py` applies global access rules before
  protection. Protection uses the client's Wi-Fi MAC address for upload and
  download. Ordinary unmarked traffic currently uses a normal queue.
- `src/jittersim/networking/browser_proxy.py` applies protected-client checks
  separately and otherwise requires a saved device assignment. This path must
  also understand general mode.
- `src/jittersim/config/storage.py` upgrades the database at startup and
  downloads a consistent configuration backup. The backup excludes random
  website choice history.
- The dashboard restores the last apply or reset after restart. The Pi
  installer preserves the database and restarts the helper, dashboard and
  browser proxy separately.

## Proposed behavior

1. Choosing a dropdown value only selects it. **Apply to all** saves and applies
   that profile immediately. Allow this even with no connected clients so later
   arrivals receive it. Show a useful empty state when no profiles are saved.
2. Show the profile name and whether it is active, paused or awaiting a
   successful network update. Disable repeat submissions while an action runs.
   Use the existing admin sign-in and visible error messages.
3. General mode takes priority over all other profile sources, including
   browser service profiles and quota fallback profiles. It also applies the
   selected profile's website rules, random variation and random outages.
   Profile-wide random changes are shared by affected clients in this mode.
4. Apply only to traffic through the configured test Wi-Fi. Router Wi-Fi clients
   remain outside this network. Preserve dashboard recovery, DHCP and other
   local traffic required for clients to connect and stay manageable.
5. Protected clients bypass every part of the selected profile in both
   directions. Their traffic must never enter the shared limited queues or
   consume the affected devices' shared speed allowance. Protection continues
   to follow MAC addresses through IP changes and reconnects. Global MAC and
   website access rules remain in force.
6. Do not replace or create saved device assignments. Adding or removing
   protection while general mode is active updates the live network through the
   existing protected control path.
7. **Stop general profile** clears the general setting and applies the saved
   device rules that are current at that moment. It does not promise normal
   traffic if those saved rules still shape it. Say this beside the button.
8. **Panic** and **Reset network** continue to pause all shaping without erasing
   settings. Remember the general selection as paused. **Apply current
   configuration** resumes it if still selected. Restart after a reset must not
   reactivate it. Restart after a successful apply restores it.
9. Suspend device, group, service and time quota charging during general mode.
   Sample before entry and establish fresh counter baselines on exit so this
   interval cannot trigger a delayed quota fallback. Preserve prior totals.
   Timers and schedules keep their existing clock times rather than being
   extended. A timer may expire before general mode stops.
10. Store a reference to the saved profile. Editing that profile follows the
    existing background change detection. Switching profiles resets its random
    state. Never report an invalid or missing profile as successfully applied.

## Implementation subtasks

1. **Plan review.** Independently assess profile priority, shared speed limits,
   protection, quota accounting and restart behavior. Incorporate human
   feedback before beginning production changes.
2. **Saved setting.** Add one optional general profile reference to the existing
   local database. Use a repeatable startup upgrade with no selected profile for
   older databases. Include the setting in configuration backups and
   configuration-version changes. Validate the referenced profile and prevent
   a future profile deletion from leaving an active broken reference.
3. **Network plan.** Extend the structured plan and its validation to represent
   general behavior directly. Share the same decision between immediate apply
   and background updates. Use a fixed number of general queues, not a rule per
   discovered client. Keep the existing individual-rule limits for ordinary
   mode. Keep all settings and message sizes within existing validated limits.
4. **Traffic handling.** Extend upload and download rules so protection is
   evaluated before general profile behavior. Keep global access rules ahead
   of protection. Route protected traffic through a separate normal queue.
   Do not place the shared speed limit on a parent queue that also carries
   protected traffic. Keep unprotected arrivals covered without waiting for a
   dashboard refresh or a new saved assignment. Apply profile website rules and
   outages separately from global access rules so protected clients bypass them.
5. **Browser traffic.** Extend the browser proxy's general-profile selection and
   destination checks. Unassigned test clients should work in general mode.
   Keep its existing test-network and public-destination restrictions. Require
   verified identity where needed for protection. Close affected existing
   tunnels on profile changes while preserving protected tunnels as intended.
6. **Runtime and controls.** Add protected read, apply and stop actions. Serialize
   these with background updates, protection changes and reset using the
   existing control lock. Validate and compile before committing a changed
   selection. Sample and reset quota baselines at mode boundaries. Repeated
   requests should safely reach the same requested state.
7. **Dashboard and status.** Add the dropdown and buttons near **Network
   controls**. Show the effective general profile beside connected clients
   without changing their saved assignments. Clearly label shared speed limits
   and saved rules that are temporarily overridden. Make traffic charts report
   the shared limit honestly. Keep keyboard access, labels and phone layout
   usable.
8. **Documentation and verification.** Explain the control, scope, protection,
   shared speed limit, stop behavior and recovery. Complete the checks below
   before the full change receives a senior review.

## Data, scale and failure handling

- Save only the selected profile reference in addition to existing settings.
  Do not create device records merely because a client connects. Keep existing
  authentication, dashboard device access and private backup handling.
- Reuse existing action history for apply and stop outcomes. Do not log tokens,
  passwords, request bodies or new client browsing history.
- Keep network work independent of the number of connected clients. Resolve
  the selected profile's bounded website list once per necessary update rather
  than once per device. Do not rebuild unchanged network rules every tick.
- General random choices must not create fake saved-device identifiers in the
  existing website-history table. If this mode reports choice counts, store
  bounded daily totals for the network scope with the same 30-day retention and
  backup exclusion. It must not multiply one shared choice by the client count.
- A failed hardware update may leave settings saved while cleanup returns the
  network to normal. The current applier attempts cleanup rather than restoring
  the previous plan. Report this distinction and cleanup failures honestly.
  A failed apply must not be shown as active. Reset must remain usable.
- The helper and browser proxy must understand the extended plan before the
  dashboard treats general mode as available. Check compatibility rather than
  allowing an older process to ignore new settings and report success.

## Verification and release

| Area | Required evidence |
| --- | --- |
| Scope | Assigned and unassigned clients receive the selected profile. A later arrival receives it without another click. An empty network can be prepared. More than 15 clients do not silently lose coverage in general mode. |
| Protection | Protected upload and download avoid delay, loss, speed limits, website rules and random outages. Reconnects, changed IPs and protection changes preserve this behavior. Global access rules still apply. |
| Priority | Device, group, schedule, timer, progression, service and quota rules cannot replace the general profile. Stopping it resumes current saved rules without changing saved assignments. |
| Shared behavior | Two affected clients share the configured speed limit while a protected client sends traffic at the same time. Verify upload and download separately. The protected client is not capped by that profile or charged against its shared allowance. Random changes and outages match the documented network-wide behavior. |
| Quotas | General-mode traffic does not consume existing quotas. Entering and leaving the mode does not charge the skipped interval or erase earlier usage. |
| Persistence | Fresh and old databases start without a general profile. Repeated upgrades preserve settings. Restart and backup reopening preserve the reference. Reset stays paused after restart. |
| Errors | Invalid profiles, old helper versions, failed apply, failed cleanup, failed stop and helper timeouts produce accurate states. No request bypasses authentication or changes protection as a side effect. |
| Concurrency | Apply, stop, reset and protection changes cannot race with a background update that restores an obsolete mode. |
| Dashboard | No token, wrong token, no profiles, busy actions, stale selection, page reload and narrow phone layout remain understandable. Keyboard users can apply and stop. |
| Browser traffic | Assigned and unassigned test clients receive general shaping through the proxy. Protected clients and global destination blocks keep their intended behavior. |

Use focused behavior tests for priority, protection, mode transitions, database
upgrades and failures. Extend real Linux packet checks for both traffic
directions and browser tests for the complete user action. Avoid tests that
only repeat button labels or trivial field mappings.

Run the existing Python 3.9, 3.11 and 3.13 checks plus the Linux and browser jobs
in `.github/workflows/wifi-recovery.yml`. Build from a clean checkout with
`tests/wifi/Dockerfile`. If shared build or service setup changes, verify those
changes in the clean container and CI rather than relying on a warmed local
workspace.

Deploy through the normal Pi installer with a private backup. Verify the
service restart order cannot temporarily mix an unsupported helper or proxy
with a new dashboard plan. Any required startup upgrade must happen
automatically. No manual per-install database edits or background data copying
are needed.

Before Pi acceptance, prepare a bounded test with two affected clients and one
protected client. Confirm real traffic, a later arrival, stop, Panic, restart
and access-rule behavior. Keep management access available throughout. Record
checks that could not be performed instead of calling the feature fully
verified.

Before rolling back to a release without general-mode support, reset and
verify normal traffic under the newer release. Preserve the database and
restore the previous code and service setup through the existing recovery
procedure. The older release must restore the reset state rather than apply
saved individual profiles. A code replacement alone is not sufficient if the
safe reset cannot be confirmed.

Implementation is authorized. Publishing the branch or deploying to the Pi
requires a separate request.

## Implementation and verification

Implemented the saved general selection, dashboard controls, shared upload
and download queues, protected-device bypass and browser-proxy behavior.
Stopping resumes current saved rules. Panic and Reset preserve a paused
selection across restarts. Saved quotas skip the general-mode interval.
The installer starts the updated helper and proxy before the dashboard
restores traffic rules.

- The complete suite passed with 527 tests on Python 3.9, Python 3.13 and the
  local Python 3.12 environment. The required lint checks passed.
- The clean Python 3.11 Linux image built successfully. Its full suite passed
  during verification. Later runs encountered an intermittent existing HTTPS
  recovery failure. The latest run had 526 passing tests with a failed recovery
  test and its teardown error.
- The same HTTPS handshake timeout reproduced on the unchanged branch head
  `5ee13db861c1240f06a638f10d690ec97abd18b1` using Python 3.13. Two following
  unchanged-head runs passed all 15 connection tests. This is a separate
  test-stability issue and must remain visible in a future release review.
- Real Linux packet checks passed for shared upload and download limits,
  protected traffic and counters, later arrivals, outages, website access
  rules, address changes and resets.
- Linux installer permissions, network smoke checks, dashboard identity,
  dashboard access under full packet loss and management-name recovery passed.
  The installer also passed the shell syntax check.
- Served-browser checks passed for general Apply, Stop, Panic, reload and
  failed Stop on desktop and phone layouts. Existing Wi-Fi recovery and
  dashboard-access browser checks passed.

The complete change was reviewed for rule ordering, shared queue placement,
mode transitions, message validation, authentication, quota accounting,
restart behavior and update compatibility. Review removed a redundant proxy
socket-mark operation and strengthened the proxy check to require exactly one
mark for each side of the tunnel.

No branch publication, remote CI run or Pi installation was performed.
Physical Wi-Fi client acceptance still requires the deployment checks above.
