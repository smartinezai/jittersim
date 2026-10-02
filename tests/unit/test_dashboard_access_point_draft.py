"""A Wi-Fi name the page sets itself must not be replaced by an old draft."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jittersim.api.server import DASHBOARD_HTML

WORKFLOWS = Path(__file__).resolve().parents[2] / "src/jittersim/api/workflows.js"
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")

HARNESS = r"""
const assert = require("node:assert/strict");
const field = {id: "access-point-ssid", type: "text", value: "JitterSim", tagName: "INPUT",
  hasAttribute: () => false, getAttribute: () => null, setAttribute() {}};
const form = {id: "access-point-form", querySelectorAll: () => [field]};
const notice = {hidden: true};
const count = {textContent: ""};
function element(id) {
  return {"access-point-form": form, "draft-notice": notice, "draft-count": count}[id] || null;
}
%(drafts)s
// The user types a name. Then Reset restores the real one and the page shows it.
field.value = "Copied network";
rememberDraft(form);
assert.equal(formDrafts.size, 1);
assert.equal(notice.hidden, false);
field.value = "JitterSim";
%(after)s
restoreDrafts();
assert.equal(field.value, "%(expected)s");
assert.equal(formDrafts.size, %(drafts_left)s);
assert.equal(notice.hidden, %(hidden)s);
"""


def drafts_source():
    source = WORKFLOWS.read_text(encoding="utf-8")
    return source[: source.index("function formError(")]


def run(after, expected, drafts_left, hidden):
    script = HARNESS % {
        "drafts": drafts_source(),
        "after": after,
        "expected": expected,
        "drafts_left": drafts_left,
        "hidden": hidden,
    }
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@NODE
def test_the_old_behaviour_put_the_typed_name_back():
    # Without clearing the draft the next refresh restores what was typed.
    run("", "Copied network", 1, "false")


@NODE
def test_clearing_the_draft_keeps_the_name_the_page_set():
    run('clearDraft("access-point-form");', "JitterSim", 0, "true")


def handler(source_start):
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
    start = script.index(source_start)
    return script[start : script.index("\n    });", start)]


def test_the_reset_and_the_save_clear_the_draft_after_setting_the_field():
    reset = handler('element("reset-wifi-name").addEventListener("click"')
    assert reset.index("element(\"access-point-ssid\").value = response.result.ssid") < reset.index(
        'clearDraft("access-point-form")'
    )
    # The failure path keeps the typed name so nothing the person wrote is lost.
    assert reset.index('clearDraft("access-point-form")') < reset.index("} catch (error)")
    save = handler('element("access-point-form").addEventListener("submit"')
    assert save.index('request("/api/access-point"') < save.index('clearDraft("access-point-form")')
    assert save.index('clearDraft("access-point-form")') < save.index("} catch (error)")
