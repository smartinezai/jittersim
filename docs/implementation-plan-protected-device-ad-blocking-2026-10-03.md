# Optional ad blocking for protected devices

## Human review summary

Add an optional **Block ads** switch for each device in **Unaffected by profiles**. The owner has chosen a separate switch rather than automatic blocking when protection is enabled. Existing devices start with ad blocking off. Turning it off must leave protection from profiles and quotas active.

The owner has also confirmed the priority: deliver effective ad blocking with as little monitoring as possible. Prefer filtering without saved browsing history. If effective blocking and the current absolute "never monitored" promise cannot both be kept, ad blocking takes priority for devices whose switch is on. This exception covers only the processing needed for blocking. It does not make protected devices subject to profiles, quotas or unrelated traffic monitoring.

The feature blocks known advertising domains during DNS lookups. DNS turns website names into internet addresses. Describe the benefit as fewer ads rather than an ad-free guarantee. Some ads share a domain with useful content. Encrypted DNS and VPNs can also bypass local filtering.

Use a separate Unbound listener alongside the existing shared-network DNS service. Reuse the household DNS rules from the current release. Keep one shared advertising list and preserve the household rules ahead of ad-blocking allowances. The managed browser proxy uses the same expiring preference so it also blocks selected devices while profiles are stopped.

The main risks are interrupted browsing when the blocker fails, filtering the wrong device after an address change and retaining more browsing information than blocking needs. The measured design restores ordinary DNS when its temporary routing permission expires after five seconds. No query logs or browsing history are saved. The monitoring explanation describes the domain checks required for optional blocking.

**Working risk: high** because DNS changes can interrupt internet access. No pull request template was found under `.github` in the inspected checkout. Technical plan review and human feedback are complete. Implementation is authorized. The release requires complete local checks, final source review and a separate feature pull request.

The technical plan review and human feedback are complete. An initial disposable proof verified packet-by-packet DNS port rewriting and five-second routing expiry. Adaptation to the newer release selected its existing Unbound software to preserve household rules. The fixed StevenBlack hosts source supplies the blocklist. Reset and Panic pause blocking while keeping saved switches. The first version covers protected devices on JitterSim Wi-Fi only.

## Confirmed choice and proposed behavior

- Confirmed: the owner can enable or disable ad blocking separately for each protected device.
- Confirmed: ad blocking takes priority over the absolute no-monitoring promise if a real conflict remains. First attempt to provide both effective blocking and no saved browsing history.
- Proposed: existing and newly protected devices default to blocking off. Offline entries retain their saved preference.
- Turning blocking off does not remove protection or reactivate profiles. Blocking does not grant dashboard or SSH access.
- Protection continues to exclude traffic from profile limits and shared quotas. Ordinary traffic discovery remains off for protected devices. Enabling blocking permits the DNS processing needed to filter that device. Existing global website and device access rules retain their current meaning.
- Proposed: removing protection also removes that device's blocking preference. Adding protection again starts with blocking off. Explain this beside the removal action.
- Show the saved choice separately from whether blocking is working. If applying a change fails, report the saved choice and the failure clearly.
- Provide a quick off switch and a small owner-managed list of allowed domains for sites broken by filtering. The allowance applies only to ad blocking and cannot override an existing global access rule.
- The first version does not force devices to abandon encrypted DNS or VPNs. Explain those coverage limits in the feature help.

## Current source points

