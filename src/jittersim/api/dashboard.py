"""Assemble the dashboard source without owning HTTP or network state."""

from pathlib import Path

_ASSET_DIRECTORY = Path(__file__).parent
_INLINE_ASSETS = (
    ("__JITTERSIM_DASHBOARD_STYLE__", "dashboard.css"),
    ("__JITTERSIM_DASHBOARD_SCRIPT__", "dashboard.js"),
    ("__JITTERSIM_NAVIGATION_SCRIPT__", "dashboard-navigation.js"),
)
# The scripts that join dashboard.js inside its closure, right before this call.
_START_CALL = "    startDashboard();"
_EXTRA_SCRIPTS = ("workflows.js", "access.js", "reconnect.js", "trust.js", "address-follow.js")


def assemble_dashboard() -> str:
    """Assemble packaged source for server import."""

    page = (_ASSET_DIRECTORY / "dashboard.html").read_text(encoding="utf-8")
    for marker, _ in _INLINE_ASSETS:
        if page.count(marker) != 1:
            raise ValueError(f"Dashboard template must contain {marker} exactly once")
    for marker, filename in _INLINE_ASSETS:
        page = page.replace(marker, (_ASSET_DIRECTORY / filename).read_text(encoding="utf-8"))
    for filename in ("workflows.css", "access.css", "phone.css", "refresh.css"):
        page = page.replace(
            "</style>",
            (_ASSET_DIRECTORY / filename).read_text(encoding="utf-8") + "</style>",
            1,
        )
    scripts = "".join(
        (_ASSET_DIRECTORY / filename).read_text(encoding="utf-8") + "\n"
        for filename in _EXTRA_SCRIPTS
    )
    # A second call, in dashboard.js or in an extra script, would insert the scripts twice.
    if page.count(_START_CALL) != 1 or _START_CALL in scripts:
        raise ValueError(f"Dashboard script must contain {_START_CALL.strip()} exactly once")
    return page.replace(_START_CALL, scripts + _START_CALL)
