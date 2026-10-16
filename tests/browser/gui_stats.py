"""Static size and complexity numbers of the dashboard page, with a committed baseline.

The numbers come straight from the assembled page (`jittersim.api.server.DASHBOARD_HTML`).
They need no browser, no Playwright and no Pillow, and the same page always gives the same
numbers. `tests/unit/test_gui_budget.py` compares them with `baselines/gui-stats.json`: a number
may not grow beyond its recorded value by more than a small tolerance, so the dashboard can only
get smaller and simpler. Colour, font-size, `!important` and spacing counts reuse
`gui_baseline.source_statistics`, so a number here means what it means in a browser baseline run.

    python -m tests.browser.gui_stats                         show the numbers against the budget
    python -m tests.browser.gui_stats --update                lock in numbers that went down
    python -m tests.browser.gui_stats --update --allow-growth raise a number on purpose
"""

import argparse
import gzip
import json
import math
import re
import sys
from pathlib import Path

from tests.browser.gui_baseline import source_statistics

BASELINE_PATH = Path(__file__).parent / "baselines" / "gui-stats.json"
SCHEMA = 1

# Size and spread numbers may grow by this much before the budget test fails. Anything in
# STRICT may not grow at all: those are accessibility and safety counts that only go down.
TOLERANCE_PERCENT = 2

# One entry per number: (name, strict, definition). The definitions are written into the
# baseline file so a reader of the JSON knows what each number counts.
METRICS = (
    ("page_bytes", False, "UTF-8 bytes of the assembled page"),
    ("page_gzip6_bytes", False, "gzip level 6, fixed timestamp, of the assembled page"),
    ("page_lines", False, "lines of the assembled page"),
    ("markup_bytes", False, "UTF-8 bytes of the page without its style and script blocks"),
    ("markup_lines", False, "lines of the page without its style and script blocks"),
    ("css_bytes", False, "UTF-8 bytes of all inline style blocks"),
    ("css_gzip6_bytes", False, "gzip level 6 of all inline style blocks"),
    ("css_lines", False, "lines of all inline style blocks"),
    ("js_bytes", False, "UTF-8 bytes of all inline script blocks"),
    ("js_lines", False, "lines of all inline script blocks"),
    ("element_ids", False, "id attributes in the markup"),
    ("distinct_colors", False, "distinct hex and rgb()/rgba() colour texts in the CSS, as gui_baseline counts them"),
    ("color_occurrences", False, "all hex and rgb()/rgba() colour texts in the CSS"),
    ("distinct_font_sizes", False, "distinct font-size values in the CSS"),
    ("distinct_margin_values", False, "distinct margin values in the CSS"),
    ("distinct_padding_values", False, "distinct padding values in the CSS"),
    ("distinct_gap_values", False, "distinct gap values in the CSS"),
    ("distinct_z_indexes", False, "distinct z-index values in the CSS"),
    ("distinct_border_radii", False, "distinct border-radius values in the CSS"),
    ("distinct_media_queries", False, "distinct @media conditions in the CSS"),
    ("important_declarations", True, "!important in the CSS"),
    ("root_rules", True, ":root rules in the CSS, inside media queries too"),
    ("aria_live_in_markup", True, "aria-live attributes in the markup"),
    ("aria_live_flip_sites", True, 'setAttribute("aria-live", ...) calls in the scripts'),
    ("inline_event_handler_attributes", True, "onclick-style attributes in the markup"),
    ("inline_style_attributes", True, "style attributes in the markup"),
    ("window_confirm_calls", True, "window.confirm() calls in the scripts"),
)
STRICT = frozenset(name for name, strict, _ in METRICS if strict)

_BLOCK = re.compile(r"<(style|script)\b([^>]*)>(.*?)</\1\s*>", re.DOTALL | re.IGNORECASE)


def _blocks(page):
    styles, scripts = [], []
    for kind, attributes, text in _BLOCK.findall(page):
        if kind.lower() == "style":
            styles.append(text)
        elif not re.search(r"\bsrc\s*=", attributes, re.IGNORECASE):
            scripts.append(text)
    return styles, scripts, _BLOCK.sub("", page)


def _distinct(css, pattern):
    return len({" ".join(value.split()) for value in re.findall(pattern, css, re.IGNORECASE)})


