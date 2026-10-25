# Give all Pi browser journeys a bounded run

## Summary for review

The Pi browser job runs 36 modules in sequence but its 15-minute ceiling
cancels the current passing run at module 30 or 31. Increase only this job's
limit to 25 minutes. Keep the test list, assertions and application deadlines.
This is risk class A with no household network effect.

There is no measured full hosted runtime yet. Twenty-five minutes is a finite
verification budget with ten minutes beyond the observed cutoff. A complete
run must establish whether it is sufficient. Prefer this one-setting change
over adding several jobs and maintaining new grouping rules. Reconsider
parallel jobs only if completed-run timings justify that extra work.

## Evidence and verification

- Main `3d60f8a8`, run 40199902213, job 121188667711, reached `phone_layout`
  before cancellation after about 15 minutes 12 seconds. Six modules remained.
  The corrected observation and chart modules had finished without an
  assertion failure. Final head `e7f962b7` reached the next module before the
  same cutoff. These are incomplete runs, not passing suites.
- The current source is `.github/workflows/wifi-recovery.yml:57-106`.
  [Exact-main job](https://github.com/smartinezai/jittersim/actions/runs/40199902213/job/121188667711).
  The local counterpart list in `scripts/local-ci.sh` remains unchanged.
- Complete the already started local full browser run on exact main. Keep its
  receipt distinct from hosted evidence and do not call it physical acceptance.
- Obtain independent review and human feedback before implementation. Change
  only the job limit and its explanatory comment. Check Actionlint and the
  existing local-CI parity tests, then review the whole diff independently.
- Publish the reviewed PR and follow its actual hosted job to completion.
  A remaining failure must be diagnosed from its test verdict. A cancellation
  must never be relabeled as a billing refusal or a successful run.

## Release and recovery boundary

The 1.1.0 release record already refers to a cut with the old limits. Changing
later main does not change that cut's workflow or make its canceled checks
successful. Keep the existing release verification policy. Any replacement
release cut or retirement decision stays with the current release owner.
This plan does not authorize signing, installation or a policy bypass.

No permissions, dependencies, build inputs, data or Pi state change. No clean
container rebuild is needed for a job ceiling alone. Revert the commit to
restore the earlier limit. No new unit test is needed for a static setting.
