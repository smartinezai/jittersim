# Release 1.1 recovery composition

Base: 6e4157649ba30d9ad3203a13e32426e9ddaa6b6f. Composed reviewed inputs: 00628c30 (updater process disappearance), 28c2c457 + 0ba7ae68 (router fixture and evidence), f9fd466d (owner-approved Linux job budget).

The updater's only production change treats an individual vanished procfs entry as absent and continues checking other group members. Unknown errors remain fail-closed. The router override obtains the complete authenticated synthetic health response before registering an immediate fulfilment handler. Original browser assertions remain unchanged. Linux job budget rises from ten to twenty-five minutes; the same job has now completed successfully on f9fd466d. No test selection or assertion is relaxed.

Independent actual-diff reviews were CLEAR. Worker receipts retain 56 passed/1 Mac-specific skip for affected updater coverage, final 7 deterministic regression passes, narrow Python 3.9 standard-library probe 4 passes, router controlled-delay unit 1 pass and full router_copy browser module PASS in 27 seconds. These are scoped results, not an all-suite pass on this composition.

Composition verification: 8 new regression cases passed, Ruff E9/F63/F7/F82/F811 passed across src/tests, git diff --check passed. Version, reservation registry and all three pinned files are byte-identical to base. Raw baseline failure-log trailing whitespace was normalised in the committed presentation only; the original remains in the worker receipt and the committed manifest was updated.

The earlier main hosted run had seven successful jobs, two real failures and one Linux deadline cancellation. This patch addresses those two failures and that budget. A separate connection-check initialisation race found on PR259 remains owned by the testing coordinator. The separate Pi browser budget proposal still requires owner feedback and is excluded. No new full-suite or hosted terminal claim is made here.

No release state, signed record, package, tag, owner key or hardware was changed. The old signed local evidence cannot cover this changed source tree. The ongoing 1.1.0 reservation is preserved; a corrected accepted ref must pass the existing release gates before any owner prepare operation. The existing release.sh merged marker still points to old 6e415764, so blindly resuming it does not select corrected main.
