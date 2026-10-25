# Router missing-helper fixture correction

Base `6e415764`; independently reviewed and browser-tested source/test head `28c2c457`.
Only the browser fixture and its unique unit regression change. Production source
is identical to the base. All 31 original `run_width` assertion ASTs are unchanged.

The hosted job 121197317661 in run 40202792197 reported `Route is already handled`
in the missing-helper health callback after `route.fetch`, surfaced later by the
planted-secret probe. The precise initiating interleaving was not reproduced or
proven. The fixture now reads the complete real authenticated health JSON before
registering interception, changes only `helper_configured` to false, and fulfills
each intercepted request immediately without a callback network fetch. No ignored
exceptions, timeout increases, application changes or relaxed privacy checks.

Validation:
- Controlled-delay snapshot/handler regression: 1 passed. It verifies no handler
  becomes available before the snapshot completes, authenticated request details,
  unchanged other health fields and no later callback fetch. This is a fixture
  ownership regression, not a claim to reproduce the exact hosted interleaving.
- Full affected `router_copy` module: PASS, 27 seconds, exact clean `28c2c457`,
  cached Chromium and Python 3.12 via the shared wrapper. Desktop and phone router
  journeys plus visible-check cases run against synthetic local HTTP fixtures.
- Scoped gating Ruff and whitespace checks: PASS.

Local receipt: `test-results/local-ci-20261025-160209/summary.md`.
The preserved original hosted trace is
`test-results/router-route/hosted-original-failure.txt`. Checksums and source/test
identity are in `manifest.json`. The local CI summary has generic hosted-unavailable
boilerplate; the actual hosted job above did run and fail. No hosted recovery pass,
full suite, real Pi, installation or hardware validation is claimed.

The browser process terminated successfully and its shared slot was released.
This successor adds evidence only; source/tests remain exactly the tested trees.
No push or merge by this lane; the parent owns integration.
