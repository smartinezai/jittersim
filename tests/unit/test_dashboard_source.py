"""Reject incomplete packaged source instead of serving a partial dashboard."""

import pytest

from jittersim.api import dashboard


@pytest.mark.parametrize("marker", [marker for marker, _ in dashboard._INLINE_ASSETS])
@pytest.mark.parametrize("occurrences", [0, 2])
def test_invalid_inline_slot_refuses_to_assemble(tmp_path, monkeypatch, marker, occurrences):
    template = (dashboard._ASSET_DIRECTORY / "dashboard.html").read_text(encoding="utf-8")
    (tmp_path / "dashboard.html").write_text(
        template.replace(marker, marker * occurrences), encoding="utf-8"
    )
    monkeypatch.setattr(dashboard, "_ASSET_DIRECTORY", tmp_path)

    with pytest.raises(ValueError, match="exactly once"):
        dashboard.assemble_dashboard()


def test_missing_packaged_asset_refuses_to_assemble(tmp_path, monkeypatch):
    template = (dashboard._ASSET_DIRECTORY / "dashboard.html").read_text(encoding="utf-8")
    (tmp_path / "dashboard.html").write_text(template, encoding="utf-8")
    monkeypatch.setattr(dashboard, "_ASSET_DIRECTORY", tmp_path)

    with pytest.raises(FileNotFoundError, match="dashboard.css"):
        dashboard.assemble_dashboard()


def _copy_assets(directory):
    for source in dashboard._ASSET_DIRECTORY.iterdir():
        if source.suffix in (".html", ".css", ".js"):
            (directory / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def test_extra_scripts_are_inserted_once_before_the_single_start_call():
    page = dashboard.assemble_dashboard()

    assert page.count(dashboard._START_CALL) == 1
    for filename in dashboard._EXTRA_SCRIPTS:
        script = (dashboard._ASSET_DIRECTORY / filename).read_text(encoding="utf-8")
        assert page.count(script) == 1
        assert page.index(script) < page.index(dashboard._START_CALL)


# dashboard.js holds the real call, so none or two of them both break the rule.
# An extra script must hold no call at all, so one stray call is enough.
@pytest.mark.parametrize(
    ("carrier", "occurrences"),
    [("dashboard.js", 0), ("dashboard.js", 2), *((name, 1) for name in dashboard._EXTRA_SCRIPTS)],
)
def test_start_call_must_appear_exactly_once(tmp_path, monkeypatch, carrier, occurrences):
    _copy_assets(tmp_path)
    source = (tmp_path / carrier).read_text(encoding="utf-8")
    if carrier == "dashboard.js":
        source = source.replace(dashboard._START_CALL, dashboard._START_CALL * occurrences)
    else:
        source += "\n" + dashboard._START_CALL + "\n"
    (tmp_path / carrier).write_text(source, encoding="utf-8")
    monkeypatch.setattr(dashboard, "_ASSET_DIRECTORY", tmp_path)

    with pytest.raises(ValueError, match="startDashboard.*exactly once"):
        dashboard.assemble_dashboard()
