# Verify the older router form destination

The field guard prevents forwarding unknown actions but still trusts the
form destination. A recognized form can send Wi-Fi credentials to another
host or an unrelated router endpoint. Refuse writes before mutation unless
the normalized destination is the known same-router Wi-Fi save endpoint
and the method is explicitly POST. Treat absent or empty HTML methods as GET.
Keep relative and absolute forms of that endpoint.
Existing requests already reject redirects. Do not broaden allowed routes.

Add mock-only regressions for foreign origins, unknown router routes, queries,
fragments, invalid addresses and unsupported methods. Verify the unchanged
recognized write and read-only imports. Repeat supported Python router suites
and independent complete source review then freeze and rerun release checks.
The compatibility cost is refusing unverified older forms. Router users can
use its own settings page. No live router or Pi changes are needed.
