"""Run the dashboard's Traffic discovery label helpers in node."""

import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)


def served_functions():
    start = SCRIPT.index("function domainSearchUrl(")
    return SCRIPT[start : SCRIPT.index("function renderObservations(", start)]


def function_body(name):
    """Return the source of one top-level dashboard function."""

    match = re.search(r"\n    (?:async )?function " + re.escape(name) + r"\(", SCRIPT)
    assert match, name
    following = re.search(r"\n    (?:async )?function \w+\(", SCRIPT[match.end() :])
    end = match.end() + following.start() if following else len(SCRIPT)
    return SCRIPT[match.start() : end]


CASES = {
    "search_url_is_encoded": (
        "domainSearchUrl('a b.example.com')",
        "https://duckduckgo.com/?q=a%20b.example.com",
    ),
    "hint_for_a_shared_name": (
        "JSON.stringify(domainHint({label:{kind:'cdn'}, seen_with:['WhatsApp']}))",
        '{"text":"Seen with WhatsApp (guess)","searchable":true}',
    ),
    "two_apps": (
        "JSON.stringify(domainHint({label:{kind:'tracker'}, seen_with:['A','B']}))",
        '{"text":"Seen with A and B (guess)","searchable":false}',
    ),
    "unknown_without_hint": (
        "JSON.stringify(domainHint({label:{kind:'unknown'}, seen_with:[]}))",
        '{"text":"","searchable":true}',
    ),
    "known_app": (
        "JSON.stringify(domainHint({label:{kind:'app'}, seen_with:[]}))",
        '{"text":"","searchable":false}',
    ),
    "missing_label": (
        "JSON.stringify(domainHint({}))",
        '{"text":"","searchable":false}',
    ),
}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
@pytest.mark.parametrize("name", CASES)
def test_label_helpers(name):
    expression, expected = CASES[name]
    program = served_functions() + "\nconsole.log(" + expression + ");\n"
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_each_row_shows_the_label_the_raw_name_the_hint_and_a_search_link():
    body = function_body("renderObservations")
    assert "observation.label" in body
    assert "domainHint(observation)" in body
    assert "domainSearchUrl(observation.domain)" in body
    assert "What is this?" in body
    assert 'link.target = "_blank";' in body
    assert 'link.rel = "noopener noreferrer";' in body


def test_the_row_text_is_set_without_parsing_html():
    body = function_body("renderObservations")
    assert "innerHTML" not in body
    assert "insertAdjacentHTML" not in body


def test_the_row_keeps_the_query_count_and_both_buttons():
    body = function_body("renderObservations")
    assert 'observation.query_count + " queries"' in body
    assert 'profileButton.textContent = "Add to profile";' in body
    assert 'blockButton.textContent = "Block globally";' in body


def test_the_dashboard_never_calls_the_search_site_by_itself():
    # The address appears only as the link target that a click opens.
    assert SCRIPT.count("duckduckgo.com") == 1
    assert "fetch(domainSearchUrl" not in SCRIPT
    assert "request(domainSearchUrl" not in SCRIPT
