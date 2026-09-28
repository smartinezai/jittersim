# DNS failures and headless operation

## Summary for review

The first implementation adds a 0–100% chance of a quick DNS connection error
on the test Wi-Fi. It keeps the existing Unbound per-device rules and leaves
management DNS alone. The dashboard already has a staged-release check so the
terminal now gets matching status, install and recovery commands. Terminal
diagnostics and logs are fixed to known services and bounded in size.

The dashboard's Setup button labelled **Check for updates** checks for a
verified release archive staged on the Pi, then offers install and rollback.
It does not look up a newer release online.

The main risk is making test devices appear offline. At a nonzero setting a
random network rejection can replace the usual blocked-name answer with a
connection error. It cannot make a blocked name resolve. The setting starts
at 0% and must be explicitly applied. Home-page bounce and random destinations
remain a later browser feature.

Decisions for this version:

- One chance applies to every new DNS request from the test Wi-Fi. Device and
  browser caches can make the per-page effect differ from the displayed chance.
- Use nftables' random number expression in the existing input firewall and
  send a prompt ICMP unreachable for UDP or TCP reset for TCP. This avoids a
  new resolver or optional Unbound module and keeps the source address intact
  for requests that reach Unbound.
- The current dashboard update button checks for a verified bundle staged on
  the Pi. Keep that flow and add the same staged-release operations to the CLI.
  Do not query the private GitHub repository from the Pi without a trusted
  release-information source.
- The first CLI slice covers access status and apply, DNS chance, diagnostics,
  capped service logs and confirmed staged install or rollback.

The home-page bounce and random-page behavior remain a later browser feature.
DNS cannot change a URL path or read the browser's history.

## Learning objective

Trace a DNS request from a test device through JitterSim's firewall and
filtered resolver, then decide where a controlled failure belongs without
weakening the existing site rules. The updater and terminal interface are
supporting work. They should reuse JitterSim's existing safe update and helper
paths rather than create another way to change the Pi.

## Current evidence

- The current base is `origin/main` at `b986890` from 26 September 2026.
- `src/jittersim/networking/access.py` builds per-client Unbound views for
  allowed and blocked sites. Test-network DNS is redirected to that resolver.
- `AccessOptions` is stored in the existing access settings. Its serialized
  form also appears in the helper policy. A new setting needs a safe default
  when old settings are loaded.
- `jittersim-dns.service` runs Unbound. The access manager checks the new
  resolver configuration and restores the old policy if applying new access
  rules fails.
- `src/jittersim/api/workflows.js` already provides **Check for updates**,
  **Install staged release** and **Restore previous installation**. The check
  reads `/api/updates`, which reports only a release already staged on the Pi.
- `src/jittersim/updates.py` checks the staged archive checksum, rejects unsafe
  archive contents, saves the previous installation and settings, checks
  service health and restores the previous copy if installation fails. The
  deployment guide says an administrator transfers a release over SSH and
  GitHub credentials are not stored on the Pi.
- `src/jittersim/cli.py` now adds access status/apply, DNS chance settings,
  diagnostics, capped logs and the existing staged update and rollback actions.

## Proposed scope

### 1. Add a safe DNS failure setting

- Add an integer percentage from 0 to 100 with 0 as the default for old and new
  installations.
- Keep the setting in the existing saved access policy so it follows the
  current save, Apply, backup, export and restore behavior.
- Affect requests arriving from the test network only. Keep the Pi's management
  resolver outside this setting.
- The firewall rejection happens before Unbound evaluates the name. At a
  nonzero chance it can hide the usual blocked-name response, but it never
  returns an allowed answer. Existing blocked names remain blocked at 0%.
- Do not save full hostnames, page paths or browser history for this feature.
- Use a bounded request path with finite resolver timeouts. Do not add an
  unbounded queue or wait for a client retry before reporting the simulated
  failure.
- Use nftables' random expression at the firewall boundary. Check the rendered
  rules before applying them and keep UDP and TCP paths separate.

Requests that are not rejected still reach Unbound with the test device's
original source address, so its per-device views remain in use. No proxy,
optional Unbound module or extra package is added. The generated firewall
handles both UDP and TCP with prompt errors instead of waiting for timeouts.

Define the percentage as a chance per fresh DNS request in the first version.
Document that operating-system and browser DNS caches mean it is not the same
as a chance per page visit. Some apps may use a VPN, encrypted DNS or a direct
address and avoid the test-network DNS service. Preserve the existing warning
about those limits.

### 2. Expose the setting in the dashboard

- Add the percentage to the existing access-rules area with clear wording that
  it affects all test-network devices.
- Validate the range in the API, saved settings and helper policy. Do not rely
  on the browser control for validation.
- Keep the existing Save and Apply flow. Show whether the value is saved and
  whether the resolver accepted it.
- Report the setting in status without exposing client browsing details.
- Keep the visible default at 0% and make the effect of 100% clear before
  applying it.

### 3. Extend the terminal operations

Keep the existing commands working. Add a small set of commands that use the
same saved settings and privileged helper as the dashboard:

