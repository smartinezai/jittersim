"""Assemble the dashboard source without owning HTTP or network state."""

from pathlib import Path

_ASSET_DIRECTORY = Path(__file__).parent
_INLINE_ASSETS = (
    ("__JITTERSIM_DASHBOARD_STYLE__", "dashboard.css"),
    ("__JITTERSIM_DASHBOARD_SCRIPT__", "dashboard.js"),
    ("__JITTERSIM_NAVIGATION_SCRIPT__", "dashboard-navigation.js"),
)


def assemble_dashboard() -> str:
    """Assemble packaged source for server import."""

    page = (_ASSET_DIRECTORY / "dashboard.html").read_text(encoding="utf-8")
    for marker, _ in _INLINE_ASSETS:
        if page.count(marker) != 1:
            raise ValueError(f"Dashboard template must contain {marker} exactly once")
    for marker, filename in _INLINE_ASSETS:
        page = page.replace(marker, (_ASSET_DIRECTORY / filename).read_text(encoding="utf-8"))
    for filename in ("workflows.css", "access.css", "speed-test.css", "connection-check.css",
                     "phone.css", "refresh.css"):
        page = page.replace(
            "</style>",
            (_ASSET_DIRECTORY / filename).read_text(encoding="utf-8") + "</style>",
            1,
        )
    for filename in ("workflows.js", "access.js", "reconnect.js", "trust.js", "speed-test.js",
                     "connection-check.js"):
        page = page.replace(
            "    startDashboard();",
            (_ASSET_DIRECTORY / filename).read_text(encoding="utf-8")
            + "\n    startDashboard();",
        )
    return page
