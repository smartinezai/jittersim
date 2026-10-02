# Online release source (design note, 2026-10-03)

Status: designed, not built. It builds on signed releases (`feat/signed-releases`). Build it in a
branch of its own after that one is merged.

## What

**Check for updates** in the dashboard and `jittersim update check --online` find the newest
release on GitHub and stage it without the Mac. The dashboard then shows
`Update available: <release>` and the owner presses **Install**. An optional setting
**Install automatically** stays off by default.

## How

- **Token.** The repository is private so the Pi needs a read-only fine-grained GitHub token
  (this one repository, Contents: read). The owner creates it and installs it with
  `sudo jittersim update set-token`, which reads it from standard input (never from an
  argument), checks its shape and writes `/etc/jittersim/update-token` (root, mode 0600, atomic
  write). `clear-token` removes it. The token is never logged, never printed and never sent to
  the dashboard. The status only says "token: set" or "token: not set". Rollbacks already keep
  the token of now (`update-token` is in `PRESERVED_CONFIG`).
- **Who fetches.** The root update manager does, not the dashboard process. The dashboard asks
  the helper for an online check. The helper starts a one-shot root unit
  (`jittersim-update-fetch.service`) so the dashboard never handles the token or the download.
- **Fetching.** `GET https://api.github.com/repos/smartinezai/jittersim/releases/latest` with the
  token and verified HTTPS (system CA store, hostname check). It needs exactly two assets:
  `jittersim-release.tar.gz` and `jittersim-release.tar.gz.sig`. Any other name or a missing one is
  refused. Asset downloads use the API asset URL with `Accept: application/octet-stream`. GitHub
  answers with a redirect to its asset host. Follow at most two redirects and only to `https`
  hosts on a short allow list (`objects.githubusercontent.com`,
  `release-assets.githubusercontent.com`). Drop the `Authorization` header on every redirect.
  Limits: 64 KiB for the API answer, 25 MiB for the archive, 16 KiB for the signature, 30
  seconds connect and read timeouts and 5 minutes in total.
- **Staging.** The downloads go to a root-only folder and then through the same
  `stage_release` as today: signature of a trusted key, newer sequence, archive checks. A
  compromised GitHub account or a leaked token can publish a release but can never install one
  because only an archive signed by the owner's key is staged. The staged record notes
  `source: github` and the release tag for the dashboard.
- **Publishing.** `deploy/raspberry-pi/publish-release.sh REF` on the Mac builds and signs the
  release with `build-release.sh` and runs
  `gh release create TAG jittersim-release.tar.gz jittersim-release.tar.gz.sig`. `--dry-run`
  prints the commands and runs nothing. Test it with a fake `gh` on `PATH`.
- **Install automatically.** When on, the fetch unit queues the install after staging. It
  uses the same checks and recovery copy as a manual install.

## One-time owner steps

1. On GitHub: Settings, Developer settings, Fine-grained tokens, Generate. Repository access:
   only `smartinezai/jittersim`. Permissions: Contents read-only. Set an expiry date.
2. On the Pi: `sudo jittersim update set-token` and paste the token, then press Ctrl-D.
3. Signed releases must be set up already (`make-release-key.sh` and `trust-key`).
4. On the Mac for each release: `deploy/raspberry-pi/publish-release.sh REF`.

## Risks

- **Token leak.** Gives read access to the private code. It cannot install anything. Keep it
  read-only for one repository with an expiry and replace it with `set-token`.
- **Signing key plus GitHub write access.** Together they can publish a release the Pi stages
  on its own. With **Install automatically** on this becomes root on the Pi without anyone
  pressing a button, which is why it stays off by default.
- **Redirect handling.** Sending the token to the asset host or to any other host would leak
  it. Tests need a local TLS server that redirects to a second host and checks the header is
  gone.
- **Clock and GitHub outages.** The check fails with a plain message and changes nothing.
- **Tests.** A fake GitHub server over TLS in the test (redirects, size limits, bad signature,
  wrong asset names, a token that never appears in logs or errors) plus mutation tests such
  as "Authorization sent on redirect" and "token logged".
