# JitterSim full verification

## Human review summary

Test the complete current feature set after the router-switch repair. The
owner requested immediate completion of the remaining tests and repairs.
Reuse the proven router-switch results and test the remaining features through
their public controls and real Linux traffic where possible.

Run the complete Python suite, both browser journeys and all clean-container
checks. Add real concurrent-client measurements for rate limits and isolation.
Then test the installed Pi with the saved Mac on its Wi-Fi. Cover normal
traffic, delay, loss, protection, access rules, DNS observations, proxy traffic,
quotas, timed behavior and recovery. Save the starting configuration privately
and arm independent restoration before changing the live network.

The main risks are temporary loss of test-device internet access and accidental
changes to saved settings. Keep the router Wi-Fi and Pi Ethernet available.
Restore configuration, applied state, Pi Wi-Fi settings, the Mac connection
and VPN. Keep credentials, response bodies and private backup contents out of
the test report. Do not change household devices or their router settings.

## Work

1. Inventory documented features and their existing tests. Record the current
   source revision and installed revision. Preserve unrelated local files.
2. Run the complete Python suite with an exact test count and result file.
   Run focused lint and both served browser journeys.
3. Build the current checkout in a clean Linux container. Run its complete
   suite plus installer, peer lookup, real network, packet protection,
   dashboard isolation and management-name checks.
4. Exercise concurrent client traffic in disposable network namespaces. Check
   independent rate limits, normal traffic, device protection and bounded
   recovery. Record measured values rather than inferred throughput.
5. Inspect and privately snapshot the Pi's saved and applied settings. Install
   a bounded Pi-local test worker that restores these settings independently
   of the Mac connection. Use the existing saved dashboard access.
6. Run live Mac traffic through the Pi. Test a representative path for each
   feature family. Correlate measured traffic, counters, diagnostics and API
   outcomes. Verify configuration controls without retaining private bodies.
7. Reproduce any defect before a focused repair. Record the design and review
   its failure behavior before production edits. Add useful regression
   coverage then rerun affected checks and release checks.
8. Verify the restored Pi services, configuration, saved access, Mac Wi-Fi
   and VPN. Save a concise feature-by-feature report with measured results
   and any concrete hardware limit.

## Verification and limits

Keep each test bounded by a deadline and data limit. Use isolated container
networking for artificial client traffic. Keep live traffic small enough to
avoid disrupting the household uplink. Router off/on already passed on the
same installed revision and does not need another password entry.

The available Mac provides a real Wi-Fi client. Separate Linux clients provide
concurrent traffic and separate source addresses for isolation checks. Report
which path each result used. A single location cannot establish Wi-Fi coverage
throughout the house. Do not invent a coverage or client-capacity result.

No migration, new stored secret or access expansion is planned. Existing
private deployment backups and release installation support code rollback.
Preserving the original settings and applied state is part of acceptance.

## Packet duplication repair

The real concurrent traffic check found a failed transition on Linux
6.8.0-117. Switching from two rate limits to one duplication profile left the
second old packet queue attached while the first new queue was installed.
The kernel rejected that temporary combination and the existing recovery
removed the project rules.

An isolated kernel check reproduced the rejection. Clearing both old queues
first allowed the same final duplication profile and ordinary traffic queue.
Clear every old device queue on one interface before adding the new queues.
Keep the existing root ownership check and bounded failure recovery. This
adds no commands or stored data and keeps the existing deployment format.

Add a command-order regression test plus a real Linux transition check in
the existing release smoke test. Rerun concurrent traffic, the full suite and
the clean Linux release checks before installing the repair on the Pi.
Review the change for stale queues, removal of unrelated rules and recovery.

The tested Linux 6.8 kernel also rejects a final combination of duplication
and a second impaired queue. Check that combination on the installed Pi and
record the actual platform result. Do not hide a kernel rejection or claim
support from command generation alone.

## Saved dashboard access repair

The live Mac completed seventeen cases before a dashboard request returned
403 during byte progression. The saved access file survived restoration.
The current identity check discards a device address unless the kernel calls
it REACHABLE. Linux also keeps valid address mappings in STALE, DELAY and
PROBE while checking reachability again.

Keep fresh REACHABLE evidence mandatory when adding access. For an existing
permission check accept those three additional address states only with one
matching direct neighbor and a current matching Wi-Fi station. Continue to
reject missing, failed, incomplete, duplicate and routed test identities.
Always reread the saved permission file so removal still takes effect.
Do not add a permission or extend the one-second identity cache.

Add focused tests that first reproduce the saved-access failure. Cover all
accepted refresh states, rejected states, enrollment, station loss, address
reuse and permission removal. Extend the real Linux address check and repeat
the full Mac test after deployment. Keep the current authenticated dashboard
and SSH isolation tests as release requirements.

## Dashboard connection repair

The installed repeat twice timed out during a new TLS handshake. The safe
connection snapshot showed unfinished connections from both the old home
address and the new test address. The server currently waits for each TLS
handshake and request in sequence. Several interrupted or unused connections
can consume the new client's whole connection deadline.

Advance unfinished handshakes and the first request byte without blocking.
Keep at most sixteen unfinished connections with the existing five-second
handshake limit and ten-second input limit. Preserve one input deadline across
waiting for the first byte, headers and body. Dispatch active requests in
the same server thread so SQLite ownership and configuration changes stay
sequential. Close excess connections and release all waiting sockets on stop.

Add real HTTPS checks for several interrupted handshakes, unused encrypted
connections, capacity and recovery. Retain partial-header, partial-body,
slow-reader, authenticated-action and access-isolation checks. First reproduce
the delay with the existing server. Then review the connection transitions,
absolute deadlines, socket cleanup and shutdown behavior before installation.
Rerun release checks and the full physical Wi-Fi test after installation.

## Completion

All three repairs are installed as `959c2bc`. The 503-test suite, both browser
journeys, eight clean Linux checks, concurrent traffic checks and thirty live
Wi-Fi stages passed. The live run used WPA2. A physical WPA3 connection check
needs the owner to unlock the Mac. Saved settings and access were restored.
The saved configuration is applied and the original VPN state is restored.

During setup the association wait may retry only an explicit helper-busy
response within its existing deadline. The physical rate check verifies an
upper cap rather than requiring a minimum speed. These changes do not relax
traffic-case failures or dashboard permission checks.
