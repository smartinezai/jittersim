# Device profile dropdown verification

The Assign a device form now offers a saved-profile dropdown. It shows names
and adds the ID when names match. Saving uses the selected ID. Refreshes preserve
a valid selection and remove one that is no longer available. Empty lists,
failed loads and credential loss prevent saving. A failed load offers Refresh
profiles as recovery.

## Source and complete change

- Branch: `codex/device-profile-picker`.
- Base: `04c982d6150d225c3be6cc082573afc64a656842`.
- Checkout: `/Users/samantha.martinez/Github repos/jittersim.worktrees/device-profile-picker`.
- Review the complete change with `git diff 04c982d6150d225c3be6cc082573afc64a656842..HEAD`.
- The implementation plan is `implementation-plan-device-profile-picker-2026-10-04.md`.
- Application changes are limited to `dashboard.html`, `dashboard.js` and
  `workflows.js`. Other changed files update existing tests and record this work.

## Actual checks

1. Focused dashboard tests: **119 passed** in 5.07 seconds on the final source.
   Command: `PYTHONPATH=src /Users/samantha.martinez/Github\ repos/jittersim/.venv/bin/python -m pytest tests/unit/test_dashboard_saved_profiles.py tests/unit/test_dashboard_sidebar_inputs.py tests/unit/test_dashboard_help_text.py tests/unit/test_dashboard_auth_state.py -q -o addopts='' --tb=short`.
2. API and device-management tests: **375 passed** before the final small change
   that hides an empty status note and clears the warning after a valid choice.
   Command: the same Python runtime with `-m pytest tests/integration/test_device_management.py tests/unit/test_api.py -q --tb=short`.
3. Final API follow-up: **5 passed** with 341 deselected in 0.26 seconds. These
   check saved-item editing, profile-menu rebuilding and served-script parsing.
   Command: the same runtime with `-m pytest tests/unit/test_api.py -k 'dashboard_can_edit_and_delete_saved_items or rebuilt_profile_menus_keep or profile_menus_keep_the_everyone or dashboard_script' -q -o addopts='' --tb=short`.
4. Browser journey: `python -m tests.browser.saved_profiles` passed on the final
   source at **1920 × 1000** and **390 × 844**. Runtime:
   `/private/tmp/jittersim-erratic-browser-venv/bin/python`. Chromium:
   `/Users/samantha.martinez/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing`.
   The journey checks required selection, saving the intended ID, names that
   match, preserved selection, empty and missing choices, failure recovery,
   access clearing and draft restoration. Screenshots were inspected together
   then confirmed after hiding the empty status note:
   `test-results/device-profile-picker-1920.png` and
   `test-results/device-profile-picker-390.png`.
5. Focused lint and `git diff --check` passed. The design detector reported a
   type-size warning based on default sizes because it scanned the extracted
   HTML without its separate stylesheet. Existing CSS declares a larger h1 and
   h2. No typography change is part of this feature.
6. A wheel was built from a fresh source copy with the bundled Python runtime
   and setuptools. The three edited assets matched their packaged bytes.
   Extracting the wheel and loading its dashboard assembly module produced
   the dropdown without unresolved asset markers. An earlier package was also
   imported with the existing application dependencies.
   Wheel: `/private/tmp/jittersim-device-picker-final-package-xf62nfxh/wheels/jittersim-0.2.0-py3-none-any.whl`.
   SHA-256: `95f05528c37a345ab44278ba1895e8f0d57406650c8f8c30e51ae6f4cc61f3ab`.

## Review and release limits

The source review found no remaining blocker in this change. Selection is
explicit. Options use text content. A request counter prevents an older load
from replacing the newer list and is invalidated when private data is cleared.
The existing authenticated request path still rejects responses from an old
credential revision. Device-edit checks are preserved.

Draft restoration is changed only for controls marked as requiring an available
option. Currently only this profile dropdown has that marker. Work remains one
pass through the existing profile list with no additional polling or request
per profile. Future large lists should share search or paging across profile
menus.

Local socket checks required execution outside the filesystem sandbox. Initial
sandbox-only attempts could not bind localhost. A browser approval review timed
out once and the permitted retry succeeded. The first small test fixtures lacked
the full profile shape used by the saved-profile summary. They were corrected
before the successful checks above. The original Python runtime lacked the build
backend. The clean package was built with the bundled backend then imported with
the existing application dependencies.

No Docker check, remote push, merge, Pi deployment or live network change was
performed. The final composed release still needs its own review and checks
after combining the other dashboard changes. Rollback requires restoring the
previous application assets. Saved device records and database structure are
unchanged by this feature.
