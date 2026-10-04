# Device exemptions and connected-client controls

## Human review summary

The running release has no saved list of devices that are unaffected by profiles. It already detects clients on the Pi's Wi-Fi and can assign a profile to a selected client. Groups currently accept only previously saved assignments. Add a persistent **Unaffected by profiles** list and let the owner select detected clients for profiles and groups without entering their addresses.

An exemption must override every profile path, including schedules, timers, progressions, random changes, service profiles and quota fallbacks. Exempt traffic must not consume device or shared group quotas. Keep an exemption when a device disconnects or changes IP. Continue applying the separate global website and MAC access rules. Exemption must not grant access to the dashboard or SSH.

Fix the demonstrated address-reuse bug as part of this work. The current connected-client action looks up a saved record by IP before checking its MAC. A different device that receives that IP can inherit the original device's ID and groups. Match saved identities by MAC and reject conflicting addresses rather than copying another client's record. Also make profile feedback explain any active group or timed override.

**Risk: high.** This changes privileged traffic rules as well as saved configuration. Protect the actual Wi-Fi MAC during packet processing rather than relying only on a prior IP lookup. Test the complete path from saved settings to generated traffic rules. Hardware acceptance requires two clients connected to the Pi. The Pi currently reports a working station lookup with zero connected clients.

**Decisions proposed for approval:** Exemptions concern profiles only. Global access rules remain active. The list contains up to 256 saved MAC addresses with optional names. Unsaved clients can join a group with a selected group profile, which supplies their saved profile. Existing saved clients can also join groups without a profile override. Additional feature ideas will be recommendations after the requested tests.

## Current evidence

- Current repository and deployed source are at `a4082c7` on `feat/service-profiles-and-pi-hardening`.
- There is no exemption model, table, endpoint or dashboard control.
- `/api/connected-devices` reads associated Pi Wi-Fi stations and joins local address and DHCP details. Unknown connection status already disables assignment.
- `/api/connected-devices/apply-profile` verifies the current IP and MAC before applying, but its record lookup can reuse another device's saved identity by IP.
- `/api/groups` accepts saved device IDs. The dashboard's member selector is populated from `/api/devices`, so unassigned connected clients cannot be selected.
- Both `compile_stored_plan` and `RuntimeScheduler` select profiles. Quota sampling is a separate path and needs explicit exclusion.
- Saved configuration is included in downloaded database backups. Analytics remain excluded from backups.

## Implementation steps

### 1. Preserve device identity

- Add a bounded lookup by normalized MAC. Preserve an existing client's ID, name and group memberships when its IP changes.
- Reject duplicate or conflicting saved identities and live IP matches. Never silently replace another known MAC at an existing address.
- Keep legacy assignments with no MAC usable where their identity can be verified. Bind a current verified MAC before using them with exemptions.
- Use MAC matching for displayed assignments and group details too. A reused IP must not display another client's saved identity.
- Fetch one bounded station snapshot for each relevant apply or scheduled evaluation. If exemptions are configured and a target cannot be checked safely, omit its profile rules and report the uncertainty. Remove stale profile rules rather than retaining them after an identity lookup failure.
- Enforce upload protection using the packet's source MAC after global access checks and before profile blocks, traffic marks or usage counters.
- Enforce download protection using the packet's destination MAC at the test interface's outgoing packet hook. Clear its profile mark before traffic shaping. Move download device and service counters to that hook after protection is checked, so a protected client cannot consume another client's quota when it takes that IP. Read both fixed project-owned counter tables with bounded commands.
- Include saved protected MACs in the validated network-plan format even if those devices have no saved profile or known IP. Protect all addresses belonging to a MAC and cover address changes between inspection and apply.
- Probe the required outgoing packet hook during installation before changing live rules. Unsupported kernels must fail the feature preflight clearly. Keep both project-owned rule tables within apply, cleanup, reset and rollback operations.

