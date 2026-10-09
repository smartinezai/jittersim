# Design note: per-caller authorization for the helper socket (2026-10-09)

Status: proposal only. Nothing here is implemented. Source: finding 5 of the source gap audit of 2026-10-09.

## Problem

The root helper listens on `/run/jittersim/helper.sock` (mode `0660`, group `jittersim`, inside the `0750` runtime directory).
Every process in the `jittersim` group can call every helper action.
Only the second socket, `helper.sock.homepage`, restricts actions (`policy_only`: `homepage-snapshot` and `homepage-confirm`, 2 slots).

Callers of the main socket today:

| Caller | Unit and user | Untrusted input it parses | Actions it needs |
|---|---|---|---|
| Dashboard | `jittersim-dashboard`, `jittersim` | HTTPS from the test Wi-Fi and the LAN | almost all (apply, reset, normal-access, access, Wi-Fi, router, updates, dashboard-access checks) |
| Browser proxy | `jittersim-browser-proxy`, `jittersim` | CONNECT lines from the test Wi-Fi | `clients` |
| DNS watcher | `jittersim-service-watch`, `jittersim` | raw DNS frames from the test Wi-Fi | `service-addresses` (and the plan read it follows) |
| Root CLI | `jittersim`, `jittersim-dashboard-access`, run as root | owner input | `normal-access`, status reads, `dashboard-access local-*` |

So a code-execution bug in the proxy or the watcher, both of which parse traffic from the test Wi-Fi, gives full control of the network, updates and the dashboard device allowlist (`dashboard-access local-add`).

## Options

1. **Per-role sockets with action allowlists.** Reuse the existing `policy_only` pattern: `UnixHelperServer` already takes an allowlist flag and its own slot count. Add `helper.sock.proxy` (`clients` only) and `helper.sock.watch` (`service-addresses` only), each with its own small slot pool. Give each socket its own group (`jittersim-proxy`, `jittersim-watch`) so the proxy cannot open the main socket at all. Remove the proxy and watcher users from the `jittersim` group.
2. **`SO_PEERCRED` checks on the main socket.** Read the peer uid/gid/pid with `socket.getsockopt(SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))` in the request handler. Require `uid == 0` for `dashboard-access local-*`, `update-install` and `update-rollback` when they are not meant for the dashboard. This is cheap and needs no unit changes, but uid alone cannot tell the proxy from the dashboard, because both run as `jittersim`.
3. **systemd socket activation (`ListenStream=` + `SocketUser/SocketGroup/SocketMode`).** Lets systemd own each socket's permissions. It does not authorize actions by itself and the helper would need `sd_listen_fds` handling. Useful only together with option 1.
4. **Separate service users.** Run the proxy and the watcher as their own users (`jittersim-proxy`, `jittersim-watch`). Needed for options 1 and 2 to tell them apart by credentials.

## Recommendation

Options 1 plus 4, with option 2 as a second check for the `local-*` operations:

- Per-role sockets reuse code that is already reviewed and in production (the `.homepage` socket), so no new mechanism is invented. Filesystem permissions plus a fixed action allowlist per socket is the standard Unix daemon pattern (Postfix, systemd-journald, Docker's socket).
- Separate slot pools also fix the slot-starvation half of finding 4: a busy proxy can no longer take the slots Panic needs.
- `SO_PEERCRED` with `uid == 0` closes `local-add` against every non-root caller, including the dashboard.

## Effects

- Dashboard: no change in behaviour. It keeps the main socket.
- Browser proxy and DNS watcher: new `--helper-socket` defaults, new users, and they lose access to the main socket.
- Deploy: new users and groups in `install.sh`, `User=`/`Group=` changes in `jittersim-browser-proxy.service` and `jittersim-service-watch.service`, `ReadWritePaths`/runtime directory permissions so the new users can reach only their sockets. The upgrade must create the users before the units restart, and rollback must keep working with an older helper that has only the main socket (keep the old socket path accepted for one release).
- `helper.py` is Panic-gated, so this needs a reviewed Panic bundle in `PANIC_APPROVED_SOURCES`.

## Tests

- Unit: each role socket refuses every action outside its allowlist and answers its own actions; slot exhaustion on the proxy socket does not refuse `normal-access` on the main socket.
- Unit: `dashboard-access local-*` refuses a peer whose `SO_PEERCRED` uid is not 0 (fake the credentials in the handler).
- Installer permission test (`tests/integration/linux_installer_permissions.py`): the proxy and watcher users cannot connect to the main socket; the dashboard user can.
- Kernel/Docker run: Panic succeeds while a client floods the proxy with CONNECT requests.
