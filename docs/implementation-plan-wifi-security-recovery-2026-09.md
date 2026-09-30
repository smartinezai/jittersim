# Wi-Fi security and network name recovery

## Human review summary

Require WPA2 with AES for the Pi test Wi-Fi and add a quick way to restore its
name to `JitterSim`. Provide both `sudo jittersim wifi reset` and a dashboard
button labelled **Reset Wi-Fi name**. The user confirmed that the reset keeps
the current Wi-Fi password. It works without a router password or dashboard
service.

The code currently allows older WPA because it does not restrict the protocol
version. Both installation and later Wi-Fi changes need the same stronger
settings. WPA3 support must be proved on the actual Pi before offering it.

**Risk: high.** Activating new Wi-Fi settings disconnects its clients. The
debugging computer may have joined the Pi after the router name was copied.
Dashboard and SSH access are blocked from that test network, so a button alone
cannot recover a computer already stranded there. Keep a working management
connection and leave the router Wi-Fi available during testing.

**Decisions to review:** Use `JitterSim` as the recovery name. Reset only the name
and keep the current password. Enforce WPA2 with AES first. Keep router name
copying available with a clear connection warning.

**Approved scope:** WPA2 with AES and name-only recovery were approved on
30 September 2026. WPA3 remains a later hardware verification question.

**Open deployment questions:** Which Pi OS, Wi-Fi driver and NetworkManager
version are installed? What
working management connection can be used for the physical acceptance test?

**Review gate:** Review this summary and the proposed work before implementation.
Have the plan reviewed and address feedback before production code changes.

## Workspace and scope

- Base: fetched `origin/main` at
  `94fd4da3cb1f3505248701aea7a95b133b02c73e` on 30 September 2026.
- Local branch: `codex/wifi-security-recovery` in a separate managed worktree.
- The primary checkout is on `feat/service-profiles-and-pi-hardening`. Its
  unrelated edits and the other chat's Pi work must be preserved.
- The other branch changes the installer, router controls, helper and dashboard.
  Refresh the base and review overlapping changes before integration. Do not
  copy that branch's unfinished changes into this work.
- This reset changes the test network name. Existing experiment reset and
  Normal access keep their existing meaning. Saved profiles, devices, access
  rules and the upstream router stay intact.
- No push, merge or Pi deployment is included without explicit authorization.

## Evidence from the current base

- `deploy/raspberry-pi/install.sh` creates the `jittersim-ap` NetworkManager
  connection with `key-mgmt=wpa-psk`. It does not set protocol or cipher limits.
  The same installer preserves an existing connection's name and password.
- `src/jittersim/networking/applier.py` uses the same security setting for
  manual Wi-Fi changes and router imports. It stops and starts the connection
  after changing settings.
- `src/jittersim/networking/helper.py` exposes `configure-ap` and
  `router-sync-ap`. These operations need protection against overlapping
  requests when the reset is added.
- `src/jittersim/api/server.py` already has manual Pi Wi-Fi controls and the
  router copy action. Writes require an administrator session or bearer token.
  Session writes also reject requests from a different site.
- `src/jittersim/cli.py` already supports recovery commands over the local
  helper socket. The installer exposes the command at `/usr/local/bin/jittersim`.
- `deploy/raspberry-pi/jittersim-control-firewall.nft` blocks dashboard and SSH
  connections from the test Wi-Fi. Preserve this boundary.
- Current main uses `TestNet` for a new installation while the manual dashboard
  field uses `JitterSim-Test`. Use `JitterSim` consistently for the new default
  and recovery name. Upgrades must preserve intentionally chosen names.
- NetworkManager's official [security settings reference](https://www.networkmanager.dev/docs/api/latest/settings-802-11-wireless-security.html)
  says an unset protocol list allows WPA and WPA2. Restrict it to `rsn` for
  WPA2 and use `ccmp` for both pairwise and group encryption. The `wpa-psk`
  setting alone does not establish the security advertised by this Pi hotspot.

## Proposed behavior

### Stronger security

Apply the WPA2 protocol and AES cipher limits when creating or upgrading the
Pi connection, configuring it manually, copying router credentials or resetting
its name. A router's copied name and password must not copy weak security.
Unsupported stronger settings fail clearly. Never retry using legacy WPA.

Installer upgrades retain the active name and password. Include security
properties in its before and after comparison so an existing active connection
reports when a restart is still needed. Do not silently disconnect the current
Wi-Fi during installation. The runtime actions activate the new settings and
verify that the expected connection is active before reporting success.

WPA3 is a separate acceptance condition. Inspect the target Pi's software and
radio capabilities before deciding whether WPA2/WPA3 mixed mode is supported.
Do not infer hotspot support from a successful client connection to WPA3 or
from a configuration value accepted by NetworkManager. If WPA3 is required,
revise this plan around verified hotspot support before implementation.

### Console recovery

Add `sudo jittersim wifi reset`. Explain the disconnection and ask for
confirmation, with `--yes` for unattended use. The operation requires the
existing local helper but not the dashboard, its database or router credentials.
If the helper is unavailable, return a nonzero result with concrete recovery
guidance. Do not claim the network was reset or create a new database.

Restore the name to `JitterSim` without reading or changing the current password.
Limit the action to the existing `jittersim-ap` connection. A missing connection
fails clearly instead of selecting another Wi-Fi profile. Repeated resets
leave the same settings. Each confirmed reset restarts Wi-Fi because saved
security settings alone do not prove which security the running network uses.

### Dashboard recovery

Place **Reset Wi-Fi name** beside the manual Pi Wi-Fi controls. Explain that it
restores `JitterSim`, keeps the password and disconnects test clients. Confirm
before activation and disable repeated clicks while the request is pending.
On success update the name field and provide reconnection guidance. On failure
keep the form usable and report the actual result.

