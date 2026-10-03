"""Protect failure reporting and credential-free accessibility evidence."""

import json

import pytest

from tests.browser import a11y_axe


def result():
    return {"version": a11y_axe.AXE_VERSION, "violations": [{
        "id": "color-contrast", "impact": "serious", "help": "Elements need sufficient contrast",
        "nodes": [{"target": ['input[value="fixture-secret"]'], "impact": "serious",
                   "html": '<input type="password" value="fixture-secret" aria-label="fixture-secret">',
                   "any": [{"id": "color-contrast", "impact": "serious", "data": "fixture-secret"}]}]}],
        "incomplete": [{"id": "landmark-one-main", "nodes": [
            {"target": ["body"], "html": '<body>fixture-secret</body>'}]}]}


def test_findings_and_manual_checks_remain_observations_without_credentials():
    report = a11y_axe.serialize_scan(result())
    assert report["counts"] == {"violations": {"rules": 1, "nodes": 1},
                                "incomplete": {"rules": 1, "nodes": 1}}
    assert report["observation_only"] is True
    assert report["violations"][0]["nodes"][0]["target"] == ["input[value]"]
    assert report["violations"][0]["nodes"][0]["html_structure"] == '<input type="password">'
    assert "fixture-secret" not in json.dumps(report)


def test_structure_drops_unexpected_values_even_in_allowed_attribute_names():
    markup = '<div role="fixture-secret" aria-live="fixture-secret" hidden="fixture-secret">secret</div>'
    assert a11y_axe.safe_snippet(markup) == "<div hidden></div>"


@pytest.mark.parametrize("raw", [None, {}, {"version": "old", "violations": [], "incomplete": []},
                                 {"version": a11y_axe.AXE_VERSION, "violations": []}])
def test_scan_errors_cannot_be_reported_as_clean(raw):
    with pytest.raises(RuntimeError):
        a11y_axe.serialize_scan(raw)


def test_evidence_limit_fails_instead_of_silently_dropping_nodes(monkeypatch):
    monkeypatch.setattr(a11y_axe, "MAX_NODES", 1)
    with pytest.raises(RuntimeError, match="node evidence limit"):
        a11y_axe.serialize_scan(result())


def test_changed_artifact_fails_when_metadata_claims_the_pinned_hash(tmp_path):
    source = b"modified engine"
    (tmp_path / "axe.min.js").write_bytes(source)
    (tmp_path / "LICENSE.txt").write_text("license")
    (tmp_path / "provenance.json").write_text(json.dumps({
        "version": a11y_axe.AXE_VERSION, "sha256": a11y_axe.AXE_SHA256,
        "bytes": len(source), "license": "MPL-2.0"}))
    with pytest.raises(RuntimeError, match="artifact hash"):
        a11y_axe.load_engine(tmp_path)


def test_actual_pinned_artifact_loads_with_verified_version_and_hash():
    source, engine = a11y_axe.load_engine()
    assert len(source.encode("utf-8")) == 580491
    assert engine == {"version": "4.13.0", "sha256": a11y_axe.AXE_SHA256}


@pytest.mark.parametrize("missing", ["LICENSE.txt", "LICENSE-3RD-PARTY.txt"])
def test_removing_either_license_fails_before_engine_execution(tmp_path, missing):
    for name in ("axe.min.js", "provenance.json", "LICENSE.txt", "LICENSE-3RD-PARTY.txt"):
        if name != missing:
            (tmp_path / name).write_bytes((a11y_axe.VENDOR_DIR / name).read_bytes())
    with pytest.raises(RuntimeError, match="license evidence"):
        a11y_axe.load_engine(tmp_path)


def test_large_input_structure_has_an_explicit_truncation_note():
    assert a11y_axe.safe_snippet("<div>" + "x" * 10000 + "</div>").endswith("[structure truncated]")


@pytest.mark.parametrize("target", [["body"] * 101, [[[[[["body"]]]]]],
                                   [["body"] * 100, ["body"] * 100]])
def test_target_depth_list_and_total_node_limits_are_enforced(target):
    with pytest.raises(RuntimeError, match="limit"):
        a11y_axe.safe_target(target)


def test_invalid_check_evidence_fails_instead_of_looking_complete():
    raw = result()
    raw["violations"][0]["nodes"][0]["any"] = [None]
    with pytest.raises(RuntimeError, match="invalid check evidence"):
        a11y_axe.serialize_scan(raw)


def test_failed_engine_execution_does_not_write_a_clean_report():
    class Page:
        def evaluate(self, source):
            raise RuntimeError("Simulated browser scan error")

    class Store:
        def write_json(self, *args):
            pytest.fail("A failed scan must not write a successful report")

    with pytest.raises(RuntimeError, match="Simulated"):
        a11y_axe.make_capture("source", {})(Page(), {"id": "a"}, {}, Store())
