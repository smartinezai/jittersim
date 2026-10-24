# Per-page browser titles

## Summary for review

The dashboard currently gives every signed-in page the same browser title. Use the
shown page's existing static region label before the private product name. Keep
`Sign in` until the existing private branding reply arrives and restore it when
access is lost. Share a small title updater between `showPage` and `applyBrand` without changing
navigation or other branding effects.

Scope approved on 24 October 2026. There are no open design questions. The main
risk is revealing private branding after sign-out or letting a delayed branding
reply overwrite the current page title. Existing reply ownership checks stay in
place and focused tests cover these transitions.

## Source and decisions

- `dashboard.html` already labels all ten page sections with static `aria-label`
  values. Read those labels rather than navigation text with live device counts
  or update badges. Do not add visible headings.
- `dashboard-navigation.js` resolves the initial hash and history changes through
  `pageFromHash` and `showPage`. Refresh only the title after its existing section update
  using the existing guarded cross-script convention.
- `dashboard.js` already controls the private name through `applyBrand`. Combine
  the resolved section label with that name using a middle dot. Preserve the
  existing name-only fallback when there is no matching section. The shared
  `updatePageTitle` helper writes only `document.title`. Navigation must not run
  default-SSID initialization, change branding or advance the request serial.
- Do not add a product name literal to public markup or scripts. Never include
  device names, counts, token text, errors or other user data in the title.

## Verification

Extend the existing branding test harness with the served page labels and real
navigation functions. Cover every known page, initial deep links, the unknown-hash
Devices fallback, programmatic tabs, plain and modified clicks, history changes,
late replies, blocked access and sign-out. Verify a deliberately cleared SSID draft
remains blank across navigation with focus outside the field. Check navigation
preserves branding and the branding request serial. Keep existing phone focus and scroll
checks unchanged. Run focused branding, neutral sign-in, navigation, impact and
refresh tests plus narrow lint, asset budgets and documentation links. Record
results under ignored `test-results/page-titles`.

Independent source review precedes any full suite or browser run. No aesthetic
screenshot pass is needed because document titles do not change the page layout.
Run the scoped Impeccable detector once after implementation.

## Risk and delivery

Risk class B. Household risk is none because this only updates document metadata.
There are no API, network, helper, storage, build, dependency or installation
changes. Work per transition stays constant and adds no requests or retained data.
The normal dashboard asset assembly carries the change into the existing package.
A source revert restores the old title with no migration or data cleanup.

Do not edit Panic-pinned files or use real devices, secrets, publication, pushes
or merges. Per-page headings and other accessibility backlog work remain open.

## Status

Implemented on 24 October 2026 after scope review. The focused checks passed with
496 tests. Narrow lint, JavaScript syntax checks and the scoped Impeccable detector
also passed. The detector returned no findings. Independent source and privacy
review found no blocker. The existing dashboard-access browser journey now expects
the exact Devices title after sign-in while retaining exact neutral sign-in
checks. The final focused branding, neutral sign-in and phone navigation batch
passed with 69 tests. The initial full native gate passed with 8,857 tests and 118 skips on `0603dc2d`.
The final frontend delta review then found that calling all of `applyBrand` during
navigation refilled a cleared SSID draft. The browser gate was stopped and its
interrupted result retained before source edits. A new regression failed on that
commit and the correction shares a title-only helper. The corrected commit needs
fresh source review and full gates. The corrected focused batch passed with 498
tests. Narrow lint, JavaScript syntax and static budgets passed. No push or merge
occurred.