- `src/jittersim/config/exemptions.py` defines protected device identity and the existing limit of 256 devices.
- `src/jittersim/config/storage.py` owns protected-device storage, database migrations, configuration versions and backups.
- `src/jittersim/api/server.py` owns the protected-device controls. Protection changes run within the existing control operation and database transaction.
- `src/jittersim/config/protection.py` identifies protected observations. `README.md` documents that protected devices are never monitored.
- `src/jittersim/networking/renderer.py` returns protected traffic before profile rules run. Ad blocking must not weaken that protection.
- `src/jittersim/networking/browser_proxy.py` resolves browser destinations through the Pi's resolver. Those requests require a separate coverage decision.
- `deploy/raspberry-pi/install.sh` configures shared IPv4 access at `192.168.50.1` and disables IPv6 on the access point. It installs `dnsmasq-base`.
- `deploy/raspberry-pi/jittersim-control-firewall.nft` owns management access restrictions. `retire-services.py` retires the old `jittersim-dns.service` so that name must not be reused accidentally.
- `.github/workflows/wifi-recovery.yml` and `tests/wifi/Dockerfile` provide existing Python, browser and Linux network checks.

These are observations from the local checkout on 3 October 2026. No live Pi configuration was inspected or changed for this proposal.

## Internal technical plan review: 3 October 2026

This is a review of the proposed design and current local source. This section records the initial review before implementation. Its recovery and privacy requirements still apply. The final integration decision is recorded below.

1. **Preserve existing DNS.** The installer already installs dnsmasq and uses NetworkManager's shared access point. A normal Pi-hole installation could compete with that DNS listener or address allocation. First test a separate dnsmasq listener on an unused port with its own bounded configuration. Only enabled protected devices should be routed there. Do not change the shared resolver into a filtered upstream for everyone. Dnsmasq documents domain blocking and multiple listeners in its [manual](https://thekelleys.org.uk/dnsmasq/docs/dnsmasq-man.html).
2. **Test existing connections during recovery.** Removing a DNS redirect rule does not necessarily remove mappings already created for existing connections. Test repeated UDP lookups from the same client socket and persistent TCP DNS while killing or stalling the blocker and its controller. Do not promise a recovery time based only on a health-check interval or an expiring device set. Any cleanup must affect only this feature's DNS connections. The [nftables documentation](https://wiki.nftables.org/wiki-nftables/index.php/Performing_Network_Address_Translation_(NAT)) explains that established flows reuse their original mapping.
3. **Cover the browser proxy and stopped simulation.** The proxy uses the Pi's ordinary resolver. It currently looks up the client's MAC for protection only when an active plan exists. A separate ad-blocking preference therefore needs verified device identity even when profile simulation is stopped. Test both paths and agree on Panic or Reset behavior. Proposed safety behavior: Panic and Reset bypass ad blocking while keeping saved preferences. Start with saved settings restores those preferences. Explain this in the dashboard.
4. **Preserve settings through ordinary device edits.** The current protected-device save path updates the display name and has no blocking preference. Simply adding a defaulted flag to every save request risks switching blocking off during a name edit. Prefer separate bounded settings tied to protected-device identity. Test old databases, repeated installation, backup, protection removal, concurrent changes and rollback using the previous release.
5. **Verify privacy and update semantics.** Domain inspection is needed for filtering but query histories are not automatically needed. Try no query logging first. Keep the blocklist and proxy decisions consistent. Dnsmasq does not reread its main configuration on a reload signal so a successful signal alone is not proof that a changed list took effect. Test the chosen update mechanism with known domains. If Pi-hole is needed, verify that its chosen privacy mode retains the matching features used by the list.

Recommended sequence after human feedback: disposable network proof, update this plan with the measured design and limits, implement the saved preferences and controls, integrate the tested resolver then run the complete release checks. A failed proof should change the design before dashboard implementation begins.

## Design and review work

