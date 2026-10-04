# Implementation plan: make the dashboard name work on the home network

## Reviewer summary

Make `https://jittersim.local:8080` work from the management network because
JitterSim's README and Pi guide promise that address. Install Avahi and publish
the dashboard name as an extra address record without changing the Pi's system
host name or taking over an existing Avahi configuration. Keep the direct IP
address as a recovery path. Block mDNS on the isolated `JitterSim-Test` network.

The installer treats Avahi as optional so a package or service failure does
not prevent dashboard access by IP. A NetworkManager hook refreshes the name
when the management address changes. The remaining hardware check is whether
the Vodafone router carries local name traffic between Ethernet and Wi-Fi.

## Current behavior

- The root README and Pi deployment guide direct users to
  `https://jittersim.local:8080`.
- The installer creates a certificate for `jittersim.local` and the Pi's
  current Ethernet address. It does not install a service that announces the
  name on the local network.
- The dashboard is reachable by its direct IP address in the reported setup.
- The management firewall currently blocks dashboard access from the test
  interface but allows other local traffic by default. It has no explicit mDNS
  rules.

## Decisions

1. Install `avahi-daemon` and `avahi-utils` as optional packages after applying
   the firewall update. If package installation or name publication fails,
   warn and keep the dashboard and IP fallback working. Do not hide failure of
   required packages.
2. Use Avahi's address publisher to add `jittersim.local` for the current
   management IPv4 address. Do not change the system host name or rewrite an
   existing Avahi configuration. An existing Avahi service remains in control
   of its own settings.
3. Run the publisher as a JitterSim systemd service. Use a NetworkManager
   dispatcher hook to restart it when the configured uplink goes up, goes
   down or gets a new DHCP address. Do not publish a loopback address when the
   uplink is unavailable.
4. Block UDP port 5353 traffic entering or leaving the test interface. Keep
   mDNS available on the management network and keep the existing dashboard
   rules. Do not enable an mDNS reflector between the networks.
5. If `jittersim.local` is already in use or cannot be published, report the
   failure in the service log. Keep the direct IP address available. Do not
   silently accept a different name because the dashboard certificate uses
   the documented name.
6. Preserve credentials, AP settings and dashboard data. On rollback, remove
   only the JitterSim service and NetworkManager hook. Leave Avahi installed
   because it may have been present or used before JitterSim.

## Security and scale

The test interface must not send or receive mDNS traffic. This protects against
name announcements from Avahi even when Avahi serves another local purpose.
The new service publishes only the dashboard name and the Pi's management
address. It does not publish credentials or dashboard data. One small
long-running publisher serves the device and refreshes only on network
changes, so its traffic remains bounded. No application data migration or
backfill is needed.

## Work and verification steps

1. Add the mDNS firewall rules and make the installer apply the changed rules
   on both first install and repeat update.
2. Add the optional Avahi packages, publisher service and NetworkManager hook.
   Check package failure and service failure leave IP access working.
3. Update the README and Pi guide with the local name behavior and IP fallback.
4. Check first install, repeat update and a changed `JITTERSIM_UPLINK`.
5. On the Pi, renew the uplink DHCP lease and confirm the name maps to the new
   address. Check a name conflict is reported.
6. From a Mac on Vodafone Wi-Fi, resolve the exact name and open the HTTPS
   dashboard. From `JitterSim-Test`, confirm mDNS has no response and the
   dashboard remains blocked. mDNS does not cross network boundaries by
   itself, so Vodafone behavior must be checked on the actual router.
7. Check that rollback removes only JitterSim's service and dispatcher hook
   and preserves any pre-existing Avahi configuration.

## Risk and review

Risk is medium because the change adds a network service and a new name for the
management address. No repository pull request template was found. A
deployment-aware review should check mDNS isolation, DHCP updates, name
conflicts and installer recovery. The actual Pi and router must pass the
network checks before calling the hostname fixed.

## Live acceptance repair

A bounded capture on the Pi proves that valid Mac name queries arrive through Ethernet. The old `inet jittersim_access` table still drops TCP and UDP port 5353 arriving outside the test interface or loopback. Avahi uses that port for management name queries. Restarting Avahi only sends fresh announcements, so access stops again when the cached record expires.

Remove the known obsolete `jittersim_access` tables in the existing network reset cleanup. Apply removes them in the same atomic batch that installs the current plan. Keep `jittersim_control`, the current gateway rules and every other application's tables. The old DNS service is not listening on this port. The current mDNS firewall continues blocking queries and replies on the test network.

This repairs the already authorized hostname feature during urgent acceptance. Verify real Linux cleanup for apply and repeated reset. Seed the old blocking rule, preserve a separate foreign table and verify that name queries reach a local responder after cleanup. Deploy through the existing installer so recovery of reset or applied state performs the cleanup automatically. Recheck management HTTPS and test-interface isolation on the Pi. No credential or database change is required.

The installer also runs only the obsolete-table cleanup after replacing the services. This covers older databases with no recognized saved action without resetting current queues or current policy tables. Genuine cleanup errors stop installation instead of being mistaken for missing tables.

## Obsolete-rule repair validation

304 tests passed on Mac and in a fresh Linux package installation. Real Linux packets reproduce the old management drop and prove that apply, repeated reset and installer cleanup restore reception. Current test-network mDNS input and output stay blocked. Current control rules and a separate foreign table are preserved. The check refuses every existing table that it would change or delete. Genuine cleanup failures remain errors. Internal review found no remaining production blocker. Management name queries and HTTPS must be checked again on the installed Pi.

## Resolver port after the main integration

The access controls that merged from main first ran their filtered resolver on port 5353 and closed that port outside the test interface and loopback. That brought back the repair above and also made test-client DNS time out once the management firewall dropped mDNS on the test interface. The resolver now listens on port 5335 (`DNS_PORT` in `networking/access.py`). The access table keeps only that port private. Port 5353 stays open to Avahi and the management firewall still blocks it on the test interface. Kernel tests keep a responder on port 5353 in the Pi namespace and check that name queries still reach it after the access restore and after an Apply.

## Update 2026-10-02: one neutral name on both networks

The owner decided to use one neutral `.local` name (default `netlab.local`, a
setting, never containing the product name) that the Pi announces with mDNS on
Ethernet (answering with the Ethernet address) and on the test Wi-Fi (answering
`192.168.50.1`). This replaces the decision above to block mDNS on the test
Wi-Fi and to publish `jittersim.local` with `avahi-publish-address`.

- Avahi publishes address records on every network when it is not given an
  interface, and `avahi-publish-address` cannot bind a record. The
  `jittersim-mdns` service therefore talks to Avahi over D-Bus and binds each
  record to its own interface (`jittersim.mdnsname`).
- Avahi's own publishing (host name, workstation, services) is switched off
  because it cannot be limited to one network. The original settings and the
  renamed service files are kept and `python -m jittersim.mdnsname restore`
  puts them back. `jittersim.local` and `<pi-name>.local` are announced on
  Ethernet only.
- The control firewall accepts UDP 5353 on the test interface and nothing else
  new. The installer limits Avahi before the firewall opens that port.
- A conflict is reported (never renamed) in `jittersim diagnostics` and the
  dashboard, with the command that changes the name.
- The decisions about optional Avahi, the NetworkManager hook and keeping the IP
  address as a recovery path still hold. The hook now wakes the publisher
  instead of restarting it.
