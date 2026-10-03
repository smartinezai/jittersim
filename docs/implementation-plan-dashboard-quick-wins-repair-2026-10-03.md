# Complete the dashboard quick wins

The goal is to finish PR 73 on the tested integration foundation. Before sign-in the page, browser title, icon and errors must use neutral wording. The product name arrives only through an authenticated route. Complete the existing wording, notification and token-duration changes without changing network behavior. The main risks are stale authentication replies, longer credential retention and losing recent confirmation or keyboard fixes during integration. The human has approved proceeding without another feedback pause through 4 October at 06:51 UTC.

## Scope and decisions

- Start from integration merge 0c8abd4b and apply the two unfinished dashboard commits 2f802a6 and 8c9b435 in an isolated branch. Preserve every merged network, confirmation and focus repair.
- Review all 33 changed files. Public branding is presentation privacy rather than a substitute for authentication. Private branding must not survive token rejection or a stale request.
- Keep the existing default of 24 hours for remembered tokens. Longer choices must be explicit, clearly explain shared-browser access and be bounded. Router credentials retain their existing duration.
- Keep one accessible action notification. Preserve errors and recovery guidance while avoiding duplicate announcements. Respect reduced motion.
- Preserve profile identifiers in API values and saved data. Names may replace identifiers only in displayed text. Avoid repeated full-list scans when rendering menus.
- Add focused regressions for demonstrated bugs. Review token rejection, token edits during a delayed reply, sign-out, private dashboard access and storage errors.

## Review and verification

1. Obtain independent review of this plan and the completed patch from the existing GUI reviewer. The signing owner has confirmed they are not editing PR 73.
2. Inspect the full change and resolve integration conflicts explicitly. Run affected unit tests, all unit tests and configured Ruff checks from the current source.
3. Run the ordinary browser modules and the dashboard visual and accessibility checks on desktop and phone. Inspect neutral sign-in, accepted token, rejected token, logout, remembered-token expiry and notification focus.
4. Build the current wheel in a clean Linux environment. Run configured workflow checks locally if GitHub account limits prevent execution. Do not describe unrun hosted checks as passing.
5. Publish a focused repair PR against integration after local verification and independent patch review. Verify its exact head and current base before merge. Supersede PR 73 only after its full intended scope is accounted for.

## Deployment and recovery

This change adds no migrations or external services. Existing token storage formats must remain compatible. Reverting the release restores the former dashboard without changing saved profiles or network settings. The combined main and Pi release remains with the coordinator and requires the separate update, signing, command and trusted HTTPS repairs plus a reachable management or recovery route. No radio or live Pi writes are part of this task.
