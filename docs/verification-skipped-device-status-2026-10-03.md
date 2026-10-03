# Skipped saved-device status verification

Source 6435c3d73e4faf254744e8837181d9cc288fabaa corrects the Start message
when saved device profiles are excluded because identity cannot be checked.
It retains actual applied counts and distinguishes pending saved identity
from the profiles skipped by a completed Start. General-profile precedence,
protected devices, service lists and existing warning details remain intact.

The actual served consumer reproduced the old misleading claim that no
profiles were saved with three unverified saved IDs. Six of ten new regression
cases failed before repair. After repair all 195 new and existing status and
active-profile checks passed with no skips. The tests exercise actual served
appliedMessage and renderApplySummary, including mixed counts, general mode,
protected clients, absent fields and a genuinely empty configuration.

Configured lint selection passed with caching disabled and whitespace checks
passed. Independent complete-patch review approved the repair before commit.
The change adds no network read, endpoint, storage or credential behavior.
Device-list work remains linear. Rollback is an ordinary code revert.

Original failures, JUnit results, reproduction and exact source hashes are
retained through 10 October in the primary checkout under
test-results/remaining-release-repairs-2026-10-03/skipped-status/.
These are targeted Mac source checks. The final combined installed Linux
workflows remain required. No remote publication or live Pi change occurred.
