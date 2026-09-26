# Device access, blocked sites and field help

## Review summary

MAC filtering and a global blocked-site list are still available under
Rules & limits > Access rules. Make those controls easier to find and use.
Add clear help beside every network setting. Extend access rules so an owner
can block named sites or services for a child device, a group or the whole
test network without creating an impairment profile.

Keep the existing dark navy theme throughout the app. Use Impeccable's
guidance for focused improvements to clarity, spacing, form layout and
accessibility. Do not introduce a light background. The design constraints
and current colors are recorded in `DESIGN.md`.

The important distinction is enforcement. Today's MAC rules block network
traffic after Wi-Fi admission. Today's website rules resolve names to IPv4
addresses and can miss subdomains or affect unrelated sites sharing an
address. Reset also removes these rules. Those behaviors suit a network
experiment but do not meet the proposed household-access behavior.

The proposed access controls stay active when an experiment stops or a profile
changes. Rejecting Wi-Fi joins requires a supported access-point control path
and real Pi tests. Website blocking should cover domain names and subdomains
through the Pi's DNS service. It must explain that a VPN, another connection
or encrypted DNS over ordinary HTTPS can still bypass network-only controls.
Do not describe this as guaranteed parental protection.

The full plan was approved on 26 September 2026 with the instruction to
implement it and improve site blocking. Household rules survive ordinary
experiment reset. Removing them requires an explicit access-rule change.
Use Unbound views for per-client DNS policies with bounded rule lists. Keep
Wi-Fi admission controls unavailable until the AP backend supports them.

## Current evidence

- GitHub main was checked on 26 September 2026 at
  `2cb1414faeeeea040721679b4675fcb7675912f5`.
- `src/jittersim/api/server.py` contains the MAC picker, allow/block modes,
  global website lists and Save access rules. Saving needs a subsequent Apply.
- `src/jittersim/networking/renderer.py` drops input and forwarded traffic from
  denied MAC addresses. Its reset command removes the project rules table.
- `src/jittersim/engine/website.py` resolves literal saved names to IPv4
  addresses. It has no domain-suffix rule at the network boundary.
- `deploy/raspberry-pi/install.sh` uses NetworkManager for Wi-Fi sharing.
  Client isolation is on by default and client IPv6 is disabled.
- The existing question marks use CSS hover or keyboard focus. Delay, jitter,
  loss, bandwidth, reorder, duplicate and corrupt lack equivalent base-value help.
- 82 existing model, rule rendering, proxy, device-state and access-rule API
  tests passed locally. This is baseline evidence, not verification of new
  features or a Pi deployment.

## Work packages

### 1. Make access rules visible and understandable

Add a direct Access rules link from Devices. Show whether a device is allowed,
blocked or waiting for Apply. Keep the mode and the effective list visible.
Offer a device picker and a manual MAC entry for devices that are offline.
Use the Wi-Fi MAC reported by the device and explain private addresses.

Keep separate explanations for blocking traffic and refusing a Wi-Fi join.
An Unaffected device bypasses experiment effects only. It must not bypass an
explicit household block. Make blocked-site rules visibly independent of
profile choices and show the rule responsible for a block.

### 2. Keep household rules independent of experiments

Separate persistent access rules from temporary traffic effects at storage,
helper and Linux rule ownership boundaries. An ordinary experiment reset,
failed profile application or scenario transition must preserve the last
successfully applied access policy. Provide an explicit authenticated action
to disable access controls with a clear explanation of the resulting access.

Apply changes as checked replacements and retain the previous policy on
failure. Load access rules before making the test network available at boot.
Preserve wired management access if restoring the rules fails. Label saved,
applied and failed changes separately. Serialize simultaneous rule changes
and reject edits based on an obsolete settings version.

### 3. Enforce Wi-Fi admission when supported

Verify the Pi's Wi-Fi controller, driver and current access-point service.
Prove a supported method for rejecting listed MAC addresses before showing
a Wi-Fi admission switch. NetworkManager's similarly named
`mac-address-denylist` selects local adapters to which a connection may apply.
It is not a connected-client deny list.

If a dedicated access-point service is needed, isolate that change to the
test interface. Preserve the SSID, password, client isolation, DHCP range,
DNS service, management firewall and gateway behavior. Update discovery's
lease-file handling. Existing connected clients must lose access immediately
when blocked and must not regain it by rejoining with the same address.
Retain the firewall rules as a second check.

