# Live homepage pairing plan review

## Human review summary

The delivered reference and DNS packet acceptance are complete. The next
homepage step is a reviewed live identity and policy channel. Keep it off by
default and limited to named controlled test pages. Production implementation
has not started in this review.

Keep the proposed separate HTTPS listener and reuse the existing local trust
setup. Resolve its exact address, firewall behavior and startup rules before
implementation. Use the settled `netlab.local` name rather than the plan's
older `pi.home.arpa` default. Preserve dedicated capacity for Stop and Panic
inside the helper as well as in the web server.

Bind approval to both the applied plan and current saved protection. Plan
changes alone do not detect a newly protected device. Read the database
without running schema updates. Recheck the relevant versions before approval.
Define the final approval boundary explicitly: changes before approval disable
the effect. A navigation already issued within the short lease cannot be
recalled.

Preserve compatibility by omitting a zero chance from old-compatible saved
settings. Use automatic conversion of the current configuration before an
older release starts. A whole old checkpoint can discard newer protection
or other edits, so keep a private recovery copy without silently replacing
current supported settings or security state.

**Risk: high.** This introduces live browser credentials and peer verification.
Feedback on the listener and rollback choices is required before production
work. The central reasoning is how approval stays current without blocking
Stop or Panic. Source inventory and evidence collection are routine work.

## Exact review sources

The plan reviewed is
`docs/implementation-plan-live-homepage-pairing-2026-10-02.md` from
`c922427d5a4e3c65dfb9c045bfbf918d2bb089d9` in the coordination checkout.
That file was read on 3 October. It still describes an older integration base.
This review is saved separately to preserve the active owner's plan and branch.

| Source | Revision | Purpose |
| --- | --- | --- |
| Published integration | `cf2a6fe44ec245061839a44b09a57e0b28782a6a` | Identity, helper admission, applied plans, profile storage and firewall rules |
| Local combined candidate | `acccfa91c9f2b15514faff313c0b572c609f8d27` | New helper response ordering, inspected as proposed combined source |
| Trusted HTTPS, PR 65 | `0245124ebb96013a9d2269a7c0a20161135f3654` | Certificate selection and existing trust setup |
| One dashboard address | `a857dd3a76daf07f2c33e5f16736424826863d0d` | Settled name and installer behavior |
| Server concurrency, PR 70 | `c31b5cb503e96881c0ba2e33617a5c2abdc68f51` | Dashboard worker admission and Panic reserve |

The dependent HTTPS and concurrency work is not assumed merged or installed.
The active release owner keeps integration and deployment. This review does
not take over update, signing, GUI or live Pi work.

## Required plan refinements

### 1. Preserve control capacity through the entire request

The integration helper admits eight connections at
`src/jittersim/networking/helper.py:1242`. Its generic request path takes
`operation_lock` before dispatch at line 1215. Apply and Reset can wait only
three seconds for that lock. A policy action sent through this generic path
can therefore delay control writes even if its own identity work avoids
`_network_lock`. Four separate HTTPS workers do not resolve that coupling.

The plan already asks for reserved write capacity. Make it explicit at helper
admission, command execution and final snapshot handling. Expensive identity
or destination checks must not retain the generic operation lock. A timed-out
browser must not free a slot while the underlying work keeps running. Reject
excess work promptly and use one total deadline for the complete check.

The local combined helper releases its operation lock before transmitting the
response. Preserve that newer behavior when the final source is selected.
Verify slow reads, slow response readers, repeated cancellation and saturated
ordinary helper work while authenticated Stop and Panic complete. Include
idle TLS connections in the web listener bounds.

### 2. Check saved protection independently from applied plan generation

`save_unaffected_device` bumps the saved configuration version at
`src/jittersim/config/storage.py:1209`. It does not publish another applied
plan. Checking only applied plan generation can miss protection changed
through another dashboard request or the CLI while identity work is pending.

Read current protection and its configuration version together. Validate both
that saved version and the applied plan generation at the approval boundary.
Also confirm the browser session remains valid after the helper read. Missing,
changed or busy state returns disabled. Saved profile edits still cannot
supply the applied chance.

The ordinary repository constructor calls `_initialize_schema` at
`src/jittersim/config/storage.py:157`. Opening it in every policy worker is
not a read-only database design. Specify an actual read-only connection with
bounded lock waits, finite result sizes and no schema changes. Do not share
a thread-owned repository connection with listener workers.