Use one administrator-protected write action shared with the console recovery.
Retain the existing checks on authentication and requests from other sites.
An unavailable helper disables or fails the action clearly. Do not expose a
password to the browser or request the router password for this operation.

Explain the name collision before **Copy router Wi-Fi to Pi**. A computer
already connected to the test Wi-Fi cannot reach these controls. Document
recovery through the management network or a local Pi terminal. Do not open
administration access from the test Wi-Fi as a workaround.

### Failure handling and concurrent work

Serialize name resets, manual Wi-Fi changes and router imports. A second
conflicting request should receive a clear busy result rather than create an
unbounded queue. Keep status reads responsive and keep router network requests
outside any broad lock that protects unrelated experiment operations.

Preserve prior name and security settings before changing them. If activation
fails, make one bounded attempt to restore and activate those settings. Keep
the password unchanged during name recovery. Distinguish an activation failure
with successful restoration from a failed restoration. Do not retry endlessly
or report success from a saved profile that is not actually active.

## Work slices

1. Review this plan and settle the WPA3 requirement.
   Establish the actual Pi capabilities and a usable management route.
2. Update the installer and common Wi-Fi change path to enforce WPA2 with AES.
   Preserve existing credentials, isolation, addressing and service ordering.
3. Add bounded name recovery through the privileged helper. Protect concurrent
   Wi-Fi actions and handle missing connections, activation and restoration.
4. Add the console command and administrator-protected dashboard button using
   that same recovery action. Cover confirmation and honest outcome reporting.
5. Update README, deployment instructions and roadmap. Explain copied-name
   risks, the command's dependencies and reconnection after a reset.
6. Run local checks and perform a senior review of the complete change. Refresh
   main and inspect overlapping work before any authorized final push or merge.
7. After separate deployment authorization, install the exact reviewed release
   and complete the physical acceptance checklist below.

## Data and access review

The name reset reads only the fixed connection's nonsecret settings and active
state. It writes the network name and required security properties. It does not
need to read, copy, index, return or log the current Wi-Fi password. Router
imports retain their current transient credential handling and local saved
NetworkManager password. Never log request bodies or secret-bearing command
arguments, output or exception details.

Keep the helper socket's existing owner and group restrictions. Use structured
commands with the existing timeouts and fixed connection name. Add no endpoint
that accepts arbitrary commands or file paths. Keep diagnostics and activity
entries limited to the action and safe outcome. This single-Pi feature has no
new user or tenant data store, migration, backfill or background queue.

## Verification

- Test actual security settings requested on fresh install, upgrade, manual
  configuration, router import and name reset. A test that only searches a
  source string is insufficient for the security behavior.
- Test name reset with an existing password, absent profile, repeated requests,
  stopped dashboard, unavailable helper, activation timeout, successful
  restoration and failed restoration. Verify unrelated network profiles and
  saved experiment state remain intact.
- Test simultaneous reset and router copy without interleaved Wi-Fi changes.
  Use bounded requests and make competing outcomes explicit.
- Exercise the console and API through the real local helper socket using
  harmless test commands. Verify unauthorized and cross-site requests cannot
  change settings. Include checks that failures do not expose credentials.
- Exercise button confirmation, cancel, pending state, success, failure and
  unavailable helper in a browser. Avoid tests tied to incidental copy text.
- Run the existing unit suite, focused lint and shell syntax checks. Current
  CI covers Python 3.9, 3.11 and 3.13, Linux network tests and browser journeys.
- Build and install a wheel in a clean environment. Check the console command
  and dashboard assets there. If shared build inputs change, verify in a clean
  container or CI. Build the Pi archive from the exact committed release.
- At plan creation no new production behavior had been implemented or tested.
  Implementation was authorized on 30 September 2026.

## Physical acceptance, deployment and rollback

Coordinate timing with the other chat before changing live Pi services. Keep
the router Wi-Fi on and verify that the management computer is on the router
network rather than the copied Pi network before starting. Record the current
Pi version and nonsecret connection settings. Keep credentials outside chat,
logs and test reports. Existing root-protected configuration must be preserved.

Verify both a new connection and an upgraded connection advertise WPA2 with
AES and no legacy WPA. Connect a Mac and a second representative device. If
WPA3 becomes part of the approved scope, verify its advertised mode and an
actual WPA3 client connection as well.

Copy a harmless test router name, then reset through the dashboard and console.
Check the name becomes `JitterSim`, the password still works and the router's
settings do not change. Repeat with the dashboard stopped. Check saved profiles,
client isolation, normal internet access and a profile apply/reset still work.
Reboot and verify the new name and stronger security persist. Read saved
settings and active state after each action. A health endpoint alone is not
acceptance evidence.

If activation fails, read the safe restoration result through management access.
Restore the prior connection settings and reactivate it there if automatic
restoration failed. A code rollback alone does not revert NetworkManager
settings. Use the previous verified release only when a software rollback is
also needed. Verify working administration and client access after recovery.

## Plan review notes

The name-only reset avoids password handling and solves the name collision.
Its main limitation is reachability: a dashboard button cannot be used from the
network that already blocks dashboard access. Console recovery also needs a
local terminal or working management connection. Treat these as explicit
operating requirements, not as evidence that the new feature is ineffective.

Before implementation, the reviewer should check whether WPA3 is mandatory,
the failure restoration rules and the planned integration
with the other branch. Before merge, review authentication, secret handling,
bounded work, concurrency, install compatibility and the physical test results.
No PR risk template is present on the inspected base. The high risk rating above
reflects possible loss of client and administration connectivity.