1. The internal technical review is recorded above. Human feedback on it is required before implementation begins under the supplied working agreement.
2. After review, use a disposable Linux environment to compare the blocker options. Prefer a separate local service that leaves NetworkManager in charge of Wi-Fi and address allocation. Prove that only selected protected clients reach the filtered resolver and that their identity is not lost when DNS is forwarded. A shared forwarder can make all requests appear to come from the Pi.
3. Verify ordinary UDP and TCP DNS. Keep the normal resolver independent of the blocker so recovery cannot create a forwarding loop. Check DNS caching when enabling and disabling blocking. Previously cached answers can delay a visible change.
4. Decide how the existing browser proxy honors the same preference. Verify that client identity reaches the filtering decision. Prefer no browsing logs. If additional monitoring is unavoidable for effective blocking, record why it is needed and define its minimum scope and lifetime. Do not claim full coverage if this path remains unfiltered.
5. Set explicit limits for list size, download size, request concurrency, DNS cache size, download time and retry count. Measure delay and memory use on the supported Pi before choosing defaults. Filtering must avoid a new database or external network request for every lookup.
6. Agree on a recovery time target. Test stopped services, stalled services, failed startup and unavailable upstream DNS. A visible warning alone is insufficient. Recovery must work if the dashboard process is unavailable. Repeated failure must not cause endless retries or service restart loops.

The central learning objective is deciding how per-device DNS filtering preserves identity and availability alongside existing profile protection while minimizing retained information. Packaging and dashboard wiring are supporting work.

## Implementation subtasks after review

1. Add bounded owner configuration keyed by the protected Wi-Fi MAC address. Use an automatic repeatable database migration that defaults existing entries to off. Preserve the choice across name edits. Include it in configuration backups and test older databases and release rollback compatibility.
2. Add protected admin controls for saving the preference and allowed domains. Reject unknown or unprotected device identities. Coordinate updates with the existing control operation so profile changes, protection removal and concurrent dashboard tabs cannot leave conflicting settings.
3. Add the selected DNS integration through the existing controlled networking boundary. Keep protection independent from profile start, reapply and reset operations. Agree on stop and reset behavior before implementing it.
4. Package the service and its configuration through the Pi installer. Installation and upgrades must be repeatable without manual commands per Pi. Handle boot ordering, Wi-Fi reconnection and service recovery. Do not expose an additional administration interface on test Wi-Fi or the public internet.
5. Add the per-device switch and clear service status in the protected list. Keep the same controls usable for offline saved devices. Add concise help about partial coverage and cached answers. Update the README and monitoring labels to distinguish profile protection from DNS processing for ad blocking. Claim that browsing history is not saved only after verifying it.
6. Add the approved blocklist update mechanism. Fetch with fixed limits and replace a validated list only after it is ready. Retain the last working list on download failure. Keep arbitrary remote list URLs outside the first version.

## Privacy and security

The blocker must inspect the requested domain in memory to make a decision. Inspecting a lookup and saving a browsing history are separate choices. The preferred design disables query histories, per-device query counts, long-term query storage and diagnostic packet logging. Check defaults, error messages, crash reports and any separate blocker database. Prefer showing service health and list age without traffic statistics.

Pi-hole documents controls for query logging, history storage and privacy. This makes filtering without saved browsing history a credible first option rather than a proven result for JitterSim. Its strongest privacy mode also limits some matching features. Test the selected combination against the approved blocklist and per-device behavior before choosing it.

If a real conflict remains, do not abandon effective ad blocking solely to preserve the previous monitoring wording. Use only the additional processing or records that the chosen blocker actually needs. Explain the necessity, data fields, storage location, access and deletion deadline in this plan before shipping. Keep unrelated JitterSim traffic discovery off. This preference does not justify collecting full network traffic or enabling every blocker statistic by default.

Prefer persisting only owner settings, approved blocklist data, allowed domains and necessary service state. Remove per-device settings when protection is removed. Limit caches and avoid attaching client histories to cached names. If records are unavoidable, bound their lifetime and include their removal in protection deletion and feature-disable behavior. Normal upstream DNS providers still receive forwarded ordinary lookups. Fetching a blocklist also contacts its publisher.

Use the existing admin authentication and saved device permissions. Restrict the resolver to intended local clients so it cannot become a public DNS server. Protect any service credentials. Domain allowances cannot broaden network access. Treat MAC addresses as the existing preference identity rather than proof of ownership. Test changed private MAC addresses, stale leases and address reuse without filtering a different device.

