"""Record accessibility observations on the bounded synthetic dashboard matrix."""

import argparse
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path


AXE_VERSION = "4.13.0"
AXE_SHA256 = "c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1"
VENDOR_DIR = Path(__file__).with_name("vendor")
MAX_RULES = 200
MAX_NODES = 5000

SCAN = """async () => {
    const result = await axe.run(document, {
        resultTypes: ['violations', 'incomplete'],
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice']}
    });
    return {version: axe.version, violations: result.violations, incomplete: result.incomplete};
}"""


def load_engine(vendor_dir=VENDOR_DIR):
    """Check the pinned artifact before executing any of it in a test page."""

    vendor_dir = Path(vendor_dir)
    try:
        metadata = json.loads((vendor_dir / "provenance.json").read_text(encoding="utf-8"))
        source = (vendor_dir / "axe.min.js").read_bytes()
    except (OSError, ValueError) as error:
        raise RuntimeError("Cannot read the pinned axe artifact") from error
    digest = hashlib.sha256(source).hexdigest()
    if metadata.get("version") != AXE_VERSION or metadata.get("sha256") != AXE_SHA256:
        raise RuntimeError("Axe provenance does not match the pinned release")
    if digest != AXE_SHA256 or metadata.get("bytes") != len(source):
        raise RuntimeError("Axe artifact hash or byte count does not match")
    if metadata.get("license") != "MPL-2.0" or any(
            not (vendor_dir / name).is_file() for name in ("LICENSE.txt", "LICENSE-3RD-PARTY.txt")):
        raise RuntimeError("Axe license evidence is missing")
    return source.decode("utf-8"), {"version": AXE_VERSION, "sha256": digest}


class SnippetParser(HTMLParser):
    """Keep structural evidence while discarding text and credential attributes."""

    roles = set("alert alertdialog button checkbox columnheader combobox dialog document feed form "
                "grid gridcell group heading img link list listbox listitem log main menu menubar "
                "menuitem navigation none option presentation progressbar radio radiogroup region "
                "row rowgroup rowheader scrollbar search separator slider spinbutton status switch "
                "tab table tablist tabpanel textbox timer toolbar tooltip tree treegrid treeitem".split())
    types = set("button checkbox color date datetime-local email file hidden image month number "
                "password radio range reset search submit tel text time url week".split())
    states = {"aria-expanded": {"true", "false"}, "aria-hidden": {"true", "false"},
              "aria-invalid": {"true", "false", "grammar", "spelling"},
              "aria-checked": {"true", "false", "mixed"},
              "aria-disabled": {"true", "false"}, "aria-live": {"polite", "assertive", "off"}}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        pieces = []
        for name, value in attrs:
            # State attributes have short fixed values. Do not retain names, labels or field contents.
            if name in {"disabled", "hidden", "checked", "selected"}:
                pieces.append(name)
            elif (name == "role" and value in self.roles) or (name == "type" and value in self.types):
                pieces.append('{}="{}"'.format(name, value))
            elif name in self.states and value in self.states[name]:
                pieces.append('{}="{}"'.format(name, value))
            elif name == "tabindex" and value is not None and re.fullmatch(r"-?\d{1,3}", value):
                pieces.append('{}="{}"'.format(name, value))
        self.parts.append("<" + tag + (" " + " ".join(pieces) if pieces else "") + ">")

    def handle_endtag(self, tag):
        self.parts.append("</" + tag + ">")


def safe_snippet(markup):
    parser = SnippetParser()
    parser.feed(markup[:10000])
    snippet = "".join(parser.parts)
    truncated = len(markup) > 10000 or len(snippet) > 512
    return snippet[:512] + (" [structure truncated]" if truncated else "")


def safe_target(target):
    remaining = [200]

    def visit(item, depth):
        remaining[0] -= 1
        if depth > 4 or remaining[0] < 0:
            raise RuntimeError("Axe target selector exceeds its evidence limit")
        if isinstance(item, list):
            if len(item) > 100:
                raise RuntimeError("Axe target selector exceeds its list limit")
            return [visit(child, depth + 1) for child in item]
        if not isinstance(item, str) or len(item) > 2000:
            raise RuntimeError("Axe returned an invalid target selector")
        # Attribute selectors can contain a field value or URL. Keep the attribute name only.
        return re.sub(r"\[([^\]\s~|^$*=]+)\s*[~|^$*]?=\s*[^\]]*\]", r"[\1]", item)

    return visit(target, 0)


def serialize_scan(raw):
    """Store findings and manual checks without the engine's full DOM payload."""

    if not isinstance(raw, dict) or raw.get("version") != AXE_VERSION:
        raise RuntimeError("Axe scan did not return the pinned engine version")
    result = {"engine_version": AXE_VERSION}
    total_nodes = 0
    for group in ("violations", "incomplete"):
        rules = raw.get(group)
        if not isinstance(rules, list) or len(rules) > MAX_RULES:
            raise RuntimeError("Axe scan returned missing or excessive rules")
        result[group] = []
        for rule in rules:
            if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
                raise RuntimeError("Axe scan returned an invalid rule")
            nodes = rule.get("nodes")
            if not isinstance(nodes, list):
                raise RuntimeError("Axe scan returned missing node evidence")
            total_nodes += len(nodes)
            if total_nodes > MAX_NODES:
                raise RuntimeError("Axe scan exceeded its node evidence limit")
            saved = {key: rule.get(key) for key in ("id", "impact", "description", "help", "helpUrl", "tags")}
            saved["nodes"] = []
            for node in nodes:
                if not isinstance(node, dict) or not isinstance(node.get("target"), list):
                    raise RuntimeError("Axe scan returned invalid node evidence")
                if not isinstance(node.get("html"), str):
                    raise RuntimeError("Axe scan returned missing element structure")
                checks = {}
                for kind in ("any", "all", "none"):
                    items = node.get(kind, [])
                    if (not isinstance(items, list) or len(items) > 100
                            or any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
                                   for item in items)):
                        raise RuntimeError("Axe scan returned invalid check evidence")
                    checks[kind] = [{"id": item.get("id"), "impact": item.get("impact")}
                                    for item in items]
                saved["nodes"].append({"target": safe_target(node["target"]),
                                       "impact": node.get("impact"),
                                       "html_structure": safe_snippet(node["html"]),
                                       "checks": checks})
            result[group].append(saved)
    result["counts"] = {group: {"rules": len(result[group]),
                                "nodes": sum(len(rule["nodes"]) for rule in result[group])}
                        for group in ("violations", "incomplete")}
    result["observation_only"] = True
    return result


def make_capture(source, engine):
    def capture(page, state, evidence, store):
        # Browser testing protocol execution preserves the dashboard's production policy.
        # A script element would be rejected by the dashboard's approved inline hashes.
        page.evaluate(source)
        report = serialize_scan(page.evaluate(SCAN))
        report.update({"state": state, "engine": engine})
        artifact = "axe-" + state["id"] + ".json"
        store.write_json(artifact, report)
        return {"state_id": state["id"], "scan_complete": True, "artifact": artifact,
                "engine": engine, "counts": report["counts"], "observation_only": True}
    return capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("test-results/gui-axe"))
    args = parser.parse_args()
    source, engine = load_engine()
    from tests.browser.gui_capture import run_matrix

    run_matrix(args.output, make_capture(source, engine), "axe")


if __name__ == "__main__":
    main()