Keep explicit limits on lists and reject empty allow-only configurations.
A changed or spoofed MAC remains a separate device identity. Do not claim
that MAC filtering can authenticate the person using a device.

### 4. Add site and service blocks

Add named block rules for one device, one group or the whole test Wi-Fi.
Each rule has an enabled switch and a bounded list of domain names. Include
subdomains by default and make that behavior visible. A service is a named,
editable collection of domains so users can block an app across its hosts.
Existing DNS observations can suggest entries. Do not promise complete
coverage from a guessed service list.

Use a maintained DNS server for domain filtering rather than implementing
a DNS packet parser in the dashboard. Verify support for device/group policy
selection before choosing the integration. Plain DNS requests from the test
network must use the local filter, including requests addressed to another
resolver. Keep DHCP and management traffic working. Block standard encrypted
DNS ports when this mode is enabled and describe the remaining HTTPS/VPN
bypass clearly. Preserve the existing IPv6 isolation boundary.

Apply equivalent hostname checks to the existing browser proxy so it does
not become a bypass. Handle connection reuse, existing tunnels, cached DNS
answers and changing addresses explicitly. Avoid globally blocking a shared
hosting address merely because one selected domain uses it. Global blocks
win over narrower rules. Changing a profile must not clear a block.

The first release contains user-defined domain lists. Automatic content
categories, remote list subscriptions, TLS interception and attempts to block
every VPN are outside this scope.

### 5. Add accessible question-mark help

Cover upload and download delay, jitter, loss, burst loss, bandwidth, reorder,
duplicate and corrupt. Also cover random seed, website mode and domain entry.
Explain the unit, the zero or blank behavior and a short concrete example.
Distinguish jitter within a connection from periodic profile variation.

Use one shared help interaction for existing and new controls. It must open
on hover, keyboard focus or tap, remain readable and close with Escape or an
outside tap. Expose the explanation to screen readers. Keep it within the
viewport at 320 pixels and avoid covering the field being edited. Opening
help must not submit a form or discard a draft.

### 6. Refine the existing dark interface

Follow the existing-design workflow described by Impeccable. Keep JitterSim's
dark navy background, blue actions and green traffic indicators. Preserve
dark surfaces in menus, dialogs, tooltips, loading states and error states
even when the operating system uses a light theme.

Make the current device state and next useful action easier to see. Reduce
competing borders and nested panels where spacing and headings are enough.
Group upload and download fields consistently. Keep advanced options folded
away and use readable labels with nearby units and help. Consolidate repeated
colors, spacing and controls so new access rules fit the rest of the app.

Check the rendered result on desktop and phones. Verify contrast, visible
keyboard focus, touch targets, zoom, long device names and reduced-motion
preferences. Retest draft preservation and the Save/Apply distinction after
layout changes. This is a refinement of the current app, not a new visual
identity. The Impeccable documentation has been reviewed. No plugin or
automatic hooks have been installed as part of this planning work.

## Storage, privacy and bounds

Reuse sign-in and write-origin checks for every private read and mutation.
Keep privileged operations in the narrow helper boundary with validated
values and no shell interpolation. The dashboard must not write service
configuration as root or expose the DNS service outside the test network.

Store rule definitions and targets locally. Do not record page contents,
complete URLs or browsing histories. Log configuration changes and failures
in the bounded activity log. Any blocked-request diagnostics must be opt-in,
bounded and deletable. Do not send client identities to an upstream resolver.

Retain the existing 256-address MAC limits. Define a total rule/domain budget,
bounded request size, finite DNS timeouts and a capped work queue before
implementation. Reuse policy snapshots rather than rebuilding all rules on
each dashboard poll. Domain caching must respect rule changes and must not
serve another device's permission result.

Include new rules in settings export, preview, history restoration and test
session snapshots. Upgrade old settings with filtering behavior unchanged.
Version exports when older readers could silently discard protections.
Validate target deletion and group membership changes in one transaction.

## Deployment, recovery and verification

The repository has no PR risk template. Treat the access-control work as high
risk because it changes network admission, DNS and recovery. Tooltips alone
are low risk. Review the network and security behavior before release.

