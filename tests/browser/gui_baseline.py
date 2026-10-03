"""Record bounded screenshots and source statistics without changing the GUI."""

import argparse
import gzip
import re
from collections import Counter
from pathlib import Path

from tests.browser.gui_capture import run_matrix


SCRIPT_AND_STYLE = """() => ({
    styles: Array.from(document.querySelectorAll('style'), node => node.textContent),
    scripts: Array.from(document.querySelectorAll('script:not([src])'), node => node.textContent)
})"""


def source_statistics(styles, scripts):
    css = "\n".join(styles)
    js = "\n".join(scripts)
    values = {}
    for name in ("font-size", "margin", "padding", "gap", "z-index"):
        values[name] = dict(Counter(re.findall(
            r"(?<![\w-])" + name + r"\s*:\s*([^;}]+)", css, flags=re.IGNORECASE)))
    colors = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", css)
    return {"css_utf8_bytes": len(css.encode()), "javascript_utf8_bytes": len(js.encode()),
            "style_blocks": len(styles), "inline_script_blocks": len(scripts),
            "css_gzip6_bytes": len(gzip.compress(css.encode(), compresslevel=6, mtime=0)),
            "important_occurrences": len(re.findall(r"!\s*important\b", css, re.IGNORECASE)),
            "color_text_occurrences": dict(Counter(colors)), "property_value_occurrences": values,
            "count_definition": "Text scans include comments and repeated declarations. Colours include "
            "hex and rgb/rgba text only. Values are not computed CSS or conformance checks."}


def capture(page, state, evidence, store):
    blocks = page.evaluate(SCRIPT_AND_STYLE)
    stats = source_statistics(blocks["styles"], blocks["scripts"])
    for key in ("served_body_bytes", "served_body_gzip6_bytes", "served_body_sha256"):
        stats[key] = evidence[key]
    if state["id"] in ("devices-375-dark", "traffic-1440-dark"):
        before = Counter(evidence["request_counts"])
        page.wait_for_timeout(15000)
        stats["request_window_seconds"] = 15
        stats["window_request_counts"] = dict(Counter(evidence["request_counts"]) - before)
    screenshot = state["id"] + ".png"
    # Credential fields are masked even when their panels are closed.
    data = page.screenshot(full_page=False, animations="disabled", mask=[page.locator(
        'input[type="password"], #admin-token-help')])
    store.write_bytes(screenshot, data)
    report = state["id"] + ".json"
    store.write_json(report, stats)
    return {"screenshot": screenshot, "statistics": report}


def run(output_dir="test-results/gui-baseline"):
    return run_matrix(Path(output_dir), capture, "gui-baseline")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="test-results/gui-baseline")
    run(parser.parse_args().output)
