# Saved dashboard access from the Pi Wi-Fi

## Human review summary

Let the owner register the computer they are using and control JitterSim while
connected to the Pi Wi-Fi. Add an **Allow this device** button, a saved device
list and CLI commands for registration and recovery. Keep dashboard access
usable when a test disconnects or slows ordinary internet traffic.

Recommended first-time setup: all Pi Wi-Fi clients can reach a limited HTTPS
sign-in page. The existing admin token is required to register the requesting
device. Private dashboard data and controls then require the token and a saved
entry. Verify the actual requesting device on the Pi instead of accepting an
identity supplied by the browser. Its ordinary traffic still follows profiles.
Keep the existing unaffected-device list independent.

Risk is medium. More local clients can reach the dashboard server. The main
risks are granting access to the wrong device, exposing private data before
registration and losing control during a test. A device address is never a
password. Anyone with the admin token can register themselves, so the saved
list does not provide a second independent authentication credential.

**Approved decisions:**

- Use the recommended token-protected registration page.
- Keep the exception limited to dashboard traffic. Keep SSH restricted and
  continue testing the device's ordinary internet traffic.

The owner has no Ethernet cable for the Mac. Do not require one. Installation
on the current release still needs a working administration path once. Router
Wi-Fi can provide that path. The new feature will support future registration
while already connected to Pi Wi-Fi.

## Learning objective and current evidence

The central reasoning is how to identify the device making a request and keep
administration reachable during deliberate network failures. CLI parsing and
button layout are routine work around that decision.

The checked release branch is `feat/service-profiles-and-pi-hardening` at
`fb5b627f9d34d18e2afc5aac99de90282a5f1027`. Confirm this and the installed
release again before implementation or deployment. Preserve unrelated edits.

- The Mac has address `192.168.50.184` and router `192.168.50.1` while showing
  the name `Vodafone-C84D`. The copied name does not identify the access point.
- The control firewall drops dashboard port 8080 and SSH from the test
  interface. The dashboard already requires HTTPS beyond loopback and an
  admin token for private reads and changes.
- The request handler knows the network peer. Connected-device inspection
  joins active Wi-Fi stations with local address information.
- Active plans separately block clients and mark outgoing traffic. Opening
  only the control firewall does not protect replies from a full-loss profile.
- The privileged helper runs as root behind a protected local socket. The
  dashboard runs as a restricted user. The installer replaces the generated
  control firewall while preserving existing credentials and saved settings.

## Proposed behavior

### Device identity and registration

Show **Allow this device** after checking the admin token. Derive the peer
address from the connection. Never trust browser identity fields or forwarded
address headers. For Pi Wi-Fi require one current associated station whose
verified address matches the peer. Reject missing, stale or conflicting data.
Do not mistake a router or relay for the requesting computer.

For direct router Wi-Fi registration verify that the peer is on the Pi's local
router network before resolving its device address. If identity cannot be
verified, explain how to register from Pi Wi-Fi or use the Pi CLI. Preserve
existing token-protected administration through the router network, loopback
and any existing remote administration route. A relay cannot register itself
as the device behind it.

Save the verified Wi-Fi hardware address with an optional name and creation
time. Limit the list to 64 entries. Adding an existing entry is safe to repeat.
Do not keep the DHCP address as the durable identity. Use existing name
validation and reject invalid device addresses.

Show saved devices and support removal after authentication. Confirm removal
of the current device and return it to the limited sign-in view immediately.
A changed private Wi-Fi address needs registration again. Explain this beside
the list. Keep the admin token required after registration.

### First-time access and network rules

Allow local HTTPS dashboard connections from Pi Wi-Fi. A sign-in page must be
reachable before its registration button can work. The firewall cannot tell
a sign-in request from a private request inside HTTPS.

Give unregistered Pi clients only the static sign-in page, token checking and
their own token-protected registration action. Keep profiles, client lists,
history, configuration downloads and network controls unavailable until
registration. Apply the checks to every private read and write. Unknown Wi-Fi
identity must never be treated as a trusted management caller. Keep the
dashboard off the public internet.

Keep only traffic to and from the Pi's HTTPS dashboard outside simulated loss,
delay, quotas and incoming device blocks. This also keeps first-time sign-in
reachable. Do not exempt ordinary traffic or forward requests to other private
devices. Keep SSH, private upstream access and local name discovery restricted.

Cover the control firewall, incoming client rules and outgoing classification.
Keep access through apply, reset, failure cleanup, restarts, upgrades and
reboot. Use the configured interfaces rather than assuming their names. Use
an address covered by the Pi certificate. Certificate verification stays on.

### Saved state and CLI

Use one authoritative access file under `/etc/jittersim` owned by root and
readable by the dashboard's service group. The helper owns changes. Serialize
updates and replace the complete validated file atomically. Do not create a
second independent copy in SQLite. A missing file means an empty list. Invalid
saved data must not grant access.

Preserve the file during installation and include it in private configuration
backups. Keep existing credentials and device settings. No password, token or
traffic history belongs in this file. Verify ownership and permissions.

The CLI has two uses:

- From the Mac, register the requesting device through the Pi's HTTPS action.
  Accept the Pi URL and a trusted certificate file. Read the admin token through
  a masked prompt. Do not put it in command arguments or logs.
- From a Pi terminal, provide root-only add, list, remove and status commands
  through the helper. Allow an explicit hardware address for recovery without
  depending on a working dashboard server.

Report whether the change was saved and why a failure occurred. Do not suggest
Linux firewall commands for the Mac.

## Implementation and verification

1. Review this plan and resolve the two questions in the summary before
   changing production code.
2. Add bounded storage and helper operations. Cover duplicate adds, removal,
   malformed files, failed writes and simultaneous changes. Preserve the prior
   complete list on failure.