def measure(page=None):
    """Return every number in METRICS for a page. The default is the page the Pi serves."""

    if page is None:
        from jittersim.api.server import DASHBOARD_HTML as page
    styles, scripts, markup = _blocks(page)
    css, js = "\n".join(styles), "\n".join(scripts)
    stats = source_statistics(styles, scripts)
    values = stats["property_value_occurrences"]
    numbers = {
        "page_bytes": len(page.encode()),
        "page_gzip6_bytes": len(gzip.compress(page.encode(), compresslevel=6, mtime=0)),
        "page_lines": page.count("\n") + 1,
        "markup_bytes": len(markup.encode()),
        "markup_lines": markup.count("\n") + 1,
        "css_bytes": stats["css_utf8_bytes"],
        "css_gzip6_bytes": stats["css_gzip6_bytes"],
        "css_lines": css.count("\n") + 1,
        "js_bytes": stats["javascript_utf8_bytes"],
        "js_lines": js.count("\n") + 1,
        "element_ids": len(re.findall(r"\sid\s*=\s*[\"']", markup)),
        "distinct_colors": len(stats["color_text_occurrences"]),
        "color_occurrences": sum(stats["color_text_occurrences"].values()),
        "distinct_font_sizes": len(values["font-size"]),
        "distinct_margin_values": len(values["margin"]),
        "distinct_padding_values": len(values["padding"]),
        "distinct_gap_values": len(values["gap"]),
        "distinct_z_indexes": len(values["z-index"]),
        "distinct_border_radii": _distinct(css, r"(?<![\w-])border-radius\s*:\s*([^;}]+)"),
        "distinct_media_queries": _distinct(css, r"@media\s*([^{]+)\{"),
        "important_declarations": stats["important_occurrences"],
        "root_rules": len(re.findall(r":root\s*\{", css)),
        "aria_live_in_markup": len(re.findall(r"\baria-live\s*=", markup)),
        "aria_live_flip_sites": len(re.findall(r"setAttribute\(\s*[\"']aria-live[\"']", js)),
        "inline_event_handler_attributes": len(re.findall(r"\son[a-z]+\s*=\s*[\"']", markup)),
        "inline_style_attributes": len(re.findall(r"\sstyle\s*=\s*[\"']", markup)),
        "window_confirm_calls": len(re.findall(r"window\.confirm\s*\(|(?<![\w$.])confirm\s*\(", js)),
    }
    assert set(numbers) == {name for name, _, _ in METRICS}, "METRICS and measure() disagree"
    return numbers


def limit(name, recorded):
    """The largest value that still passes for a metric with this recorded number."""

    if name in STRICT:
        return recorded
    return recorded + math.floor(recorded * TOLERANCE_PERCENT / 100)


def compare(current, recorded):
    """Return one problem text for every number that is over its limit or has no baseline."""

    problems = []
    for name, _, _ in METRICS:
        if name not in recorded:
            problems.append("%s: now %d, but the baseline has no value for it" % (name, current[name]))
        elif current[name] > limit(name, recorded[name]):
            allowed = "no growth allowed" if name in STRICT else "recorded + %d%%" % TOLERANCE_PERCENT
            problems.append("%s: now %d, limit %d (recorded %d, %s)"
                            % (name, current[name], limit(name, recorded[name]), recorded[name], allowed))
    return problems


def updated(current, recorded, *, allow_growth=False):
    """Return the recorded numbers after an update.

    A number that went down is lowered to its new value. A number that is missing is added.
    A number over its limit is only raised when growth is allowed on purpose.
    """

    result = {}
    for name, _, _ in METRICS:
        old = recorded.get(name)
        if old is None or current[name] < old:
            result[name] = current[name]
        elif current[name] > limit(name, old) and allow_growth:
            result[name] = current[name]
        else:
            result[name] = old
    return result


def render(recorded):
    """The baseline file text. One number per line keeps merge conflicts to the lines in dispute."""

    document = {
        "schema": SCHEMA,
        "definitions": {name: text for name, _, text in METRICS},
        "recorded": recorded,
        "tolerance_percent": TOLERANCE_PERCENT,
        "strict": sorted(STRICT),
    }
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


def load_recorded(path=BASELINE_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))["recorded"]


def explain(problems):
    """The failure message of the budget test. It says what to do, not only what is wrong."""

    return (
        "The dashboard grew beyond its committed static budget:\n  "
        + "\n  ".join(problems)
        + "\n\nThe budget lets the dashboard only get smaller and simpler (tests/browser/GUI_BASELINE.md)."
        "\nFirst try to bring the number back down: reuse an existing colour or size, remove what is unused."
        "\nIf the growth is deliberate and reviewed, raise the budget on purpose and say why in the PR:"
        "\n  python -m tests.browser.gui_stats --update --allow-growth"
        "\nAfter a change that lowered numbers, lock the gain in with:"
        "\n  python -m tests.browser.gui_stats --update"
        "\nA merge conflict in gui-stats.json: keep either side, then run --update (add --allow-growth"
        "\nif your own change needs a higher number). Each number sits on its own line."
    )


def _table(current, recorded):
    lines = ["%-34s %10s %10s %10s" % ("metric", "now", "recorded", "limit")]
    for name, _, _ in METRICS:
        old = recorded.get(name)
        lines.append("%-34s %10d %10s %10s" % (
            name, current[name], "-" if old is None else old, "-" if old is None else limit(name, old)))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true",
                        help="lower recorded numbers that went down and add missing ones")
    parser.add_argument("--allow-growth", action="store_true",
                        help="with --update, also raise numbers that are over their limit")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    arguments = parser.parse_args(argv)
    if arguments.allow_growth and not arguments.update:
        parser.error("--allow-growth only makes sense with --update")
    current = measure()
    recorded = load_recorded(arguments.baseline) if arguments.baseline.exists() else {}
    if arguments.update:
        arguments.baseline.parent.mkdir(parents=True, exist_ok=True)
        after = updated(current, recorded, allow_growth=arguments.allow_growth)
        arguments.baseline.write_text(render(after), encoding="utf-8")
        print(_table(current, after))
        print("Wrote %s" % arguments.baseline)
        problems = compare(current, after)
    else:
        print(_table(current, recorded))
        problems = compare(current, recorded)
    if problems:
        print("\n" + explain(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
