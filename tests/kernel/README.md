# Kernel tests

Tests here run real `tc`, `nft`, Unbound and friends inside network namespaces.
They are skipped unless `JITTERSIM_KERNEL_TESTS=1` is set on Linux as root, so a
normal `pytest` run on a Mac skips them.

- `run-in-docker.sh`: the datapath and access tests (`test_datapath.py`,
  `test_access.py`) plus the smoke check, in one privileged container.
- `test_router_wire_guard.py` (part of `run-in-docker.sh`): a capture on the
  router's port that fails on any frame the Pi must never send toward the
  router (foreign MACs, damaged headers, test Wi-Fi source addresses, DHCP or
  IPv6 router answers, reflected mDNS/SSDP/NetBIOS, ARP for the Wi-Fi address,
  redirects, bursts), across every profile type, Panic, Reset and a Wi-Fi
  restart. See `docs/router-safety-audit-round2-2026-10-17.md`.
- `run-outage-lab.sh [output-dir] [unittest selection]`: the Pi-only cutover lab
  (`test_outage_lab.py`, built on `outage_lab.py`). About 15 minutes. It also
  needs `JITTERSIM_OUTAGE_LAB=1`, which only its image (`Dockerfile.outage-lab`,
  tag `jittersim-lab-outage`) sets, because it installs a DHCP client hook in
  `/etc/dhcp`. Results land in `output-dir/results.json`, logs and packet
  captures per scenario in `output-dir/logs/`.

## The outage lab

```
INET (fake internet: dnsmasq DNS, http.server)
  |
ROUTER (bridge br-lan 192.168.0.1, dnsmasq DHCP+DNS, nft masquerade, conntrack log)
  |-- HOUSE (wired household device)
  |-- PI eth0 (dhclient lease)      PI wlan0 192.168.50.1 (NM shared-mode dnsmasq + nft table,
  |                                  JitterSim control firewall, applier, access manager, Unbound)
  |-- AIR br-rwifi (router radio)   AIR br-piap (Pi radio)
                     \               /
                      CLIENT, OTHER_CLIENT (dhclient; "roam" = move the station port)
```

Mature tools do the work and only the glue is new: dnsmasq (what NetworkManager
runs for shared mode, with NetworkManager's exact command line, and what many
home routers run), ISC dhclient (a stock DHCP client with INIT-REBOOT, renew,
rebind and expiry), tcpdump plus scapy for captures, nftables and conntrack (the
Pi's own datapath), hostapd in controller-only mode, Unbound, Python's
http.server. NetworkManager itself is not run: its effects on wlan0 (address,
dnsmasq, `nm-shared-wlan0` table) are applied directly, from its source.

Run one scenario:

```sh
tests/kernel/run-outage-lab.sh "$PWD/outage-lab-output" tests.kernel.test_outage_lab.StaleLeaseTests
```

Tests marked `expectedFailure` document defects in Panic-gated files; they must
start passing (and lose the marker) when those defects are fixed.
The findings are in `docs/outage-lab-results-2026-10-09.md`.