## Verification and release

Baseline on 3 October 2026: the existing storage, protection, device-management and browser-proxy tests passed with local loopback networking permitted. The initial restricted run could not open local test sockets. These checks cover the starting code and do not validate the proposed ad-blocking feature.

- Verify the same blocked test domain with three clients: protected with blocking on, protected with blocking off and an ordinary client. Only the first receives the blocking response. Verify an allowed domain on all three.
- Verify both direct browsing and the browser proxy. Use controlled test domains instead of assuming that a live website always serves an ad.
- Test allowance changes, cached answers, toggling while connected, protection removal, offline reconnect and reused addresses. Confirm that device naming does not reset the preference.
- Keep existing profile and quota protection tests passing. Test simultaneous general profiles, outages and quota changes. Protected traffic must remain excluded from those effects and unprotected profiles must retain their behavior. Monitoring tests must distinguish the selected blocking path from unrelated traffic discovery.
- Kill or stall the blocker and its controller. Verify recovery within the agreed bound with the dashboard stopped. Test boot, Pi reboot, access point restart and upstream DNS failure. Ordinary DNS must remain usable when the blocker is unavailable and the upstream is healthy.
- Inspect files and logs after protected browsing. The preferred result is no recorded requested domains or client query histories. If additional records prove necessary for effective blocking, verify that only the documented fields are saved and that limits and deletion deadlines are enforced. Verify that protected devices with blocking off still follow the existing monitoring exclusion.
- Use focused tests for migration, identity decisions, concurrent changes and failure recovery. Use existing integration and browser checks for simple wiring.
- Update the Linux test image only where necessary. Verify dependency installation and service behavior in a clean container and CI. A warmed developer environment is insufficient.
- On the Pi, compare normal lookup delay and memory use with representative concurrent clients and a bounded large list. Use management Ethernet or management Wi-Fi for these checks.
- Review networking, privacy, deployment and rollback before release. Publish the branch or deploy only when separately authorized.

Rollback must first restore ordinary DNS and remove only feature-owned routing. It must leave Wi-Fi, address allocation, protected-device settings and management access usable. Verify a rollback to the previous release against the migrated database before calling the change ready.

## Reference material

