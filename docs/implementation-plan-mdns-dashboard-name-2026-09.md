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
