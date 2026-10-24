# Source-equivalent post-release successor

Create a separate successor to frozen `4c843cac` after all its runtime gates have finished. Keep the old candidate and every original receipt immutable. The successor contains reviewed address-journey fixture repair `e2e2e650`, separately approved latest-main `79ef2bac` ancestry and the exact approved static metric records. It changes no production source, authentication behaviour or network rules.

## Exact changes

- Start from reviewed `e2e2e650`, whose parent is frozen `4c843cac`. Retain all prior queue inputs through ancestry.
- Include main `79ef2bac`. Its delta from `aa561623` is exactly four critical expiry-fixture and test-plan paths and its tree equals reviewed `c52f194d`. This reuses the completed critical repair rather than repeating it.
- In `test_api.py::ControlServerTests::test_dashboard_contains_configuration_and_network_controls` change only the expected manual Check token adapter from `await checkAdminToken()` to `await checkAdminToken(true)`. All other assertions stay unchanged. The intended manual intent is already tested in the phone handoff suite.
- Change only four non-strict recorded leaves in `tests/browser/baselines/gui-stats.json` to measured final source values: `page_bytes: 878431`, `page_lines: 17359`, `markup_bytes: 114163` and `js_lines: 14210`. Preserve all seven strict counters, the strict list, schema, definitions, every other recorded value and 2% tolerance. These precise record changes have explicit owner approval.

## Proof, review and checks

Record exact input ancestry and changed blobs. Prove the whole `src` tree and assembled dashboard bytes equal frozen `4c843cac`. Keep original full-native results of 8,984 passed, 118 skipped and three failed, browser results of 34 out of 36 modules passed and GUI receipts with their original provenance. The two old browser failures pass in the separate repair. The old two budget failures and one exact API expectation failure are the only intended corrections here.

Send the complete final metadata and test delta plus the equivalence proof for independent coordinator and metadata review before the final local checkpoint. After review commit and freeze. The coordinator then assigns only failed budget and API checks, affected address journeys, critical fixture checks and lint on the exact successor. Do not repeat the full suite for a JSON and test-only delta or claim old receipts ran on this new head.

## Boundaries and rollback

No optional source rewrite, minification, strict-counter change, baseline policy change or unrelated metadata. No main, running release, reserved-state, publication, signing, hardware or remote action. The separate branch can be discarded without modifying frozen `4c843cac` or originals. No open implementation question remains within the exact approved scope.
