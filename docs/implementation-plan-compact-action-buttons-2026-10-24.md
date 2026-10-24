# Compact action buttons scope

Keep the current dark interface and reduce only main-page action buttons on desktop with a fine pointer. Preserve all safety controls and touch targets. The main risk is an overly broad selector changing controls that already have special sizing. Use a limited selector with explicit exclusions and check the computed sizes in a local fixture.

## Current evidence

- `access.css` gives every content button a 2.75rem minimum height. The default button also has 0.62rem vertical and 0.9rem horizontal padding.
- Secondary controls in rows use smaller padding but still inherit that minimum height.
- Schedules uses primary blue for many unrelated card actions. Profiles has no primary save action.
- Sidebar Start reserves two lines to avoid layout movement. The safety bar and Panic have separate sizing rules.

## Implemented change

1. Add a desktop fine-pointer rule for buttons inside main pages. Use a 36px minimum height and 44px minimum width. Reuse existing compact padding values to stay within the committed style budget.
2. Exclude the whole sidebar, the Router page, the Updates page, help icons, card disclosure controls, observation group disclosures and traffic legend controls.
3. Retain the existing main action on Devices. Make Save profile the main action on Profiles and Save schedule the main action on Schedules. Quiet the other Schedules card actions and Traffic Scan now. Keep Save speed references primary on Traffic.
4. Use class-only changes to give Measurements Copy table, Activity Copy as Markdown, Access Save access rules and Diagnostics Refresh a main action.
5. Retain the current dark secondary background and reduce its shadow within the same scope. Preserve danger styles, focus, disabled states and all semantics.

## Verification

- No JavaScript changes and no label, ID, handler, authorization or confirmation changes.
- Run the existing static style budget without changing its limits.
- One batched desktop and mobile fixture pass. Measure default, hovered, disabled and keyboard-focus buttons plus coarse-pointer targets. Check 320px width and 200% zoom for wrapping.
- Run existing GUI and axe checks through the shared browser slots after approval. At most one additional batched pass to confirm fixes.
- Run the final Impeccable detector once against changed UI source. Record existing findings separately from introduced findings.
- Complete applicable local checks only after scope approval. No real devices, publication, pushes or merges.

## Boundaries

No new fonts, themes, components or asset budget increases. No dashboard redesign. No router status, authentication refresh, focus return, access polling or release label work. Never edit Panic-pinned Python files. Rollback is a source revert with no data change.

Scope approved on 24 October 2026 for this bounded C3 follow-up. The approval covers CSS and class changes on unowned main pages. It preserves the existing dark interface and does not start the held dashboard redesign.

Risk class B. Household risk is low because the change affects presentation only. Network behavior, stored data, authentication and the update path do not change.

The scope review also approved removing one redundant coarse-pointer traffic legend rule. The general content button rule already gives those controls a 2.75rem minimum height. Keep the legend excluded from compact sizing and verify its visible target size in the existing traffic chart journey on fine and coarse pointers. The bounded visual fixture supplied no counters so its legends stayed hidden. This makes room for the fine-pointer condition without increasing the committed media-query count or any budget.


## Status

Implemented on 24 October 2026 after scope review. Focused style-budget, source and documentation checks passed. The second and final visual batch passed after correcting the first batch's synthetic baseline response policy. Visible legend checks are part of the existing traffic chart journey. Clean-commit review and full local validation remain pending. No push or merge is authorized in this task.
