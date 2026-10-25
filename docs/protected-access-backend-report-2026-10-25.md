# Protected resolver backend hooks

Apply the separately supplied two backend hooks to current base b061d2d8.
access_settings now includes saved protected devices, so content-based access
plan caching observes their changes. Address-move responses refresh the
household resolver before the existing ad-blocking/network response path. A
failed resolver refresh keeps the saved fields and reports live_applied false.

The proposed patch's storage context predates access_plan and did not apply
verbatim. The one-line field addition was placed in the current access_settings
method. The server changes match the supplied patch. No engine, networking,
pinned source, helper, installer or service file was changed.

Three new focused tests cover access-settings inclusion/cache refresh, ordering
of successful network updates and preservation of saved-but-unconfirmed fields.
The direct native Python 3.12 run passed 132 tests across the new module,
access_plan_cache, api_protected_without_mac, protected_dashboard_access and
integration/test_device_management. The first sandboxed attempt was blocked by
loopback listener permissions. The explicit loopback-capable run passed with
fake helpers and temporary databases. Gating Ruff, full Ruff on the new test
module and git diff --check passed.

Use a private copy of cached Python 3.12 dependencies for the exact-head native
wrapper receipt. The wrapper installs the current package as a wheel without
network access. Its results and command are retained in
`test-results/protected-backend-hooks/`. The composition with protected resolver
head 82cf49e5 is tested in a separate isolated worktree, leaving both writers'
source checkouts unchanged.

The actual DNS outcome producer remains a separate prototype. These hooks do
not introduce counters or claim a resolver event source. No remote operation,
real device contact, installation, signing or publication is included.
