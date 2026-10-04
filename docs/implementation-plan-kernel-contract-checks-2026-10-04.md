# Correct the final kernel acceptance contracts

The exact748 kernel run passed38 cases and failed two. The post-Panic test
reused an apply request without the fresh control revision now required by the
reviewed helper. The range test inspected download queues while expecting
upload percentages. Download uses bursty loss with its own kernel parameters.
Correct only these tests and repeat the complete frozen verification.

The implementation and approved Panic source tuple remain unchanged. The main
risk is weakening a test to accept unsafe behavior. Add explicit refusal of
stale post-Panic requests and fresh requests without an explicit Start before
applying with the current revision and explicit_apply enabled. Keep
household access disabled and verify actual upload and download packet flow.
Check upload settings on eth0 and download burst settings on wlan0 separately.
Keep all four upload percentage assertions and check both burst probabilities.

1. Retain the actual failed40-case run, logs, image identity and cleanup result.
2. Independently review the helper revision contract and queue directions.
3. Correct only the two kernel tests. Use the public helper control-revision
   action rather than private state or bypassing its safety checks.
4. Review the complete diff and verify application, deployment and approved
   Panic bytes remain identical to748. Run relevant existing host tests.
5. Freeze the corrected test source and rebuild the source bundle and wheel.
6. Repeat the complete configured Linux matrices, browser checks, clean builds,
   all13 native commands, all40 kernel cases, CA permissions and locked packages.
7. Publish only after actual completed results and final source review.

This changes no runtime behavior, credentials, retained evidence or live Pi
state. The earlier kernel failure remains a failed run. No stable publication
or physical acceptance is inferred from the corrected software checks.