- Show current network, access and DNS-failure state.
- Show bounded logs for a fixed list of JitterSim services.
- Print a bounded diagnostic summary that omits admin tokens, Wi-Fi passwords,
  certificate keys and browsing details.
- Read and change the DNS failure percentage. Save it separately from Apply as
  the dashboard does and apply it through the normal access-policy path.
- Check the approved staged release, install it only after a clear terminal
  confirmation and offer the existing rollback operation.

Do not make the command run arbitrary shell text or accept arbitrary service
names. Keep log line counts capped and service names on an allowlist. Decide
whether scripting support such as JSON output is needed after the basic
commands are clear. Full device and profile editing is outside the proposed
first CLI slice.

### 4. Resolve online release discovery

Do not add a duplicate dashboard update button. The current Setup button checks
the staged release and already supports confirmed install, progress, health
checks and rollback. It does not check whether a newer online release exists.
Confirm whether the existing staged-release check is enough or whether this
work must detect a newer release before staging.

If online discovery is needed, first select a trusted source that can serve
release information for the private project without placing a GitHub credential
on the Pi. Keep checking release information separate from approval,
downloading and installing code. Do not fetch and run the current `main` branch.

The terminal `update check`, `update install` and `update rollback` operations
should call the same helper and staged-release functions as the dashboard. Do
not implement a second installer or pull `main` directly on the Pi.

### 5. Record the later navigation behavior

Add a follow-up roadmap item for optional home-page bounce and configured
random destinations. Those behaviors need a browser-level component. A browser
proxy handling HTTPS CONNECT requests knows the hostname but not the encrypted
page path or browser history. A future design must decide how the browser can
change destinations while keeping HTTPS identity checks intact. Do not add TLS
interception or collect browsing history as part of this work.

## Work sequence

1. Add the saved setting with a 0% compatibility default. Add the failure
   behavior behind the existing test-network DNS path.
2. Add dashboard editing and saved-versus-applied status.
3. Add terminal status, bounded logs and diagnostics, DNS setting controls and
   staged update commands.
4. Keep online release discovery as a follow-up until a trusted private
   release-information source is selected.
5. Update the roadmap and deployment guide with the limits and headless
   recovery commands.
6. Confirm the `jittersim-dns` activation path on the Pi. The last recorded
   hardware check found the unit installed but inactive. Verify which access
   startup action enables it and confirm its health after boot and policy
   changes.
7. Review the complete change, build a release archive and prepare Pi
   acceptance steps. Do not install it on the live Pi or push it as part of
   this implementation without separate approval.

## Verification strategy

### Local behavior

- Check the percentage boundaries, the 0% default and invalid values in the
  saved-settings and helper-policy paths.
- Check normal resolver answers, injected failures and existing blocked or
  allow-only site behavior for both UDP and TCP. The injected firewall error
  may replace the usual blocked-name response while still denying the name.
- Check cached results, client retries and simultaneous lookups so the shown
  percentage has understandable behavior and does not stall the resolver.
- Check old database files, settings export and restore so the new field
  defaults safely and survives normal recovery.
- Check dashboard save, Apply, error and reload behavior.
- Check terminal help, status, DNS changes, update confirmation, rollback and
  bounded log output, including missing services and helper failures.
- Check that diagnostics never print credentials, private keys or browsing
  history.
- Check that staged update status remains read-only and that install and
  rollback still use the existing recovery service. If online discovery is
  approved, check unavailable network, invalid release information, an
  unchanged release and a newer release without installing it.

### Deployment and Pi acceptance

- Check the installer and update archive with the new code and any changed
  service definitions.
- Check a clean container or CI build so the release does not depend on a
  warmed developer environment.
- On a dedicated test client, try 0%, an intermediate percentage and 100%.
  Confirm normal DNS resumes when set back to 0% and after a resolver restart.
- Confirm the management connection, router-facing DNS and non-test household
  devices remain usable during all tests.
- Confirm a failed resolver apply restores the old policy and a failed release
  install restores the previous installation and settings.
- Confirm Unbound still receives the correct client identity and returns each
  device's own saved site policy after the failure layer is enabled.
- Re-run the existing emergency reset and normal-access paths after a failed
  apply.

Automated tests do not prove real client caching behavior or Pi service
recovery. Record those as hardware acceptance checks.

## Security, scale and recovery

- Classify this change as high network risk because it changes client DNS and
  can make the test network appear offline.
- Keep the failure setting disabled by default and restrict it to the test
  network. Preserve management and household network routes.
- Keep DNS processing bounded by the existing service capacity. Add limits for
  any new client queue, request size and time spent waiting for upstream DNS.
- Do not write query names or page paths to logs. Keep only bounded aggregate
  failure counts if the count helps the owner understand the effect.
- Keep state changes behind dashboard authentication and validated local
  helper actions. The command-line interface must use the same checks.
- Preserve the existing access-policy rollback and release recovery paths.
  Old databases and release archives must continue to load with failure
  injection disabled.
- Require review from someone familiar with the DNS path and a Pi operator
  before live acceptance. Code completion does not mean the feature passed
  hardware acceptance.
