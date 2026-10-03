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
