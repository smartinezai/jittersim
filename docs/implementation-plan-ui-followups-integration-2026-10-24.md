# UI follow-up integration

Combine the reviewed local follow-ups in a new isolated branch from main 53ec72bb94f09a33aa5b55971c5446163c24e419. Preserve every parent commit through ordinary merge commits. Do not change product behavior during composition. Source conflicts need a concrete hunk review before manual resolution. No push or main merge is included.

## Approved inputs

| Area | Exact head |
| --- | --- |
| Discovery membership and collapse | 47ea235dccc68ccb1d735e690a3974b6f5357369 |
| Release documentation | 40c0fac39c326bfdb34ee15eab8ba35dbf00c5d1 |
| Notification layout and order | 6db0675297734953120fa8830be61cf773b2db95 |
| Empty group selector | a50839b2014a2e6d22cfcb6ea7ec10d4abbafbe2 |
| Schedule form fit | a78aad652f93baead93d7d2446158c20bf63719d |
| Landscape layout and action wrapping | 0bb76516a41c88e269a0699a27b1769efa231f26 |
| Profile reset controls and budget metadata | 365baac1cb075d7a97e7a33527fb1dd4a6c1174e |

The profile input may be composed last but final freeze waits for the author's final validation and review. Preserve landscape media-query count 14 and the six approved profile metadata entries. Do not add a generic reset change.

## Procedure

1. Claim sole writing responsibility for this branch in the central append-only claims file.
2. Commit this unique implementation plan before merges.
3. Merge each exact head in the order above. Stop on source conflicts and report exact hunks plus a narrow proposal.
4. Record a per-file source manifest with input blob identities and final blob identities. Prove that all input commits remain ancestors.
5. Compare src/jittersim/panic_guard.py, src/jittersim/networking/helper.py and src/jittersim/networking/access.py against 03fe31d8a1695dd42c9e213540f3127b555e8640.
6. Check the held ancestry and exclude chart 415d4595, unfinished discovery work and held 175/209/I changes.
7. Report the candidate head, tree and conflict status. Run no gates until the coordinator approves all final inputs and assigns the combined checks.

## Boundaries and risk

This task combines existing changes. It adds no new endpoint, stored data, network request, dependency, deployment operation or migration. The risk is interaction among nearby UI changes and stale validation metadata. The final combined gates must cover those interactions after the source is frozen. Rollback means discarding the isolated integration branch or reverting its merge commits. No hardware, credentials, reset operation, publication, rebase or squash is authorized.

## Review state

The coordinator approved this exact composition scope under the existing explicit authorization for ongoing implementation. The initial plan was created before merges. Final input readiness and the combined validation assignment remain pending.