- [Pi-hole overview](https://docs.pi-hole.net/)
- [Per-client group examples](https://docs.pi-hole.net/group_management/example/)
- [Service and port requirements](https://docs.pi-hole.net/main/prerequisites/)
- [Privacy settings](https://docs.pi-hole.net/ftldns/privacylevels/)
- [Query logging and history configuration](https://docs.pi-hole.net/ftldns/configfile/)
- [Dnsmasq blocking and service configuration](https://thekelleys.org.uk/dnsmasq/docs/dnsmasq-man.html)
- [Existing connection mappings in nftables](https://wiki.nftables.org/wiki-nftables/index.php/Performing_Network_Address_Translation_(NAT))
- [Why domain filtering cannot reliably remove YouTube ads](https://discourse.pi-hole.net/t/youtube-blocking/81964)

## Status

The owner has authorized taking over the feature and approved the reviewed sequence. The optional switch and the preference for effective ad blocking over an absolute no-monitoring promise are confirmed. Implementation is proceeding in an isolated checkout and is being adapted to the reviewed release source before publication. A pull request and merge are authorized. If CI cannot run solely because of billing errors, the complete local checks may be used before merging.

## Implementation checkpoint

The separate Unbound service listens on port 5354. It uses the existing
household per-device rules before checking one shared advertising tree.
Allowed ad domains cannot bypass household denials or allow-only rules.
Household changes temporarily bypass ads until the new rules are installed.
The existing household DNS failure setting also applies to this listener.

Only selected source MAC addresses sending DNS to the Pi are routed to the
blocker. Routing permissions expire after five seconds and are refreshed
after a successful DNS probe. The managed browser proxy reads the same
expiring policy. Neither path records query history. Shared file locking,
settings generations and control revisions prevent a stale health check or
queued switch change from reversing Panic. Reset and Panic pause ads without
deleting switches. Explicit Start or enabling a switch can resume blocking.
Automatic profile refresh cannot resume it.

Domain downloads have fixed byte, domain, worker and retry limits. The
current public list was accepted with 72,233 domains in 2,220,527 bytes.
Downloads are limited to 8 MiB and 200,000 domains. One worker downloads a
fixed HTTPS source with no redirects. Three failed automatic attempts defer
further attempts for a day. The last validated list is retained on failure.
There are at most 256 allowed domains and 256 selected devices. Root state
files are limited to 128 KiB. DNS caches and concurrent work are bounded.
The advertising tree is stored once rather than copied into every view.

The database uses separate tables tied to protected identities. Names can
change without losing switches. Removing protection deletes the choice.
Moving a private address or undoing that move carries the current choice.
Database recovery copies retain preferences. The downloaded list is a
rebuildable cache outside the release recovery copy. Installer operations
are repeatable. The updater backs up and restores the new service unit and
continues to support a recovery copy from a release without ad blocking.

The original development baseline passed all 2,236 Python checks. The
adapted desktop and phone journey passed with keyboard controls and saved
choices. Focused checks cover the household rule order, bounded state,
concurrent Panic, protection removal and updater compatibility.

The initial dnsmasq proof passed its functional checks for UDP, TCP, record
types, changed MAC identity, allowances, pause, resume and removal. DNS death
and controller stall restored ordinary DNS on the same UDP socket. It found
no saved browsing history and confirmed a non-root DNS child. Its cleanup
returned a failure after namespace removal had already removed the virtual
interface. Cleanup is corrected. This historical proof is not a final
receipt for the adapted Unbound implementation.

The corrected native Unbound proof passed with exit zero. It checked UDP,
TCP, record types, current MAC isolation, allowances, pause, removal, DNS
death, controller stall, household precedence and absence of saved history.
Unbound reserves `.test` by default so the fixture uses controlled
`example.net` names without changing production resolver safeguards.
Full release checks and final source review remain release gates. The first adapted proof attempt could not run because
the prepared images lacked required network or DNS tools. The owned test
containers were removed. No unrelated containers were changed.

Publish a separate feature pull request after the reviewed base is on main.
If CI is blocked solely by billing, merge only after all local equivalents
pass. Do not bypass actual code or test failures. No live Pi deployment is
included until independent management recovery is verified. Actual Pi
memory, throughput and physical multi-client acceptance remain unverified.


## Final source review checkpoint

The helper and household rule consumers were reviewed together. Ad writes
carry the existing control revision. Requests queued before Panic cannot
restart blocking. A fresh explicit enable can resume it. Missing ad settings
after a latched Panic start paused when they are reconstructed. An access
transition clears ad routing while household rules change. It retains the
latest saved pause choice even if Panic arrives during that transition.

The updater still verifies exact reviewed source digests before migrating
persistent Panic state. The feature records its helper and access digests
alongside the original approved bundle. It does not accept arbitrary source,
release labels or observed candidates as proof of compatibility. The signed
release checks continue to cover the complete archive. Modified candidates,
missing legacy intent and incompatible recovery remain refused.

DNS validation failures wait at least five seconds before retrying. The
service needs CAP_KILL to stop its own child after Unbound drops root
identity. The bounded service permissions include that capability. Verify
the actual stop and restart under those permissions in the final Linux run.

Current focused evidence: 198 helper, Panic, ad state and dashboard checks
passed. Another 156 update and compatibility checks passed with one expected
platform skip. The first full diagnostic run found the retired button word,
a redundant capability and source digest updates. Those were corrected.
The final full run must use the complete corrected source.
