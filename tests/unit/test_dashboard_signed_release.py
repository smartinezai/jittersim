"""Setup's Install staged release button works only for a release staged with a valid signature."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[2] / "src/jittersim/api/workflows.js"
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")

HARNESS = r"""
const assert = require("node:assert/strict");
const elements = {};
function element(id) {
  if (!elements[id]) {
    elements[id] = {id, hidden: false, disabled: false, textContent: "", attributes: {},
      classList: {values: new Set(), toggle(name, on) { on ? this.values.add(name) : this.values.delete(name); }},
      setAttribute(name, value) { this.attributes[name] = value; },
      removeAttribute(name) { delete this.attributes[name]; }};
  }
  return elements[id];
}
let onlineView = null;
let onlinePolling=false;
let adminTokenRevision=0,adminTokenVerified=true;
function currentPage() { return "updates"; }
const document = {hidden: false};
const answer = %(answer)s;
async function request(path, method) {
  assert.equal(path, "/api/updates");
  assert.equal(method, "GET");
  return answer;
}
%(source)s
(async () => {
  await loadReleaseStatus({force: true});
  console.log(JSON.stringify({
    status: element("release-status").textContent,
    install: element("release-install").disabled,
    badge: !element("update-available-badge").hidden,
  }));
  console.log("CASE-COMPLETE");
})();
"""


def release_source():
    source = WORKFLOWS.read_text(encoding="utf-8")
    start = source.index("let releaseState = null;")
    return source[start: source.index("function loadUpdateCheckPreference(")]


def show(answer):
    script = HARNESS % {"answer": json.dumps(answer), "source": release_source()}
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[-1] == "CASE-COMPLETE", result.stdout
    return json.loads(lines[-2])


SIGNED = {"phase": "ready", "installed": "old1234", "available": "new5678",
          "rollback_available": False, "installable": True, "signed_by": "desk-mac",
          "reason": None, "message": "Release new5678 signed by desk-mac verified and ready."}


@NODE
def test_a_signed_staged_release_names_its_signer_and_can_be_installed():
    shown = show(SIGNED)
    assert shown["install"] is False and shown["badge"] is True
    assert "Available: new5678 (signed by desk-mac)" in shown["status"]


@NODE
@pytest.mark.parametrize("reason", [
    "This release was staged without a signature. Stage a signed release.",
    "The key that signed the staged release is no longer trusted.",
    "The staged release is not newer than the installed release.",
])
def test_a_staged_release_that_cannot_be_installed_says_why_and_stays_disabled(reason):
    shown = show({**SIGNED, "installable": False, "signed_by": None, "reason": reason})
    assert shown["install"] is True and shown["badge"] is False
    assert shown["status"].endswith(reason)
    assert "signed by" not in shown["status"]


@NODE
def test_an_answer_without_the_signature_fields_never_enables_install():
    answer = {key: value for key, value in SIGNED.items()
              if key not in ("installable", "signed_by", "reason")}
    shown = show(answer)
    assert shown["install"] is True and shown["badge"] is False
