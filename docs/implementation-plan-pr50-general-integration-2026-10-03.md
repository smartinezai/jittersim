# Keep service lists in general profile compilation

## Review summary

The service-list variation change and the existing shared DNS deadline each work separately. Their clean source merge breaks every general profile because the new list argument is not passed through the compilation wrapper. Keep both behaviors by carrying the supplied list rules into the inner compilation step while preserving the one DNS session for the complete plan.

This is a small integration repair with medium release risk. The source change stays in general profile compilation. Tests must show that saved lists still work, scheduler-supplied list values are retained and all lookups share the existing deadline. No stored data, settings, authentication or deployment change is needed. The release coordinator assigned this repair under the current 24-hour takeover authorization and owns final publication and device acceptance.

## Source evidence

- PR50 head: `42ceccaa7a7d5d281b667853fef3613e942b68b8`.
- Reviewed foundation: `8d871a7450e4a104985afd7c7b3994964bf75889`. Before source edits inherit `6bf1bec98c139dbcc5bdd71b21854ff064279e26`, which adds the reviewed helper response-lock repair.
- PR50 adds `service_list_rules` to the public general compilation function.
- Foundation change `fe281b3` wraps that function in a shared DNS session.
- A three-way merged source refers to `service_list_rules` inside the inner function without declaring or forwarding it.
- Independent combined-source tests reproduced 23 failures with the same `NameError` and 355 passes.

## Steps

1. Have a separate reviewer consider this plan before merging source into this isolated branch.
2. Merge the immutable PR50 source locally into the reviewed foundation. Do not alter either original branch.
3. Run existing affected tests on the actual merged working tree and capture the general-profile failure.
4. Add direct general compilation tests for intentionally empty supplied rules and the shared lookup budget through the actual wrapper. Existing PR50 tests already cover rolled values and outages in general mode. Preserve existing saved-list, scheduler, outage and protected-client assertions.
5. Carry the optional supplied list rules through the public wrapper into the inner compilation step. Preserve the distinction between no supplied value and an intentionally empty list.
6. Run the affected general, service-list and DNS tests. Run required lint and the complete local suite after the separate proxy DNS repair is ready.
7. Obtain independent source review. Give the coordinator the exact focused commit and source base. Combined kernel, browser and exact-head green CI remain release gates.

## Reliability and growth

Keep the existing bounded shared lookup deadline and shared resolver capacity. Do not create another DNS session for each list or bypass the existing timeout reporting. Carry already computed values into compilation so the scheduler does not silently repeat work or discard rolled list settings. Limits on lists, domains and queue marks remain unchanged.

## Security and data

No new data source, endpoint, database column or log is introduced. Existing household restrictions and protected-device checks remain. No migration or backfill is needed. General profile outages and per-list exceptions must continue to use the same intended rule ordering.

## Deployment and recovery

The final release uses the normal installer and the coordinator's deployment plan. This repair needs no extra environment command. Roll back with the previous official release if combined acceptance fails. No physical device command is part of this repair.

## Verification record

Independent plan review approved forwarding the optional rules through the existing DNS wrapper. It required direct empty-input and shared-budget tests. These requirements are included above. Record the exact before/after failures, focused checks, independent source review and final source commit here before handoff.


Completed source repair: `ab2a8da749cb38914cdab8bd5f97b6ba5257872c`.

- The actual merged source produced 22 failures and 48 passes in selected existing general/variation checks. All four new direct compilation cases failed with the same `NameError` before repair.
- After repair 121 affected general, variation, service-list, website, resolver and lookup-pool checks passed.
- Direct coverage preserves saved fallback, supplied rolls and outages, intentionally empty input and one shared lookup deadline. The deadline check observes lookup admission rather than depending on worker startup timing.
- Independent source review found no production blocker. Its test timing concern was resolved before the source checkpoint.
- Final combined source `894d40ec346c20e7c2b933dc158a6c47e51f0fe8` passed 3,071 tests with 39 expected skips on both Mac and Linux. Required Ruff and whitespace checks passed.
- Fresh installed-wheel general-profile, protected-client and complete browser journeys passed. Service-list packet acceptance passed for rerolls, outages, both directions, IPv4, IPv6, protected clients and continued Pi DNS.

The release coordinator carried the source into its local combined candidate. Exact-head green CI and final combined deployment acceptance remain separate gates. No original source branch, remote ref or physical Pi was changed by this repair.
