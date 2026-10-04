# Clear private results and repair phone controls

The dashboard keeps old diagnostics after enrollment is removed. Its small phone
layout can also push all device content below the pinned controls and some help
buttons miss taps inside their advertised target. The sign-in Remember choice
is incorrectly kept as an unfinished form after a successful access-rule save. This work repairs those observed failures
without changing the dashboard's design or relaxing its browser checks.

The main risks are old replies restoring private content, help targets covering
nearby fields and clearing edits made while a save is still running. Each repair
will use the existing ownership checks and preserve unrelated unfinished edits.
The reproduced warning belongs to the credential form rather than the saved
access-rule form. Credential choices must stay outside settings drafts.

## Scope and decisions

- Start from `ad66be96a14aab658faa834b8e3cb1b3c24da017` in the separate
  `codex/dashboard-private-markup-cleanup` branch.
- Clear diagnostics, action history and private website/device results when
  dashboard access locks. Preserve the request generation and token checks so
  replies started before the lock cannot restore them.
- Measure the 320 by 568 layout with normal and wider fonts. Reduce unnecessary
  space before the first device card while keeping readable text, navigation,
  all controls and the current dark theme.
- Keep the invisible help target inside the viewport when its circle is scrolled
  into view. The reproduced missed point was below the window. Preserve actual
  hit checks rather than relying on the target's declared size. Keep its small visible circle,
  keyboard focus and the tooltip's bounds. Check nearby inputs remain usable.
- Exclude the credential form from settings drafts. Keep the existing save
  version check, failed saves and edits made during a save.
- Keep server, network, enrollment and API behavior unchanged. The coordinator
  owns the separate general-profile repair and all release operations.

## Verification

1. Preserve failed runs of the current installed package. Record source identity,
   test identity, the remaining private markup and actual layout or hit results.
2. Review this plan before editing application source.
3. Add focused regression coverage only for state changes that can lose private
   data or edits. Use existing browser checks for layout and help geometry.
4. Build a fresh wheel. Compare all packaged application files with this working
   tree and run complete `dashboard_access`, `phone_layout`, `help_icons` and
   `dns_failure` modules against that installed package.
5. Run the relevant authentication, polling, request and draft tests then Ruff.
   Inspect desktop and phone screenshots in one batch and confirm the repairs.
6. Review the complete diff for late replies, bounded work, neighboring controls
   and unwanted data retention. Commit focused changes locally and send the
   coordinator exact commits and proof files.
7. After all production repairs are combined, the coordinator runs the full
   local workflow checks on each supported Python version and clean Linux
   containers. Old application test receipts do not establish the new package.

## Delivery and recovery

No build dependencies, migrations, stored settings or deployment configuration
change. Private results are already read on demand. Clearing their DOM requires
no additional requests or storage. A rollback reverts the focused repair commits
and rebuilds the package through the existing deployment process.

The coordinator owns the final push, hosted billing diagnosis, merge and safe Pi
deployment. This branch does not push or change the live network.
