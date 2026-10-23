# Remove legacy code the installed Pi no longer needs (2026-10-23)

Scope: item 4 of [review-overengineering-backend.md](review-overengineering-backend.md)
(migration and legacy code), plus anything else that is provably unused. Base: main `03fe31d8`.

## Rule used

The Pi runs `0.2.0.dev+4b343b6` (6 October, see section 0 of
[release-runbook-2026-10-16.md](release-runbook-2026-10-16.md)). Its update manager cannot install
main (Panic hash list), so the next install is the root installer `install.sh`. After that the
new manager may roll back to any retained generation, and recovery reads the retained folders.

A candidate is removed only when the code can be shown to do nothing on every path: the Pi at
4b343b6, an `install.sh` upgrade from any older build, a rollback to 4b343b6 or older, and
recovery. Anything whose need depends on files on the Pi (which this work could not read: no
Pi access) is kept.

## Decisions

| Candidate | Decision | Proof |
| --- | --- | --- |
| `retire-services.py` retirement loop (`OBSOLETE_SERVICES = ()`, `retire_services`, `clear_failure`) | Remove. Keep the update-idle check as `check-update-idle.py` | The list held four units only on the Pi line `58e38252` and `c38de994` (30 Sep). Merge `dcebfb51` (2 Oct) emptied it, and it has been `()` in every build since, 4b343b6 included (`git show 4b343b6:deploy/raspberry-pi/retire-services.py`). With an empty tuple the loop does nothing, so removing it changes no behavior on any path. Nothing reads its backup folder `/var/lib/jittersim-retired-services` (`git grep` finds only the script), so rollback and recovery never used it. The 4b343b6 manager does not call the script: only `install.sh` of the same package does. The second call in `install.sh` only ran the idle check again and `daemon-reload`, which `install.sh` runs later anyway. |
| `upgrade-notice.py` | Remove | It only prints a count when the database lacks `domain_observations.mac_address`. That column came in `0890d775` (1 Oct), an ancestor of 4b343b6, so on the Pi it always prints nothing. The clearing itself stays in `storage.py`, and the dashboard still says when an upgrade cleared the list. Only effect: an `install.sh` upgrade from a build older than `0890d775` with protected devices no longer prints the count first. |
| Legacy certificate adoption (`updates.py` `_adoption_*`, `publish_certificate_adoption`, `recover_incomplete_certificate_adoption`, callers in `localca.py`) | Keep | Present at 4b343b6. Whether it is needed depends on the Pi's live identity (`legacy` or `ca`) and on any `.certificate-adoption` folder, which could not be read. It is also crash recovery on the safety-critical update path. |
| Legacy `backup` recovery generation (`updates.py`, `recovery_backup`, `cleanup_generations`, rollback) | Keep | 4b343b6 still accepts `root / "backup"` as a generation. Whether `/var/lib/jittersim-updates/backup` still exists on the Pi is unknown, and removing the reader would make rollback refuse or skip it. |
| `localname.py` `_legacy_name`, `LEGACY_LOCAL_NAME` | Keep | Live behavior, not migration: `pi.home.arpa` and an earlier saved name stay DNS and certificate aliases on every build, so old bookmarks keep working. Also runs inside `updates.identity_modules`. |
| `homepage-profile-compat.py`, `homepage-compat.conf`, `updates.py` install of both | Keep | It protects a rollback to a build before `a80dd59a` (3 Oct) that cannot read `homepage_bounce_percent`. 4b343b6 can, but older retained generations may exist on the Pi. The drop-in also persists in `/etc/systemd/system/jittersim-dashboard.service.d/`: it runs the script before the dashboard starts, so removing the script without a cleanup step would stop the dashboard. |
| `install.sh` `previous_access_release` and `archive_stale_access_state` | Keep | Also hit by a fresh install on a card with a left-over `/var/lib/jittersim-access`, so it is reachable without an old build. Security relevant. |
| `panic_guard.py` and `updates.py` `PANIC_LEGACY_PATH` upgrade code | Out of scope (pinned) | Listed only. Needs a Panic review and a new approved hash. |
| "Pi line" migrations in `storage.py` | Not touched | Other agents edit `storage.py` now. Same question as above: they run once on an old database and are cheap. |

## Older builds no longer supported

None. Both removals were no-ops on every build since 2 October. An `install.sh` upgrade from a
build before 1 October only loses the printed count of DNS names it is about to clear.

## Lines

Net 218 lines removed outside docs (two scripts and their tests: 28 added, 246 removed).