Test a protection write during delayed identity verification without Apply.
Test session revocation during the same delay. Distinguish these from a
protection change after an approval has already issued a navigation.

### 3. Make the separate listener a deployment decision

The existing household input exception covers TCP 8080 at
`src/jittersim/networking/access.py:159`. The recovery guard also preserves
only that dashboard port plus DHCP at line 277. A new HTTPS port does not
inherit those exceptions. Do not add a listener then assume it remains
reachable during household restrictions or failed access restoration.

Document the exact policy port, bind interfaces and intended behavior during
an access transition. Disabled responses or temporary unavailability during
a transition are safe. Opening another control exception requires an explicit
narrow rule and packet checks. Management and routed callers must still be
refused for pairing even if a socket can reach the listener.

The one-address branch sets `netlab.local` as its default in
`src/jittersim/localname.py:17`. Reuse its trust setup and normal certificate
validation. The HTTPS branch chooses a neutral certificate for test-Wi-Fi
peers at `src/jittersim/api/server.py:10676`. Preserve that behavior rather
than expose the management certificate's additional names.

Startup failure or missing certificate files must disable pairing without
preventing the dashboard from serving Panic. Cover port conflicts, AP startup
order, certificate renewal, listener shutdown, older service units and
rollback. Keep the feature disabled until actual extension fetch trusts the
installed endpoint normally. Observe the real extension's Origin and
preflight behavior rather than infer it from an ordinary dashboard fetch.

### 4. Define rollback before writing the new chance

The profile writer serializes all dataclass settings at
`src/jittersim/config/storage.py:133`. The old reader passes those settings
straight into `Profile` at line 3432. Adding a field with a zero default does
not by itself preserve old-reader compatibility.

A read-only constructor check on the reviewed integration rejected the
illustrative proposed key `homepage_bounce_percent` even at zero:

```text
Profile.__init__() got an unexpected keyword argument 'homepage_bounce_percent'
```

This key is illustrative. No field or production implementation was added.
Omit an off field where the compatibility contract requires it. Before
rollback, stop approvals and convert the current configuration to the target
release's supported format through an automatic, repeatable operation. Keep
all supported current settings. Save a private copy for recovery. Coordinate
security-state preservation with the existing update owner.

Cover zero and nonzero values, export/import, old helper negotiation, a failed
conversion and an interrupted restart. Verify that the old reader can read
the converted configuration before starting the old services.

### 5. State supported policy winners and identity limits

The current proxy selector applies protection before service lists, general
mode, service assignments and device rules at
`src/jittersim/networking/browser_proxy.py:75`. Preserve a matching zero
chance. Specify how quota and everyone-else rules behave in the chosen final
compiler, including an unassigned but verified test client. Unsupported or
missing metadata must disable the effect rather than infer a saved chance.

Fresh kernel route, neighbor and station checks establish the repository's
network identity boundary. They do not attest human identity or prevent a
deliberate hardware-address clone. Restrict this release to controlled test
origins. Public DNS checks do not pin Chromium's later connection or eliminate
later server redirects.

## Manageable implementation steps after feedback

1. Resolve the listener address, helper admission and rollback contract in the
   owning plan. Refresh its exact dependency revisions.
2. Add off-compatible profile and applied-plan metadata with focused
   compatibility and rule-selection checks.
3. Add the fresh helper decision with saved-protection consistency and control
   capacity checks. Exercise concurrent writes and actual command deadlines.
4. Add bounded pairing sessions and the reviewed TLS listener. Test credential
   separation, replay, expiry, revocation and unauthenticated overload.
5. Add the live extension build and trusted HTTPS fixture journey. Preserve
   explicit actions, local Off, tab cancellation and restart behavior.
6. Review the complete combined change and run clean CI. The release owner
   handles installation and physical acceptance after safe recovery exists.

## Validation and remaining dependencies

This was a source and plan review. The only executed code check was the
old-profile constructor compatibility check above. No production source changed
and no new network endpoint was created. The local reference and DNS packet
suites were not repeated because PR 58 already delivered them.

At the fresh availability check, another JitterSim container and fourteen
unrelated containers occupied Docker. None was stopped or changed. Installed
Pi identity, safe independent recovery, normal extension trust and physical
three-device acceptance remain separate evidence requirements. No push,
merge, deployment, radio change or live traffic change occurred here.
