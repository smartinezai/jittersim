# Publish the reviewed homepage reference and DNS acceptance

Carry the completed reference and DNS tests onto current integration and run
them in CI. The reference remains a local fixture with simulated identity.
Publishing it does not enable a live Pi policy feed or change normal browsing.
The main risk is claiming production evidence from the simulation or failing
to load the actual extension in the CI browser. Live identity and HTTPS trust
remain separate release requirements.

## Scope

Use a separate branch from integration `a1da456`. Preserve the reviewed
commits `0541cc2`, `8aa7f80`, `eeeb3e4` and `985f2ea`. Leave existing owner
worktrees untouched. The completed work and its original plans are already
reviewed. Publication, fixes, testing and merging are authorized.

## Steps

1. Carry the four completed commits onto current integration.
2. Register the DNS dashboard journey in the existing browser workflow.
3. Add a bounded homepage-reference job with matching pinned Python and Node
   Playwright versions. Use the full Chromium channel so the actual extension
   loads in headless CI. Run the eight HTTP checks and all 32 browser checks.
   Follow [the extension guide](https://playwright.dev/docs/chrome-extensions).
4. Include the reference fixture in the Pi test image because its HTTP tests
   load that file. Copy the fixture only rather than local Node dependencies.
   Keep the existing collected kernel test in the clean Linux job. Confirm the
   extended DNS packet case runs there even while local Docker is occupied.
5. Review the complete diff. Run the focused browser and Python checks locally
   then the full Python suite and lint before one final push.
6. Open a focused PR. Merge after its published clean-build, browser and Linux
   checks pass. Record exact source and keep physical checks outstanding.

## Security, limits and recovery

Risk is medium for delivering tests and the isolated experiment. The future
live policy endpoint remains high risk and is not added by this delivery PR.
The fixture listens only on loopback. It uses disposable credentials and a
separate disposable browser profile. No live administrator credential, Pi
setting, user browser profile or OS permission is used or changed.

No production schema or installer changes are needed. CI has finite job and
journey deadlines. The existing reference caps payloads, pending requests and
fixture handlers. Rollback removes the experiment and CI calls without a data
conversion. The user can disable the isolated extension at any time.

Use isolated GitHub runners for clean builds and packet checks. Do not stop
the fourteen unrelated containers on the local Docker VM. Normal HTTPS trust,
actual peer identity and physical phones remain unverified until exercised
against the final installed live implementation.
