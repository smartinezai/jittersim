# Accurate discovery block-list wording

The discovery API reports exact global block-list membership. It does not report whether each observed DNS query was blocked or whether a website request succeeded. Replace outcome-sounding discovery badges with words that describe membership. Keep all observation counts, policy actions and API fields unchanged. This is a small display correction with low household risk.

Use “On global block list” for a listed name. Use “All on global block list” or “N on global block list” for group summaries. Update the matching Python display helper so the dashboard and command-line grouping remain consistent. Update the discovery help text to distinguish stored membership from a query outcome and DNS observation from website success.

Paths proposed to the coordinator are dashboard.js, dashboard.html, dashboard_rules.py, the row label in remote_cli.py and their existing grouping, block-state, subdomain, parity and command-line display tests. Do not change networking code, storage, access policy, authentication, migrations or deployment. No new data is collected or saved. The build already hashes served inline assets. Reverting the commit restores the earlier text without changing settings.

The coordinator approved the exact display paths. Independent plan review found no blocking concerns. Browser jobs remain held for the reserved hardware-study quiet window. After editing, run gating lint and relevant unit tests. Use the shared heavy-test wrapper and the coordinator's approved browser slot for focused rendering and GUI checks on the exact final clean head. Inspect desktop and narrow-screen evidence in a bounded pass. Review the final diff independently before normal push and one draft PR. Leave PR229 untouched.

Base: origin/main fadaa9d983c393e54c1e09a8dc489eb892fea163 freshly fetched and verified remotely. No current new-since-view marker was found in the renderer. That is a separate slice and is deliberately left out to keep this correction small.

The first focused browser run demonstrated horizontal overflow at 320px from the longer group badge. Allow only badges inside observation-group-meta to wrap, retaining every other style. Add an explicit check that the badge does not overlap the query count. Repeat the same exact-head checks after this correction.
