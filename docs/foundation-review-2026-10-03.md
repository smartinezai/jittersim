# Remaining foundation review

The service-list variation merge needs a small general-profile integration repair. The browser proxy also has a pre-existing unfinished-DNS-work defect. Both are being repaired separately on this isolated branch. The semantic-version and deployment changes have no new blocker in isolation but their combined signing and first-upgrade paths need deliberate integration and acceptance tests.

This is a source review and local reproduction record. It is not evidence of device acceptance or green CI.

## Reviewed sources

| Change | Exact source head |
| --- | --- |
| Service-list variation, PR50 | `42ceccaa7a7d5d281b667853fef3613e942b68b8` |
| Semantic versions, PR60 | `473d6e1ac3d48adf1fd235c8a66c1275d7ea78fa` |
| Deployment version choice, PR74 | `1e8edb1517dfcf4ddbd47464ff5bfc86cb3bacc2` |
| Remote CLI/deployment, PR46 | `7899f5a630cf4437090fcc967daf29ea72d92068` |
| Release signing, PR52 | `4330aa21c590b354da31754545b295737ec06da1` |
| Historical combined foundation | `180f6c022030a19de0192bab13473deabce1dbb6` |
| Repair implementation foundation | `6bf1bec98c139dbcc5bdd71b21854ff064279e26` |

The live source heads were checked on October 3. The historical foundation contains the household MAC repairs and the bounded website/service DNS work. The implementation foundation adds the verified helper response-lock repair. Original source branches remain untouched.

## Service lists and general profiles

PR50 adds an optional supplied list-rules argument. Foundation change `fe281b3` introduces the shared lookup-session wrapper. A clean automatic merge combines those source changes without passing the new argument into the inner function. The resulting reference raises `NameError` and breaks general-profile Start, scheduled changes and list saves in general mode.

An independent temporary combined snapshot produced 355 affected passes and 23 failures with this same error. On the actual isolated merged branch the selected existing general/variation checks produced 48 passes and 22 failures. Four new direct compilation regressions also failed before repair.

The separate repair preserves saved-list fallback, supplied rolled values, supplied outage state, intentionally empty lists and one DNS deadline for the whole general plan. Existing household MAC checks passed in the broader combined snapshot. No additional blocker was found in list validation or outage rule ordering. Combined real packet, browser and CI checks remain necessary.

Sources: [PR50 general compilation](https://github.com/smartinezai/jittersim/blob/42ceccaa7a7d5d281b667853fef3613e942b68b8/src/jittersim/engine/general.py#L20) and [foundation lookup wrapper](https://github.com/smartinezai/jittersim/blob/180f6c022030a19de0192bab13473deabce1dbb6/src/jittersim/engine/general.py#L19).

## Browser proxy unfinished work

The browser connection limit does not bound DNS work that remains after a timeout. Eight workers can stay blocked while repeated CONNECT requests add work to their executor queue. A socket probe retained 8, 24 and 40 queued lookups after 16, 32 and 48 requests. Separate baseline regressions on the actual branch reproduced eight running plus 40 queued lookups and 24 stalled workers after three server recreations.

This defect predates PR50 and remains after `60b1792`. The separate repair shares eight workers and at most 32 physically unfinished items across server instances. Queued cancellation must retain admission capacity until a worker consumes the cancelled item. Closure must handle cancellation cleanly and must not shut down the shared pool.

Sources: [lookup submission](https://github.com/smartinezai/jittersim/blob/180f6c022030a19de0192bab13473deabce1dbb6/src/jittersim/networking/browser_proxy.py#L266) and [executor creation](https://github.com/smartinezai/jittersim/blob/180f6c022030a19de0192bab13473deabce1dbb6/src/jittersim/networking/browser_proxy.py#L388).

## Semantic versions, signing and deployment

PR60 and PR74 have no new source blocker in isolation. Keep these combined acceptance requirements:

- The first upgrade needs the normal installer. PR74 forwards labels such as `0.2.0.dev+COMMIT`, which existing PR72 and unchanged signing-PR52 update managers reject. The signing README promise that ordinary `--install` works on a pre-signing Pi must be reconciled with this limitation. Use `--use-installer` for that first combined upgrade unless an explicit compatible path is verified.
- Combine the semantic-version builder with signing deliberately. PR60's builder accepts two arguments while PR52's deployment script supplies a third signing-key argument. Retain the signed `deploy/RELEASE` manifest, release sequence and semantic version together.
- Preserve both downgrade checks and signature/order checks. Keep the apply-time signature check and persistent release floor.
- Update combined signing fixtures to include valid package versions. Verify signed tagged and untagged bundles through real staging with both normal-install and already-upgraded-manager paths. Retain acceptance of existing installed release labels where required.

Exact-source execution confirmed that older managers reject the development label and PR60 rejects a development build after the same numbered final release. Read-only merge inspection found the expected builder/updater/documentation/test conflicts between PR60 and PR52 and deployment-script/test conflicts between PR74 and PR52. Resolve them before final review.

Relevant source points are PR74 `deploy-from-mac.sh:403`, PR60 `build-release.sh:12` and `updates.py:385`, PR52 `updates.py:744` and `updates.py:893` and PR52 signing fixtures `test_release_signing_scripts.py:273`. The historical foundation did not yet include the remote CLI/deployment script.

Physical first installation, signing trust setup, passwordless staging and rollback still need the deployment owner's acceptance. No physical Pi command was run during these source reviews.