3. Add verified caller identification and registration. Cover DHCP changes,
   reused IPs, private-address rotation, multiple addresses, unavailable
   observations and relayed requests. Revalidate identity before a permission
   change. Reuse only a bounded short-lived observation for ordinary checks.
4. Add the limited page and access checks. Exercise actual authenticated
   requests from registered and unregistered clients. Check every private
   read, download and write plus forged browser identities.
5. Add the CLI and dashboard controls. Exercise the served button and real
   request path. Verify first-time, registered, removed and failed states on
   desktop and phone layouts. Skip wording-only tests.
6. Repair packet handling and installer persistence. Use real Linux packets
   to prove dashboard access survives client blocks and full loss while
   ordinary traffic follows its profile. Confirm SSH and private upstream
   access stay blocked. Check apply, repeated reset, failure cleanup and
   repeated installation.
7. Run current Mac tests, a clean Linux build, packet checks, browser checks
   and repository lint checks. Review the complete change for wrong-device
   access, missed routes, bounded work and recovery behavior.
8. Once publication and deployment are authorized, install the exact reviewed
   release through verified router Wi-Fi or an available Pi terminal.
   Register the Mac from Pi Wi-Fi and test real dashboard use. Check reboot
   persistence and restore the original experiment state after acceptance.

## Limits, recovery and task boundaries

Bound saved entries, observations, file sizes, requests and helper work. Keep
connection deadlines. Avoid repeated inspection commands for each background
read, unbounded queues and indefinite retries. Review the existing single
server worker when testing newly reachable clients.

Keep router-network and root recovery available. A software rollback restores
the previous firewall and access behavior while preserving the access file
for a later reinstall. Confirm the recovery connection before reverting.

The router Wi-Fi live toggle remains a separate requested improvement. Keep
real router radio changes separate from access acceptance because they can
disconnect the computer performing the check.

The owner approved the recommended design and authorized implementation,
testing, one final push and deployment on 30 September 2026. The complete
access feature is the authorized takeover scope.

## Implementation review and checks on 1 October 2026

The helper verifies the socket peer using its return route, one reachable
address entry and one current Wi-Fi station. Ordinary checks reuse identity
for at most one second. Registration and removal inspect it again. Every
request rereads saved permission so removal takes effect immediately. Routed
management callers retain token-protected controls but cannot enroll a relay.
IPv4 is supported for registration. Unverified peers fail closed.

The root helper serializes changes to one file. The file is limited to 32 KiB
and 64 entries with names of at most 64 characters. Reads reject unsafe owners,
groups, permissions, links, special files and malformed contents. Writes use
a complete temporary file followed by atomic replacement. No access state is
copied into SQLite. The installer creates the file once and preserves it on
later runs. Configuration backups must include it separately from SQLite.

The static page shows sign-in and enrollment before private panels are
available. All protected HTTP reads and writes check the token and the peer.
Registration accepts only a name from the browser. Cross-site permission
changes are rejected. The Mac CLI uses certificate-verified HTTPS with a hidden
token prompt and refuses redirects. Pi recovery commands use the helper.

Only local dashboard replies receive a reserved packet mark. The download
rules clear that mark before profile queues. Forwarded traffic from another
server on port 8080 still follows the profile. Dashboard input precedes client
blocks. SSH, private upstream access and name discovery retain their rules.

Checks completed:

- 462 tests pass on Mac and in a clean Linux image.
- Repository lint checks and shell syntax checks pass.
- Browser checks pass for first enrollment, failed enrollment, saved sign-in
  after reload and removal on desktop and phone layouts.
- Existing router copy and Wi-Fi name recovery browser checks pass.
- Real Linux packets keep dashboard connections usable through full loss,
  denylist and allowlist blocks, repeated apply and repeated reset.
- Real packets confirm ordinary traffic and forwarded port 8080 remain
  affected while SSH and private upstream access stay blocked.
- Existing network ownership, unaffected-device, quota and name discovery
  packet checks pass.
- Actual installer initialization run twice in a disposable Linux container
  preserves the saved entry with root ownership and mode 0640.

The dashboard keeps its existing single request worker with input, handshake
and response deadlines. A hostile local client can still compete for that
worker. This feature does not promise protection against local flooding.

Hardware installation and reboot acceptance remain pending. The Mac still
shows `192.168.50.184` with gateway `192.168.50.1`. Its route to the Pi management
address currently uses VPN interface `utun4` and SSH times out. The first
upgrade needs a verified wireless router connection or a local Pi terminal.
After installation, verify the actual Mac route and use an address covered by
the preserved certificate before claiming dashboard acceptance.

## Installation repair after the first Pi attempt

The Mac reached the router network after the Pi was temporarily powered off.
SSH now reaches the Pi at `192.168.0.17`. The first update was automatically
reverted because the dashboard and browser proxy could not import their
installed modules. The deployment wrapper's private-file settings reached
the installer and made new package folders unreadable by the service user.
The prior release and its services are restored.

Set ordinary program permissions before the installer creates its environment
and installs the package. Keep explicit private permissions on credentials,
the access list and recovery backups. Verify a fresh package under the actual
service user's permissions with private-file settings inherited from its
caller. Add that check to Linux CI. Then push the repair, build the exact
release and repeat deployment with the existing private recovery backup.
Check package imports as the service user before starting services and keep
automatic rollback if any live check fails.

The installed release passed authenticated hardware checks after the restart
check was changed to wait for readiness. Actual Mac enrollment then found
that an interface-filtered Linux neighbor query omits the interface name.
Query the exact peer without an interface filter so Linux includes the name.
Select the verified interface from the bounded result before returning it.
Verify this against an actual Linux neighbor entry in CI and on the Pi before
repeating the Mac enrollment check.
