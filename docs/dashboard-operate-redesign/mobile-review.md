# Follow-up after the independent mobile report

The report below preserves its original failed receipt. The sole writer subsequently
changed only help_icons fixture setup to dismiss the specifically asserted sign-in
success acknowledgement. The complete wrapper module then **passed** at
2870fc2bf2b2a5a9407997174bc3bc2c985e022e in local-ci-20261024-192826 (34 seconds),
covering all configured pages, 1280/390/320 widths and normal/wider fonts. This
supersedes only the report's statements that the full help module remains failed
or unrun. Its physical/Safari/text-scaling and other-dialog limitations remain.

---

# Mobile acceptance review — 24 October 2026

Reviewed read-only tracked snapshot `redesign-gui-worktree` at `bb98e4d767974e70e03da9011b52d56e206aa38b`. `git diff df1816dd bb98e4d7 -- src` is empty, so production source matches frozen `df1816dd5926015a6f01c597dfe69515e21b25d2`. Tracked working tree remained clean. No source, test, documentation, release or main changes were made by this lane.

## Outcome

No new persistent mobile product defect demonstrated. Existing evidence covers the phone shell, navigation, page overflow, ordinary targets and wider fonts. New bounded Chromium checks establish actual phone confirmation-dialog geometry and clarify a failed help-icon test. Real phone, Safari and Pi/network acceptance remain outstanding.

The full existing `help_icons` module still records **FAIL**. Do not label the module PASS: its sign-in toast intercepts a sampled help-icon target before the fixture dismisses it. An independent bounded diagnostic proves the sampled target and tooltip work after the user dismisses that toast. The sole writer can address the fixture setup; this lane made no fix.

## Requirements and reused evidence

Sources: historical `docs/history/2026-10-02-backlog-and-ux/planned-features.md` §9, active `docs/planned-features.md` phone row, and mobile entries in `docs/planned-fixes.md`.

| Acceptance item | Evidence and limit |
|---|---|
| Panic and router control remain visible at 390×844, 360×640 and 320×568; page begins on first screen | `redesign-worktree/test-results/local-ci-20261024-190737/browser_phone_layout.log`: PASS at all three sizes with normal and 112.5% wider fonts, including end-of-page safety clearance and nine short-window/landscape cases. The router status is intentionally read only in the current safety design. |
| Navigation and scroll-to-page-start; no sideways page overflow | Same `phone_layout` PASS. Horizontal scrolling belongs to the page-link row itself and is explicitly intended; it is not page overflow. Parent reports the final separate navigation rerun PASS at test/docs head `67fac7c7`; that later run was not repeated by this lane. |
| Apply, Reset and credentials in Controls; usable touch targets | Same `phone_layout` PASS includes Controls sheet positioning/focus, computed ≥44 px targets and six explicit manual Allow enrollment cases. Effective help hit testing is separately discussed below. |
| 375 px page presentation, light/dark and accessibility observations | `redesign-gui-worktree/test-results/local-ci-20261024-191545`: 37 GUI pytest cases PASS, 40 baseline states PASS and 40 axe states PASS. These are sign-in plus nine page views at 375/1440, light/dark; they do not establish phone dialog geometry. Known axe `skip-link` observation is already triaged in planned fixes. |
| Status/live announcements | `redesign-worktree/test-results/local-ci-20261024-191317`: `sidebar_status` and `live_regions` PASS. The confirmation checks in `live_regions` occur before resizing to 390 px; do not infer phone confirmation coverage from that module. |
| Recovery and apply behavior | `redesign-worktree/test-results/local-ci-20261024-191832`: `wifi_recovery`, `apply_clarity`, `wifi_restart_reconnect` PASS. Recovery confirmation checks occur at desktop width; the later phone credential check is separate. `apply_clarity` does not supply confirmation-dialog geometry. |

The reused summaries contain historical failures in other steps. Only the named PASS steps above are reused; the full mixed runs are not represented as green.

## Fresh help check and diagnosis

Executed the requested existing wrapper, one actual browser process at a time:

```sh
env PLAYWRIGHT_BROWSERS_PATH="$PWD/test-results/playwright-private" \
  PIP_NO_INDEX=1 PIP_NO_BUILD_ISOLATION=0 \
  PIP_CACHE_DIR="$PWD/test-results/pip-cache" \
  /private/tmp/claude-501/jittersim-heavy.sh --python 3.12 --only help_icons browser
```

