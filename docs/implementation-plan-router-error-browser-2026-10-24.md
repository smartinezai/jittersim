# Router password error browser coverage

The completed router error change has offline checks but no browser assertions for its linked summary. Extend the existing router copy journey at desktop and phone sizes. Keep all failures inside the loopback fixture. The main risk is leaving mocked routes or remembered credentials behind before the original journey continues.

## Scope

- Start from `48708fbeb61b3b5f05d9c1a2c8718188b47d1d66` in the isolated router browser worktree.
- Edit only `tests/browser/router_copy.py` plus this plan and ignored local evidence.
- Check blank input, rejected password, summary focus, link focus and the existing field description.
- Mock only the existing router check path for rejection, router connection failure and service failure. Preserve password and Remember choices and distinguish invalid credentials from unavailable services.
- Hold a permitted retry to assert old error cleanup while the real DOM shows the busy control. Complete that mock successfully and verify cleanup after acceptance.
- Check edit and Clear password cleanup. Restore an empty password and the original Remember choice before continuing the existing real fixture acceptance and throttle journey.
- Keep route removal in finally blocks and use finite Playwright waits. Make no production, fixture, shared script or external state changes.

## Review and validation

The coordinator reviews this plan before the browser test edit and reviews the complete test diff before commit. Run only wrapper static lint and the affected `router_copy` browser module with a private copied offline Python runtime and APFS-cloned browser cache. Allow only synthetic Unix and loopback sockets through execution review. Point browser downloads to the closed loopback port and stop if cached binaries are incomplete. After a passing working-tree run, commit locally and record the same module result against the committed head. No full suites or remote writes.

## Risks and rollback

Mocking router failures must not contact hardware or affect the fixture's later rate-limit checks. The summary assertions must use ordinary DOM actions and must not relax existing journey assertions. Revert this focused test commit to roll back. Combined branch validation remains the coordinator's responsibility.
