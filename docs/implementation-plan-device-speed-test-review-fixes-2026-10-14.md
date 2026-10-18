# Device speed-test review fixes: plan

## Summary for human review

This follow-up fixes four remaining findings in the device speed-test review. It will base the speed-cap conclusion on service rules in the scheduler's applied plan, record the profile that actually controls the shared “Same as everyone” queue and keep the browser request open long enough for the bounded helper retries. It will also give an accurate message when a run is saved after the profile changes or the Pi reports a measurement fault.

The change is limited to the existing speed-test API and dashboard. It adds no database column, helper action, network route or device-side work. If the scheduler cannot provide an applied plan, the result will record that the service-rule state is unknown and withhold the cap conclusion. This adds an explicit nullable value to the existing result summary and a distinct “applied rules unavailable” verdict. Start requests will use a 75-second browser timeout. The bundled dashboard uses an eight-second helper timeout, so the two sequential reads and all retries take at most 49 seconds before route work, leaving 26 seconds for overhead.

The change is low risk for security and stored data. It touches the interpretation of measurement results and user-facing status, so focused unit and desktop/phone browser checks are required before another full review. The repository has no general PR-risk template; this plan records the risk and required checks directly.

## Scope

Address only these remaining findings recorded in `docs/review-results-device-speed-test-2026-10-10.md`:

1. Service-rule warnings are based on enabled saved settings. They can describe rules that are not in the currently applied plan, while active time or service quotas can be missed.
2. The “Same as everyone” profile snapshot uses the saved general profile, which can differ from the profile currently applied to the general queue.
3. The browser gives start requests 15 seconds, but two sequential helper reads can each take three attempts at the helper's eight-second timeout plus retry pauses.
4. The dashboard says “Speed test saved” for results whose status is `changed` or `fault`, even though their measurements may be incomplete.

Keep the four owner decisions in the original plan out of scope. Do not add Phase 2, new providers, new retention rules or Wi-Fi station counters.

## Proposed behavior

### Service-rule warning

- Read the scheduler's existing applied plan snapshot without rebuilding or applying a plan. Treat it as confirmed only when the applied plan is present and the source map contains the expected queue state. A source entry by itself is not proof that the rules are applied. A known `none` source can be confirmed for a queue with no active profile. For a shared “Same as everyone” result, also require the general queue to exist in the applied plan.
- Base the warning on service rules present in that snapshot and relevant to the tested device or its shared queue. Do not infer active rules from saved configuration alone.
- Treat a service-list entry in the applied plan as relevant only after the existing protected-device check confirms the tested device is unprotected. Match service assignments and byte or time quota rules to the tested device ID. Only treat quota rules marked active in the plan as able to divert traffic. Ignore rules for other devices.
- Capture the conclusion with the run at start so result creation adds no helper call, database scan or network work.
- If the scheduler cannot confirm an applied plan and the expected queue state for a measured source, store `service_rules: null`, withhold any otherwise cap-judged verdict using a distinct `service-rules-unknown` value and provide a clear label and note. Keep `true` for a relevant active rule and `false` for a confirmed plan with no relevant rule. A confirmed source of `none` remains `not-measured` and does not need a service-rule conclusion. No row migration is needed; older summaries without the optional key keep their current interpretation.
- Preserve the existing warning and cap-verdict behavior when the applied plan confirms that a relevant service rule can divert traffic.
- Resolve the “Same as everyone” profile through `_applied_profile` with `GENERAL_QUEUE_ID` and the saved general profile. Require both a present applied plan and a matching source-map state before recording a confirmed controlling profile. Keep the unknown-source behavior otherwise.
- Add `service-rules-unknown` to the server's `VERDICT_LABELS` so history and comparison explain the verdict. Comparison must report that the applied rules could not be confirmed. The existing CSV verdict columns should carry the new value and be covered by a focused export assertion.

### End-of-run message

- Keep “Speed test saved” for complete results and results the person deliberately finished early.
- For `changed` or `fault` results, use the recorded end reason and make clear that the result was saved but may be incomplete. Browser checks must assert both the stored status and the message.
- Keep cancellation and results that were not saved on their existing messages.
- Keep the message in the existing polite status region so screen readers announce the result once.

### Start request timeout

- Give only `POST /api/speed-tests/start` a 75-second browser timeout through the existing request-timeout mechanism. Keep the current retry count and helper timeout unchanged.
- Add a focused check that budgets both sequential helper reads: each read can take three attempts at eight seconds plus two 0.25-second retry pauses, so the current bound is 49 seconds and leaves 26 seconds for route work. Confirm the bundled dashboard launches with the eight-second helper timeout. Also verify the start request uses the longer timeout while ordinary requests keep their current limit. If the helper timeout or retry policy changes, this check must force the limits to be reviewed together before that configuration is used for start requests.
- Keep server-side helper socket timeouts and bounded retries as the source of the upper bound. Do not add background work or let a timed-out request silently start an untracked measurement.

## Verification

- Add focused unit coverage for a saved but unapplied service rule, a relevant rule in the applied plan, an unrelated device rule, active and inactive quota rules, and an unavailable applied plan.
- Cover service lists, matching and unrelated assignments, plus active and inactive byte and time quota rules. Cover an unavailable plan without converting that state into a confirmed absence of service rules. Check the new label in result history, comparison warnings and CSV output.
- Cover “Same as everyone” when the saved general profile differs from a scheduler override and when the applied source is unknown.
- Check start timeout budget and request classification in the browser or request helper tests.
- Add browser coverage for complete, changed and faulted results so the announcement matches what was stored. Keep the existing 404 and 503 window cases covered.
- Add a bounded shutdown case with a helper read held open. Verify shutdown reports the live sampler, then release the fake helper so the test leaves no thread behind.
- Run the speed-test unit file, the dashboard budget check, the speed-test desktop/phone browser journey and the configured Ruff gate on the exact final commit.
- Run the supported Python matrix and the local Pi Linux release workflow when available. Record unavailable hosted checks as unavailable, not as passing.
- Review the complete final diff after these changes. Do not infer Pi acceptance from the browser fixture or local Linux workflow.

## Risk, data and operations

- **Risk:** Low for access control and stored data. Moderate for whether a speed-cap conclusion and recorded profile are trustworthy, start request latency and whether the end message is accurate.
- **Data:** No new category of information is read, stored, exported, logged, retained or deleted. The existing JSON result summary gains an optional nullable service-rule state. CSV exports continue to carry the verdict through their existing verdict columns.
- **Security:** No changes to authentication, authorization, tenant boundaries, protected-device rules, helper commands, firewall rules or external requests.
- **Deployment:** No dependency, package, migration, backfill, service or configuration changes. The existing release build and rollback path remain valid.
- **Failure handling:** An unavailable applied-plan object or expected queue state must not create a confident cap conclusion or controlling-profile claim. Start-request timeout must stay above the bounded helper retry budget with room for route work. A result that was saved with a changed or fault status must not be presented as a complete measurement.
- **Scale:** Read the already-held plan and scan only its bounded rule lists. Add no per-second database, helper or network calls and no new background work.

## Review gate

The 10 October review record also mentions a hung-helper shutdown test gap. The plan includes that test because it covers the new bounded join behavior; it does not expand the production scope.

An independent reviewer should assess this plan and the behavior against the current runtime plan model. The human reviewer should read this summary and that agent review, then give feedback. No production code changes begin until that feedback is recorded.

Human feedback recorded on 14 October 2026: “The proposed behavior looks right.”
