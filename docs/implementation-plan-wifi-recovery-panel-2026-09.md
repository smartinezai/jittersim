# Make Wi-Fi recovery easy to find and copy router security

## Human review summary

Move **Reset Wi-Fi name** into the floating quick-actions panel beside the
existing password controls and Panic button. Label its result clearly:
**Restore JitterSim name** with a short **Keep password and security** note.
Keep its confirmation before restarting test Wi-Fi. Show progress and the
outcome near the clicked control.

When copying router Wi-Fi, detect and copy the security type from the same
router band as the name and password. A WPA2 router must produce WPA2 on
the Pi instead of the current forced WPA3. Cover personal WPA, WPA2, WPA3
and their mixed modes when the router response and Pi support can be verified.
Show the detected type before copying and verify the Pi's running Wi-Fi
before reporting success. Unknown or unsupported modes stop the copy.

Name recovery must preserve the current password and security type. Upgrades
must also preserve the selected type instead of silently forcing WPA3 again.
Keep the existing WPA3 default for a fresh installation and for new manual
settings. New manual settings must say that they select WPA3. A separate
manual security selector is outside this request.

Explain that a device asking for a WPA3 password needs the test network's
Wi-Fi password. When router Wi-Fi was copied to the Pi this is the router's
Wi-Fi password. The router administration password and dashboard token have
different purposes. Never display or collect the Wi-Fi password for name reset.

The main risks are interruption of test clients and incorrectly detecting
or applying security. Original WPA and WPA/WPA2 mixed mode use obsolete
security. Copy them only as an explicitly confirmed compatibility choice
with a visible warning. Missing router details must never trigger a guess.
The Pi may be unable to offer some modes even when it accepts saved settings.
Adding a fourth action also needs a phone layout check.

**Risk:** High because this now changes network authentication, privileged
Wi-Fi operations and upgrade behavior. No pull request risk template was
found in this checkout. Confirm the required category before opening a pull
request. Review the security settings and physical Pi evidence as well as
the dashboard.

**Decisions for review:** Keep security matching within the personal WPA
family. Reject open networks, WEP and workplace modes that require an
identity or certificate. Reset only the name and preserve security.

**Open questions:** Which modes can this Pi advertise and accept in hotspot
mode? Which security fields does each supported router firmware expose,
including the older HTML form? Did the original password prompt appear
when joining Wi-Fi or inside the dashboard? The feature must not depend on
answering that last question.

**Review gate:** Review this summary and the proposed work before implementation.
Have the plan reviewed and consider that feedback before authorizing code work.

## Current evidence

- Inspected `feat/service-profiles-and-pi-hardening` at
  `c38de994e258e44bcac937a3fece2a92dd637d44` on 30 September 2026.
  The separate unaffected-devices plan is unrelated and must be preserved.
  The deployed commit was not verified. The live dashboard contains the
  same reset button and three quick actions described below.
- `src/jittersim/api/server.py` places quick actions near line 636.
  The Wi-Fi name reset button is near line 1455 under
  **Manual Pi access-point fallback** far down the dashboard.
- The button sends one confirmed request to `/api/access-point/reset-name`.
  It disables itself while pending and reports success or failure.
  The route uses the existing protected helper action.
- `src/jittersim/networking/wifi.py` restores `JitterSim` and enforces WPA3.
  It preserves the password and makes one restoration attempt on failure.
  Its manual configuration path also forces WPA3 and does not currently
  restore all previous settings after a failed import.
- Router import copies the router's Wi-Fi name and Wi-Fi password into the Pi.
  The live action history includes a recent successful router import.
- `RouterWifiSettings` currently carries only the name, password and enabled
  state. Both router-reading paths discard security. The JSON response uses
  `ModeEnabled` and `ModeEnabled5G`. Existing tests contain WPA2 labels and
  numeric codes for no security, WPA2 and WPA/WPA2 mixed mode. Those codes
  are not evidence of WPA3 support. The older form's security fields still
  need verified examples.
- `deploy/raspberry-pi/install.sh` forces WPA3 on both fresh installations
  and existing profiles. The reset API and console also explicitly reject
  a successful result unless its security label is WPA3.
- `tests/browser/wifi_recovery.py` already checks cancellation, failure,
  pending requests, success and an unavailable helper. Its current layout
  checks scroll down to find the reset button.
