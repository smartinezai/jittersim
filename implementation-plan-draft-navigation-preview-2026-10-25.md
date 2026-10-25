# Keep navigation write checks focused on mutations

The navigation recorder currently treats the read-only profile range preview POST as a saved change. Exempt only POST requests whose parsed URL path is exactly `/api/profiles/ranges`. Preserve every existing no-write assertion and record all other non-GET methods. No dashboard or server change is needed.

1. Reproduce the recorder classification with fake page/request objects against the actual browser check. Confirm preview POST is currently recorded alongside a genuine mutation.
2. Add the exact method/path condition in `tests/browser/draft_navigation.py`. Check genuine profile saves, misleading suffixes, other methods and other paths remain recorded.
3. Freeze the local change for independent review. After review and coordinator confirmation that all four existing shards are free, run only the affected navigation browser module through the shared native wrapper slot.

Base is `0b8cbf9341a7b51497882686bd41b47ccbeade79`. This is the explicitly assigned harness repair. No runtime source, remote operation, sleeps, forced clicks or general POST exemption. Rollback reverts the one recorder condition.
