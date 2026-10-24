# Require fresh identity for manual device Apply

Manual Apply must verify the selected Wi-Fi client from a successful current helper scan before saving or starting its profile. The scheduler deliberately keeps a recent client list during brief helper failures. That continuity rule must not authorize a new manual action. The repair will reject stale evidence in the existing selected-client check while preserving healthy Apply and Reset/Panic ordering. No deployment, storage format or helper behavior changes are planned.

This is the explicitly authorized urgent implementation scope. The global working agreement was read before implementation. Independent review follows the focused local tests before any publication.

1. Inspect current selected-client verification, Apply ordering and existing stale-cache tests. Stop and report if manual acceptance is an explicit policy.
2. Add focused regression coverage using the existing API harness, a temporary database and a fake helper. Demonstrate the verifier and real Apply accepting matching stale identity on the release baseline. Record the failure before changing production code.
3. Reject stale identity before any save, configuration version change, runtime update or helper Apply. Keep the existing conflict response and refresh instruction.
4. Run only the new tests and relevant existing device Apply, Reset/Panic and scheduler continuity tests through the native test wrapper. Inspect the final diff and report exact source revision, baseline proof and results for independent review.

The change adds one bounded in-memory condition with no new I/O, retries or scale-dependent work. Existing installed packages supply the test environment. Rollback is reverting the selected-client condition. The remaining limitation is the existing interval between a successful scan and helper Apply. Closing that interval would require a separate helper contract change outside this task.