The proposed download hook can inspect the Ethernet destination before traffic shaping according to the [official nftables manual](https://netfilter.org/projects/nftables/manpage.html#_netdev_address_family). Packet tests on the actual target kernel remain required.

### 2. Persist and enforce exemptions

- Add an idempotent SQLite table keyed by normalized MAC with an optional display name and creation time. Cap it at 256 entries inside the write transaction.
- Expose protected list, add and remove actions with useful validation errors. Include saved exemptions in configuration backups.
- Use one shared eligibility decision in stored-plan compilation and runtime selection. Filter targets before selecting direct, group, scheduled, timed, progression, random, service or fallback profiles.
- Ensure every profile-derived queue, outage, website filter and browser service rule excludes protected clients. Preserve global access rules.
- Skip service assignments that belong to protected clients explicitly. Do not treat those deliberately excluded clients as broken references that abort the new plan.
- Preserve verified protected-client bindings for the browser proxy and send their traffic without a profile mark. Verify the peer's current Wi-Fi MAC before choosing a proxy profile while exemptions exist. Unverifiable identity must not cause a protected client to receive a profile.
- Recheck protected proxy tunnels when a plan changes. Keep them open when only another client's profile changes. Close them when their own protection or global destination permission changes. Keep the existing incoming MAC policy and global destination checks active.
- Exclude protected devices from device, service and time quota updates. Shared group totals include only eligible members.
- Rebase device and service counter baselines, eligible group member baselines and timed-quota sample times when protection changes. Preserve accumulated usage and scheduled reset bookkeeping. Do not charge an exempt interval after protection is removed.
- Use the same eligible group members for automatic sampling and manual quota resets. Check exemption removal and counter resets as well as addition.
- Serialize exemption edits with scheduler apply operations. A failed helper update must show that protection has been saved but is not yet confirmed on the network. Do not report a successful live change.

### 3. Make connected-client actions complete

- Add an **Unaffected by profiles** control on each connected-client row. Disable profile application while it is active.
- Keep a separate saved unaffected list visible for offline clients with names and a remove action.
- Add group selection to connected rows and let the group form select currently connected clients alongside saved devices. Reuse detected IP and MAC details.
- For an unsaved client, require a selected group profile before saving its first assignment. Save that assignment and group membership together so failure cannot leave half a change.
- Preserve unrelated group members and enforce the existing one-group-per-device rule and 15-member limit. Explain an existing membership conflict.
- Validate the complete proposed plan before committing connected-client changes. The existing maximum is 15 combined device and service rules across all groups. Check exemption removal at this limit too. A failed validation must preserve the previous records.
- Show saved assignment, group membership and exemption state. Feedback must distinguish a saved base profile from an effective group or timed override.
- Preserve pending selections during refresh. Provide clear states for no connected clients, no profiles, no groups, missing IP, an expired selection, authentication failure and helper failure.
- Explain that this panel shows clients on the Pi's Wi-Fi. Router Wi-Fi clients require a separate router integration and cannot be inferred from the Pi's station list.

### 4. Verify the whole feature

- Start with the existing test suite. Add regressions for normalized MAC matching, changed IPs, reused addresses, stale selections, conflicting groups and partial write failures.
- Test exemption creation, removal, restart, offline visibility, backup contents and the 256-entry limit. Check protected endpoint authentication through HTTP.
- Test both manual and scheduled plans with a protected client and an affected client. Cover direct and group profiles, schedules, timers, progressions, random outages, website changes, service profiles and every quota fallback.
- Check quota accounting before exemption, while exempt and after removal. An exempt group member must not cause a fallback for another member.
- Inspect generated Linux commands and extend the isolated Linux network test. Send traffic in both directions with one protected MAC and one affected MAC. Reassign an affected IP to the protected MAC after inspection and verify normal traffic plus zero profile-quota consumption. Repeat with multiple addresses on the protected MAC. Verify clear failure behavior when station identity cannot be checked.
- Keep a protected proxy tunnel active while another client's scheduled or random profile changes. Verify new protected tunnels and existing ones without weakening global access rules.
- Exercise browser workflows with detected clients. Check desktop and narrow layouts in one inspection round, fix defects together then confirm once. Do not mistake static HTML text assertions for working controls.
- Run the full suite, focused lint and diff checks. Review the complete change for bounded work, identity safety, authentication, data preservation and recovery.

### 5. Release checks and remaining recommendations

- Build the exact tested commit from clean source using the existing installer. Preserve the database and credentials. Use a readable package installation mask while keeping backups private.
- Verify installed source hashes, services and protected dashboard endpoints.
- A real network test needs one exempt client and one affected client. Verify both directions of shaping and confirm that changed group or timed settings never affect the exempt client.
- Before rolling back to software that lacks exemptions, apply and verify a normal reset while the new release is running. Replacing code alone is not a safe rollback for saved exemptions.
- After testing, report remaining gaps and a short ranked list of useful features. Do not expand the implementation into unrelated features without approval.

## Review gate

The personal working agreement requires a written plan, a separate review and human feedback before implementation. This follow-up extends the earlier exemption plan with connected-client group controls and identity fixes. The review identified packet identity protection, proxy behavior, quota baselines and complete-plan validation as required corrections. They are included above. The owner approved this reviewed scope on 30 September 2026 with “Proceed with the reviewed plan”.

## Live dashboard follow-up

The live check found that connected clients, the upstream connection and the
runtime summary still looked busy before a token was checked or after a failed
check. Update those panels together with the existing protected panels. A token
edit must stop private polling until the new token is checked. Exercise empty,
rejected, failed and successful checks using the served dashboard functions.

Run the packet check in a separate network namespace on the Pi to verify its
actual kernel without replacing live network rules. Require a namespace that
differs from the host and contains only loopback. Deploy the tested source and
run authenticated checks through the existing administrator workflow.

The authenticated check also found the Pi's original protection table has only
`mac_address` and `name`. Upgrade it in one transaction by renaming the name
column and adding an unknown creation time of zero. Preserve entries and
normalize their MACs. Reject repeated identities or excessive rows without
partially changing the table. Test reopening, editing, deletion and rollback.

The router import client currently gives up after eight seconds even though
login, encrypted reading, AP activation and logout can take longer. Give this
specific operation a bounded 90-second wait. Keep ordinary helper reads at their
existing timeout. The token tooltip must use `ssh -t` for the sudo prompt.
