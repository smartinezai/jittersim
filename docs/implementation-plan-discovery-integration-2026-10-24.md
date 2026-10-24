# Discovery integration plan

Combine the reviewed device-collapse and global-list wording changes on verified main 53ec72bb. Preserve both original PR heads and resolve only their overlapping browser-test insertions. Product source must retain the automatic merge result. The existing coordinator remains the only GitHub merge writer.

## Steps

1. Merge PR229 head70c13820 then PR231 head9b5a96ab locally in this isolated branch.
2. Retain both complete browser helpers and invoke both for each width before scenarios mutate profile state. Preserve collapse, focus, count, membership and narrow-layout assertions.
3. Check parentage and the complete difference from current main. Confirm pinned files remain unchanged. Freeze the combined commit before verification.
4. Request independent review of the one-file resolution and final source identity from the existing discovery owner. Correct any confirmed regression on this branch and repeat affected review/checks on its final head.
5. Run exact-head lint, full native unit, full browser and GUI through the shared wrapper. Avoid duplicating coordinator-owned validators. No Linux/kernel change is included and no additional Docker gate is planned unless review identifies a relevant need.
6. Give the coordinator the exact head/tree, results and merge order. Do not publish or merge without its specific branch/PR instruction.

## Boundaries

No changes to the three pinned files, original PR branches, unrelated UI work or the previous frozen candidate. No hardware, credentials, signing, release publication or reset. Preserve all previous failures. Owner release freeze and PR175/209/local-I holds remain binding.

## Review

This plan follows the coordinator-assigned one-file composition and current autonomous implementation authority. Independent review is required before the combined change is handed back as ready.