Provision services and migrations through the installer. Re-running the
installer must produce the same configuration without losing credentials or
rules. Check service configuration before activation. Back up the database
and access-point configuration. Define how rollback restores both together
and avoid silently discarding new protections on a downgrade.

Verification must cover:

- Phone and keyboard journeys for all help controls, all rule targets,
  validation, draft preservation and saved-versus-applied status.
- Disabled filtering, both MAC modes, unknown clients, MAC changes, group
  membership changes and conflicts with Unaffected devices.
- Domain suffix boundaries, changing DNS results, shared hosting, DNS over
  TCP, external DNS attempts, proxy tunnels and the documented bypass limits.
- Reset, reboot, interrupted apply, failed DNS restart, concurrent edits,
  rollback and retention of the previous working protection.
- Clean Linux builds using the actual DNS/AP tools, Linux network tests,
  browser journeys and supported Python versions in CI.
- Real Pi tests of refused Wi-Fi admission, client isolation, direct and
  proxied browsing, startup recovery and a working wired management path.

Use fewer larger coherent commits. Split UI/help work from network and
deployment work only when that makes review or rollback clearer. Keep code
local until the finished change has been reviewed, tested and authorized
for publishing. Hardware acceptance remains unverified without Pi access.

## Design references

- [Impeccable: improve an existing design](https://impeccable.style/docs/improve-design/)
  covers focused changes while preserving existing colors and behavior.
- [Impeccable: work with a design system](https://impeccable.style/docs/design-system/)
  describes recording visual decisions and reusing them across new screens.
- [NetworkManager Wi-Fi settings](https://networkmanager.dev/docs/api/latest/settings-802-11-wireless.html)
  distinguish local adapter selection from access-point client isolation.
- [dnsmasq manual](https://thekelleys.org.uk/dnsmasq/docs/dnsmasq-man.html)
  documents domain and subdomain replies, record-type behavior and
  configuration checks. Per-client policy support still needs verification
  before selecting this or another maintained resolver for the full scope.

## Approved addition: normal-access panic button

On 26 September the scope was extended to include a master control that turns
off all profiles and blocking immediately. The button performs no confirmation
step and does not depend on clean forms or a current settings version. It keeps
saved settings, pauses automatic changes and persists the disabled access
state. Ordinary Reset keeps household restrictions active.

The helper attempts both experiment cleanup and access-rule removal even when
one fails. The UI reports a partial failure without claiming normal access.
Disabling filters does not require a working filtered DNS service. Existing
DNS connection mappings are removed so they cannot retain an old redirect.
The command is also available through `jittersim normal-access` over SSH.
Management security and Wi-Fi isolation remain enabled.

## Implementation and verification notes

Persistent policy files and separate nftables tables now keep household rules
independent of experiment resets. Unbound provides client-specific local zones.
The browser proxy checks the same hostname rules and closes existing tunnels
when policy changes. Policy replacement has a temporary guard, durable writes,
process locking and recovery to the previous working configuration.
MAC restrictions also check traffic leaving the Wi-Fi interface so an existing
one-way download stops when its device is blocked. Scheduled experiment
changes preserve the applied access policy and cannot activate pending edits.

The dark interface includes named rules, direct device actions, applied-state
labels, shared accessible help controls and the normal-access button. Browser
checks cover narrow screens, keyboard use, drafts, light system preferences,
reduced motion and reload persistence. Linux checks use real Unbound,
nftables, connection tracking and hostapd controller commands.

The standard AP backend remains NetworkManager. Refusing a Wi-Fi association
is capability-gated. Actual radio behavior, a backend change and Pi hardware
acceptance cannot be marked verified from the controller-only Linux test.

References used for the implemented DNS and admission behavior:

- [Unbound views](https://unbound.docs.nlnetlabs.nl/en/latest/topics/filtering/tags-views.html)
- [Unbound configuration](https://unbound.docs.nlnetlabs.nl/en/latest/manpages/unbound.conf.html)
- [hostapd control interface](https://android.googlesource.com/platform/external/wpa_supplicant_8/+/master/hostapd/ctrl_iface.c)
- [nftables families and interface hooks](https://wiki.nftables.org/wiki-nftables/index.php/Nftables_families)