- `redesign-gui-worktree/test-results/local-ci-20261024-192407`: sandbox blocked temporary synthetic Unix socket (`PermissionError: Operation not permitted`); no browser assertion ran.
- Approved local-only escalated retry `local-ci-20261024-192422`: setup/cache PASS; `help_icons` FAIL after 6.7 seconds at `profiles 390x844 normal fonts`, cardinal point `(-20, 0)` beside the first `Profile preset help` icon (`help_icons.py:131,217`).
- Read-only stdin diagnosis used `lockf -s -t 0 /private/tmp/claude-501/jittersim-slots/browser-1.lock .venv-local-ci-3.12/bin/python -`, because the original wrapper only accepts named modules. It used the same existing slot pool, cached Chromium and synthetic localhost fixture; no capacity expansion, download, Pi/router or Docker operations.
- Initial actual icon box: `(x=170.84375, y=716.6875, width=20, height=20)`. All four sampled points hit `#toast`, `#toast-dismiss` or `#toast-message`. The toast says “Token accepted. Diagnostics loaded.”; temporary coverage of content with a Dismiss action is explicitly intended in planned fixes.
- The first diagnostic mistakenly searched for accessible name `Dismiss`, although the observed button has `aria-label="Dismiss message"`, and therefore skipped dismissal. Its `persistent_help_failure` label is superseded by the corrected diagnostic, not a product finding.
- Corrected diagnostic clicked observed `#toast-dismiss`, asserted toast hidden, then found all four ±20 px cardinal points hit the intended help button at both 390 and 320 px. Sampled tooltip viewport fit, focus, Escape and outside-click behavior passed using the existing helper.

Evidence: `mobile-review-evidence/diagnostic.log`, `diagnostic-corrected.log`, `help-390-diagnostic.png`, `help-390-dismissed.png`, `help-320-dismissed.png`. The full help module stopped early, so it does not newly establish all pages, wider-font cases or all tips.

## Fresh actual phone dialog checks

Opened the actual **Reset Wi-Fi name** confirmation from Controls and actual **Delete Mobile review profile** confirmation from its saved profile row. Only Cancel and Escape were used; no confirmation action was accepted and no reset/delete POST occurred. The profile existed only in the disposable synthetic fixture.

| Case | Reset dialog | Delete dialog | Buttons and flow |
|---|---|---|---|
| 320×568, 112.5% Verdana/wider font stress | x27/y21, 266×526 | x27/y121.625, 266×324.75 | ≥49.5 px height; all buttons within viewport |
| 375×812, coarse pointer (`has_touch=True`) | x24/y160.6875, 327×490.625 | x24.078125/y316.6875, 326.84375×178.625 | ≥44 px height; all buttons within viewport |
| 390×844, normal fonts | x24/y188.6875, 342×466.625 | x31.578125/y332.6875, 326.84375×178.625 | ≥44 px height; all buttons within viewport |

All six cases: dialog box within viewport, document width equals viewport width, initial Cancel focus, Escape closes and restores opener focus, reopening then clicking Cancel also restores focus, and zero reset/delete writes.

At 320 px with wider text the long Reset dialog scrolls internally. Native focus on Cancel initially sets scrollTop=393 (clientHeight=524, scrollHeight=917), so its heading is initially above the visible portion. A separate bounded check scrolled to 0 and verified the complete heading is in view, then scrolled Cancel into view and cancelled successfully with focus returned. This is scrollable long copy, not simultaneous visibility of all copy.

Evidence: `mobile-review-evidence/diagnostic-corrected.log`, `dialog-scroll.log`, `dialog-reset-{320,375,390}.png`, `dialog-delete-{320,375,390}.png`, and `dialog-reset-320-top.png`. Each diagnostic held one existing shared browser lock and released it at completion.

## Remaining acceptance limits

- Actual phones and Safari/WebKit were not exercised. Chromium viewport/coarse-pointer emulation does not establish Safari behavior, dynamic browser chrome, safe-area insets, virtual keyboard interaction, real touch accuracy, VoiceOver/TalkBack or OS text scaling.
- Wider text is 112.5% plus a wider font face. It does not establish 200% text, full browser zoom or arbitrary accessibility text settings.
- Physical acceptance remains required on both Pi test Wi-Fi and router/management network, including independent recovery, reconnect after Wi-Fi changes and affected/protected clients. Synthetic fixture evidence cannot establish real network shaping, protection, shared limits or reconnection.
- This lane's extra help diagnostic samples the first profile help control only. The failed full module's remaining page/font states were not rerun, and no general help-icons PASS is claimed.
- Other dialog types, especially import, speed-test and connection-check dialogs, were not newly audited here.

No pending or denied action remains in this lane. Initial sandbox restriction was resolved by approved escalation limited to cached Chromium and synthetic localhost. The remaining limits are explicit acceptance work, not evidence of a newly reproduced production failure.
