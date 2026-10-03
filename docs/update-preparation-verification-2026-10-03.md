# Update preparation and recovery checks

Package preparation now finishes before services stop. The outage installs only
verified wheels without fetching dependencies or building source. Settings are
saved after the helper, dashboard, browser proxy and watcher have confirmed that
their processes have ended. The running test Wi-Fi remains available.

A complete recovery folder has a unique request identity and checksums. One
atomic selector publication makes it active. A failed directory sync after
replacement is treated as a possible completed publication. Recovery validates
all inputs before deleting live files and keeps its chosen copy pinned across
retries. Current signing keys, token and standalone manager survive rollback.
Certificate preservation joins this flow through its separately reviewed hooks.

The operation reserves 240 seconds for recovery within an 840-second allowance.
Commands have finite time and output limits. An unknown cancelled command keeps
the request and blocks restoration until its process group has ended. Snapshot
size, entry count, depth, cleanup and private preparation inputs have limits.

Failure-first evidence is saved under `/private/tmp/jittersim-*.xml`. It covers
failed builds and free-space refusal before stop, interrupted publication,
unknown writer state, invalid restore inputs, excess output, retained child
processes, health trickling, retention and inherited installer-lock ownership.
Existing signing, version ordering, online-job and trust recovery tests run with
real signatures and filesystem changes. Unit fixtures replace external package
and service commands only. The real SQLite test ends an owned writer process
with committed WAL then confirms that rollback restores its committed value.

A separate private Python 3.12.13 proof built seven actual wheels from this
checkout with cached inputs. It installed and checked the dependency closure and
isolated imports in a disposable environment then installed twice offline at
the same package number. Its receipt records the source hash. This proves the
package inputs work without relying on source imports or a warmed installation.

Linux command ownership and a copied standalone manager receive separate checks
before the final handoff. Final source review and the complete configured test
matrix remain required. No live Pi deployment is established by these fixtures.
