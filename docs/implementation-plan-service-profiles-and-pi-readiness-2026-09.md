# Implementation plan: service profiles, Pi readiness and remote control

## Reviewer summary

Make JitterSim safe to install, operate and reset on a dedicated Raspberry Pi.
Fix the dashboard script, protect private reads, make network changes and reset
reliable and verify the test Wi-Fi setup. Add a way to apply one profile to a
chosen list of websites or services on managed test clients while other
traffic keeps its normal profile.

Add optional remote dashboard access so the owner can monitor the Pi and
apply or reset profiles from a laptop away from home. Use a private connection
with a valid HTTPS address. Keep the admin token required for every private
read and action. Remote management does not route the laptop's browsing through
the Pi or put it on the test Wi-Fi.

This is high-risk work because it changes access to private data, privileged
network rules, the Pi's Wi-Fi setup and how client traffic is classified. The
first work should repair the known failures and prove a safe reset. The service
feature must then identify a destination without relying only on its IP
address. The owner chose strict matching for managed Mac browsers first. An
HTTPS CONNECT proxy on the Pi uses requested hostnames. Other proxied sites
keep the device profile. Apps or browser paths that bypass the proxy remain
outside the browser service feature. The owner chose private Tailscale Serve
for remote management and authorized implementation, local testing, focused
commits and one final branch push. Real Pi behavior still needs a separate
hardware check.

## Current behavior and gaps

- The served dashboard contains a JavaScript syntax error caused by a newline
  escape in its Python string. This stops the page controls from running.
- The full SQLite backup and several private read routes answer requests
  without the admin token. The live traffic route has a token check.
- The installer creates an access-point password but does not create the
  `jittersim-ap` connection, client address service or internet sharing.
- Reset can overlap a scheduler apply. Network removal commands can fail while
  reset still reports success.
- The compiler accepts plans that ask Linux for an unsupported number of
  priority bands. Applying a plan can also replace an existing root queue on
  either configured interface.
- Some rules and the management firewall use `eth0` and `wlan0` even though the
  helper accepts other interface names. Health reports `ok` from configuration
  alone rather than checking the helper.
- The installer makes the installed root helper code writable by the
  `jittersim` account.
- The dashboard has a local certificate for `jittersim.local` and its current
  Ethernet address. There is no private way to reach it from outside the home
  network. The management firewall only blocks access from the test Wi-Fi.
- A device profile affects all its traffic. A profile website list blocks
  destinations outside the list. Service limits apply a fallback profile only
  after a byte or time limit. Those limits match resolved IPv4 addresses, so
  they cannot promise isolation when sites share an address.

## Approved decisions

- Keep one ordinary profile for each device. Add a separate service assignment
  that names a device, a list of destinations and the profile for those
  destinations. A service assignment should take effect immediately and should
  not depend on a quota. Existing quotas remain separate behavior.
- Support Mac browsers configured to send HTTPS CONNECT tunnels through the
  Pi proxy. Match named hosts and their subdomains. Unknown hosts keep the
  ordinary device queue. Do not claim coverage for native apps, VPNs, plain
  HTTP, QUIC or a browser that bypasses the proxy.
- Remove the current network-wide IPv6 forwarding block. The provisioned test
  AP provides IPv4 sharing only. The proxy can reach public IPv6 hosts, but
  ordinary test-client IPv6 is outside this first release.
- Let users name and edit services directly. DNS observations can help suggest
  names but should not be the only way to enter a service. Define how service
  names with several hostnames are kept current. Reject conflicting assignments
  until priority has an explicit, tested meaning.
- Protect every private read, including backups, devices, observations, plans,
  diagnostics and action history. Leave only the page and a minimal health
  response public. Check that the dashboard loads private data only after a
  successful token check.
- Serialize all apply and reset operations with the scheduler. A successful
  reset must mean that project-owned rules are absent and no older apply can
  follow it. Treat an absent rule as safe and a failed removal as an error.
- Validate plans against the Linux queue limit before sending commands. Check
  existing root queues before changing them so reset does not remove another
  network tool's configuration. Preserve a working management path.
- Provision a named test Wi-Fi connection through NetworkManager with IPv4
  sharing and a DHCP pool separate from manually addressed managed clients.
  Require NetworkManager 1.50 or newer for that pool. Keep AP credentials
  outside the repository. Tie interface settings and firewall rules to one
  checked topology or reject unsupported interface names at installation.
- Keep installed code root-owned and give the dashboard write access only to
  its data directory. Health should distinguish a live helper from a configured
  socket path.