- `.github/workflows/wifi-recovery.yml` runs Python checks, clean Linux
  container checks and the browser recovery journey for this release branch.

## Learning focus and proposed work

The central reasoning is translating a verified router security choice into
the Pi's real running configuration across import, recovery and upgrades.
Saved settings alone cannot prove that the radio offers the expected mode.
Repositioning markup and updating documentation are the mechanical parts.

1. Review the expanded plan and confirm the security and recovery decisions.
   Obtain representative router responses and inspect Pi hotspot support.
2. Extend router settings detection with the security of the selected band.
   Cover the JSON and older-form paths without breaking the router toggle.
3. Add a protected security preview for the copy action. Return the network
   name, source band and security description without any Wi-Fi password.
   Show that description in the copy confirmation. Include a legacy-WPA
   warning only when that type was actually detected.
4. Carry the verified choice through the API, helper and Wi-Fi controller.
   Read router settings again when the confirmed copy runs. If its name,
   band or security changed after the preview, require a refreshed preview.
   Never trust a browser-provided security label as the source of truth.
5. Apply and verify each supported mode with bounded failure recovery.
   Preserve the previous name, password and security if activation fails.
6. Make name reset preserve security. Update its API and console result
   checks so a verified WPA2 result is accepted after a WPA2 import.
7. Preserve existing security on installer upgrades and service restarts.
   Keep the fresh-install WPA3 default and address old-helper compatibility.
8. Move the single recovery control into quick actions. Keep one active
   control and the existing operation so a second button cannot create
   inconsistent progress or allow repeated requests.
9. Confirm the restored name, preserved password and security plus client
   disconnection. Put its progress and final result
   where the user can see them from the floating panel.
10. Add short guidance beside the manual Wi-Fi form and router-copy action:
   use the test Wi-Fi password to join and use the copied router Wi-Fi
   password after import. Do not imply that an empty form field means
   the network has no password.
11. Check wide desktop and phone layouts. Keep the new control reachable
   while scrolling and leave room for the password panel and page content.
12. Update README and Pi recovery instructions for the new location,
    matching rules, supported modes and failure recovery.
13. Run the checks below and review the full change before any separately
    authorized publication.

## Matching rules and unsupported settings

Treat each personal mode as a distinct requested result: WPA only, WPA2 only,
WPA3 only, WPA/WPA2 mixed and WPA2/WPA3 mixed. A mixed mode must preserve
both advertised choices. Do not silently turn it into one of its components.
Match relevant encryption and protected-management requirements when the
router exposes them. If those details are unavailable, inspect whether the
router mode has an unambiguous supported meaning before permitting import.

Take the name, password and security from the same enabled source band.
Prefer the Pi-compatible band. If several bands expose conflicting settings
and the source is ambiguous, ask the user to select the source before copying.
Do not infer security from the password's length or the network name.

Use an explicit set of supported settings. Reject missing fields, unknown
values, invalid numeric codes, open networks, WEP and enterprise modes before
changing the Pi. Keep old firmware supported only where its security can
be verified. A mode accepted by NetworkManager is not proof of hotspot support.

