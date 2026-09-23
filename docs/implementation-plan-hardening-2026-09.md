# Implementation plan: safer recovery and visibility

## Reviewer summary

This work addresses several gaps found during a review of JitterSim. It will
restore the last applied network state after a Pi service restart, prevent
IPv6 from silently bypassing IPv4-only profiles while shaping is active, refresh
website and service address matches on a bounded schedule, add a downloadable
configuration backup and a limited action history, and show useful live
diagnostics in the dashboard. The dashboard will use HTTPS so its admin token
and one-time Vodafone password are not sent as plain text.

IPv6 will be blocked for clients on the test Wi-Fi while a plan is active. It
will not be shaped per device in this change because JitterSim does not yet
track stable IPv6 addresses for assigned devices. Resetting the network removes
that block. The Vodafone router controls remain specific to the TG3442DE and
are still dependent on its current local web interface. Real Pi and router
verification is required after deployment.

## Decisions and risks

- Keep device identities and existing quotas IPv4-based. Block forwarded IPv6
  from the test Wi-Fi while a plan is installed, and state this clearly in the
  dashboard and README.
- Cache DNS results for at most one minute with a strict entry limit. This
  reduces repeated lookups while allowing service address changes to appear
  without restarting JitterSim. Shared hosting and encrypted DNS remain
  limitations.
- On startup, reconcile the helper with the last recorded action: re-apply a
  previously applied plan or finish a reset. A never-used installation stays
  untouched. A prior explicit reset must not be undone on reboot.
- Export a point-in-time SQLite backup without exposing it to unauthenticated
  callers. Record action names, time and outcome only. Never record passwords,
  tokens or request bodies. Keep the history bounded.
- Serve the dashboard over HTTPS with a Pi-local certificate. Update the
  installer, service and firewall together. Document certificate trust and
  address-change behavior so a browser warning is not mistaken for proof of
  identity.
- Diagnostics are read-only and report the helper/runtime state, connected
  clients and available traffic counters. Do not claim to measure end-device
  latency or identify encrypted services.

## Work slices

1. Add bounded DNS caching and regression tests for expiry, limits and failed
   lookups.
2. Add the explicit IPv6 test-network block and tests that verify apply/reset
   behavior.
3. Reconcile previously applied/reset state on service start and cover retry
   and failure reporting.
4. Add authenticated configuration backup and bounded, secret-free action
   history with API and storage tests.
5. Add a read-only diagnostics endpoint and a dashboard panel for results.
6. Enable HTTPS in the Pi installation, update port/firewall settings and add
   tests for secure defaults and clear deployment instructions.
7. Update the README and roadmap to describe actual behavior and limitations.
   Run focused tests, the complete suite, syntax checks and deployment-file
   checks. Commit each slice separately, then push the finished commits.

## Deployment and recovery

The installer must preserve existing credentials and configuration. The new
certificate is generated only when missing. A failed helper apply follows the
existing owned-state cleanup path and records the failure without deleting
saved profiles or quotas. If the HTTPS service cannot start, the installer
must report the certificate or service error rather than silently fall back to
plain HTTP. Confirm dashboard access over HTTPS, the IPv6 policy, restart
recovery and Reset from a second client after deploying to the Pi.
