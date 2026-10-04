# Final local release verification

Accepted main `5e0fed2ccb2414373643f20db1df59aa2de25306` has the same complete source tree as tested candidate
`6fb864f0874c7152048297d4ff87f7051001d21a`. The application tree is
`ee20676f7e06e176a94abe6473153427cc40cb98`. The built wheel SHA256 is
`41739cd18896f308b312675ae225a17fddafe5ebf525d009c6cbd8550ca78ecb`.

Each full Git-backed Python 3.9, 3.11 and 3.13 suite passed 5,461 cases with 40
kernel-only skips. The native no-Git full suite passed 5,391 cases with 110 audited
skips: 40 kernel, 57 deployment-checkout and 13 signing-checkout cases. Those
checkout cases passed in the full Git-backed suites. No unexpected skips.

All 31 browser modules, both Node journeys, 40 interface capture states and 40
observational accessibility states completed. All 40 configured first-stage
commands, artifact return and cleanup passed. Both clean no-cache builds,
all 13 native commands, all 40 enabled kernel cases, network smoke, CA permissions
and all three locked-package build and installation checks passed.

The first Python 3.9 run hit a partially written test fixture marker. Its failed
XML and stopped follower remain retained. The unchanged full retry passed.
Earlier failed kernel contract and browser assertion runs also remain failed.
Only their independently reviewed test corrections are in the accepted source.

The accessibility scan recorded 20 moderate and two minor rule instances across
18 states. Twelve concern skip links, eight repeated section labels and two
empty table headings. No serious or critical violations were recorded. The
scan is observation-only and includes checks requiring manual review.

Hosted jobs failed before executing steps because of GitHub billing limits.
The complete local counterparts satisfy the current release authority. Hosted
CI is not green. Stable publication keeps its separate hosted and physical
gates. Pi trust, sudo authentication, safe recovery and physical phone/client
acceptance remain outstanding. Persistent outage logging is outside this batch.

The machine-readable [actual local result summary](verification-final-local-release-2026-10-04.json)
records source identities, command receipts, XML hashes and driver manifest.
Earlier [candidate verification notes](verification-coordinated-candidate-2026-10-04.md)
preserve the repair history. Durable logs and all failed runs are retained
through 10 October under `test-results/remaining-release-repairs-2026-10-03/`.

The read-only management check at 06:27 UTC found the Mac using the Pi test
Wi-Fi gateway `192.168.50.1`. SSH to the management address timed out.
Connect through Ethernet or the router network and confirm recovery access
before staging or installing.