The official [NetworkManager security settings](https://www.networkmanager.dev/docs/api/latest/settings-802-11-wireless-security.html)
distinguish protocol versions, authentication, encryption and management
protection. An unrestricted protocol list permits both WPA and WPA2.
Therefore a single generic password setting is insufficient to prove an
exact copy. Mixed WPA2/WPA3 hotspot support must be verified on the target
software and radio before offering it.

Apple's [router guidance](https://support.apple.com/en-us/102766) identifies
original WPA and WPA/WPA2 mixed mode as obsolete security. The requested
compatibility feature may support them with a specific warning and explicit
confirmation. It must never choose them as a failure fallback.

## Verification

- Router tests: each supported named or verified numeric mode, both bands,
  differing band settings, legacy forms, missing fields and malformed data.
  Confirm that a security read does not change the router or Pi.
- Wi-Fi tests: switching between modes clears incompatible previous
  settings. Verify running authentication and encryption as well as saved
  values. Cover unsupported radio behavior, activation failure, one bounded
  restoration attempt and preservation of the previous credentials.
- Helper and API checks: authentication, requests from other sites, invalid
  settings, old helpers, concurrent operations and stale preview rejection.
  Confirm that responses and logs never expose Wi-Fi passwords.
- Reset and installer tests: preserve each copied mode across name recovery,
  upgrades and restarts. Cover an existing WPA3 installation and retain the
  fresh-install default. Exercise interrupted changes and truthful recovery
  outcomes.
- Browser journey: security preview and confirmation, legacy warning,
  unsupported-mode feedback, pending state, failure and success. Verify that
  recovery is visible without scrolling on desktop and phone. Retain the
  existing cancellation and unavailable-helper checks.
- Run the existing release workflow including its clean Linux container
  build. Check that security-verification commands exist in the installed
  environment. Test fixtures and container checks do not prove radio support.
- On the Pi, connect a client for each claimed supported mode. Inspect what
  the access point advertises. Mixed WPA2/WPA3 requires evidence that both
  kinds of client can connect. Test recovery and reboot persistence through
  a working management connection.

These are meaningful regression and branching checks. Avoid unit tests that
only repeat labels or markup placement.

## Access, failure and growth checks

Keep the existing administrator requirement and rejection of requests from
other sites. Do not expose administration access from the test network.
The reset must need neither the router administration password nor a new
Wi-Fi password. Do not read secrets to explain which password to use.
The security preview must never return the Wi-Fi password. Import retains
its existing transient use of router credentials. Restore credentials only
inside the privileged process through a protected local recovery copy.
Keep it root-accessible, remove it when recovery is complete and handle an
interrupted import on the next start. Never log secret-bearing request
bodies, command arguments, router responses or recovery copies.

Keep one bounded request per confirmed action. Preserve the disabled state
while pending and the helper's refusal of overlapping network changes.
Report failures without claiming that a name or password changed. Use one
bounded read for preview and one for the confirmed import. Keep router
network calls outside broad locks. Do not add automatic security polling or
unbounded retries. The work remains independent of connected-device count.
No dashboard database migration or background job is needed.

## Delivery and recovery

Use the existing release workflow and clean build checks. Check any new
security command or dependency in a clean build and on the Pi. No manual
per-environment data update should be needed. Package the exact
reviewed revision through the existing Pi release process after publication
and deployment are authorized.

Read the installed version and inspect the authenticated live dashboard
after installation. Verify the panel's location first. An actual reset
requires an agreed interruption window and management access. Then verify
the name and security from a separate test device and reconnect using the
unchanged password. Reboot and upgrade checks must prove that the selected
mode remains in effect. A screenshot alone does not prove Wi-Fi recovery.

Reverting the interface restores the previous layout. Reverting the code
alone cannot be assumed to restore copied NetworkManager settings. Older
code requires WPA3 and may force it again during installation. Define and
test a rollback that restores the previous working profile through management
access before running that older version. Keep the existing
`sudo jittersim wifi reset` route available with the new name-only behavior.

## Plan review observations

This is now broader than moving a button. Changing only router import would
fail because name reset and upgrades force WPA3 again. Existing security
decoding serves router writes and recognizes only a limited set of values.
Do not expand that write behavior accidentally while adding import detection.

The key acceptance gaps are reliable firmware detection and real hotspot
support. The layout gap remains because browser checks currently scroll to
the button before taking screenshots. This expanded scope awaits review
before implementation under the personal working agreement.

## Implementation authorization

The owner approved implementation, testing, publication, deployment and README
updates on 30 September 2026. The work uses an isolated checkout based on
`c38de994e258e44bcac937a3fece2a92dd637d44`. The original checkout and unrelated
plan are preserved.

At the next checkpoint, explain why a saved Wi-Fi mode does not prove what
the active radio supports. This is the selected comprehension check after
the authorized implementation.

## Implementation verification

The requested implementation was authorized on 30 September 2026. The complete
local suite passed with 429 tests. Browser checks covered copy confirmation,
cancellation, reset progress and recovery on desktop and phone. Clean Linux
checks passed for network setup, packet exemptions and management name queries.

The physical Pi offered WPA2 and WPA3 with name reset preserving each mode.
WPA and both mixed modes failed verification and restored the previous profile.
The original Wi-Fi was restored after testing. Client handshakes for each mode
remain unverified. Deployment and final release checks are recorded separately.