- Offer remote management as a separate, opt-in setup. Use Tailscale on the Pi
  and the owner's laptop with Tailscale Serve forwarding
  the Pi's private HTTPS dashboard from loopback. Serve supplies a certificate
  for the Pi's private tailnet name. Require explicit tailnet access rules for
  the owner's laptop and keep the JitterSim admin token in place. Do not open
  router ports or enable public sharing. Preserve local management access and
  an SSH recovery path if the private connection fails. The owner chose this
  path. Tailscale's Personal plan is free for non-commercial home use with a
  limit of six users. Confirm that this project qualifies during setup. The
  setup should grant dashboard access to the owner's laptop only and should
  not route the laptop's general internet traffic through the Pi.
- Make remote setup repeatable and reversible. It should check that the Pi is
  enrolled in the expected private network and report the private dashboard
  address. Removing remote access must leave the local dashboard working.

## Follow-up questions after Pi testing

1. Do non-browser apps need strict hostname selection in a later release?
2. Which additional hostnames do the chosen services use during the actual
   demonstration on the Mac?
3. Does the Pi's current OS meet the NetworkManager and socket-marking
   requirements? Hardware validation is still pending.

The working tree had three pre-existing edits to helper logging and its
guide. Keep those edits separate from this feature branch.
## Work slices

1. Record a clean baseline, create a work branch and preserve the existing
   working-tree edits. Install test dependencies in an isolated environment.
   Add a short local setup step to the README.
2. Repair the served dashboard script. Add a check that parses the JavaScript
   actually sent to the browser so Python string escaping cannot hide errors.
3. Close private read routes and add request-level checks for missing, wrong
   and valid tokens. Check that no backup or observed domain data is exposed.
4. Make apply and reset mutually exclusive, verify removal results and add a
   recovery command usable over SSH. Test overlapping operations, a failed
   apply, a failed reset and a service restart.
5. Enforce Linux queue limits and inspect existing root queues before apply.
   Test empty plans, the maximum supported size, excess assignments and
   repeat apply. Keep changes limited to project-owned state.
6. Make the Pi installation create or update the test AP safely. Check the
   interface names, client address range, forwarding, firewall, ownership and
   helper readiness. Preserve existing credentials and support repeat install.
7. Design and build service assignments for the approved managed clients.
   Keep ordinary traffic on its base profile. Cover hostname changes,
   overlapping services, unsupported client paths and both directions.
8. Add opt-in private remote access after private reads and reset are fixed.
   Keep the local HTTPS address, add a valid private HTTPS address, restrict
   remote clients and provide setup, status and removal steps. Test access
   from a separate network, token checks, loss of the private connection and
   local recovery.
9. Update the README and roadmap. Run focused tests, the full suite, lint,
   served JavaScript syntax checks and deployment-file checks. Review the full
   diff, create focused local commits and push the reviewed branch once.

## Security, scale and recovery

- Store service definitions and the minimum client state needed to route
  traffic. Keep passwords, tokens, page contents and full browsing history out
  of logs and backups. Bound retained DNS observations and action history.
- Accept browser CONNECT requests only from the test network, match the
  client's assigned address and never accept a client-supplied queue mark.
  Reject private destinations. Keep the dashboard admin token separate from
  the test Wi-Fi password. Check that a test client cannot reach management.
- Limit service names, active clients, rules, traffic samples, retries and
  helper request time. Reject oversized plans before changing the network.
- Use forward, repeatable SQLite migrations for new stored assignments. Make
  installer changes safe to rerun. A rollback must include an SSH reset and a
  path to restore the prior package without losing saved profiles.
- Treat private-network membership as another access gate. Limit which
  enrolled devices can reach the Pi. Never use public dashboard sharing, keep
  VPN credentials out of the repository and document how to revoke a lost
  laptop. If home internet or the private connection is down, remote control
  will be unavailable and local management must still work.
- The repository has no pull request template. Mark the eventual change as
  high risk in its review text and request review from someone familiar with
  Linux networking and local service security before merging.

## Verification and release boundary

Use a disposable Linux environment to run real `nft` and `tc` commands on test
interfaces. On the Pi, verify the admin connection from the management network
and public internet access from two test clients. Confirm that a selected
service receives its profile, an unrelated site remains normal, shared-host
cases do not silently change unrelated traffic and reset works after failure.
Measure delay, loss and both bandwidth directions with a repeatable script.
Record the Pi model, OS, interface names, package version and test results.
From a separate internet connection, open the private HTTPS dashboard, read
live activity, apply and reset a profile. Confirm a non-enrolled device and a
test Wi-Fi client cannot reach it, requests without an admin token cannot read
private data and losing the private connection does not break local control.
Keep the remote dashboard connected during an active impairment to confirm
that test traffic rules do not affect the management connection.

Local tests cannot prove Wi-Fi, router, kernel queue or managed-client behavior
on the Pi. Keep hardware results marked unverified until they are measured.

## Remote access references

- [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)
  describes private HTTPS sharing and the self-signed local origin option.
- [Tailscale pricing](https://tailscale.com/pricing) lists the current Personal
  plan terms. Recheck them before enrolling the Pi.
