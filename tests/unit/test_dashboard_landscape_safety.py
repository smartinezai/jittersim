"""A compact resize can return focus without stealing it or choosing a hidden toggle."""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML

HANDLER = re.search(
    r'    document.addEventListener\("focusout", \(event\) => \{.*?\n    \}\);',
    DASHBOARD_HTML,
    re.DOTALL,
).group(0)
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")


@NODE
@pytest.mark.parametrize(
    "compact,inside,old_visible,destination,toggle_visible,disabled,expected",
    [
        pytest.param(True, True, False, False, True, False, 1, id="hidden-footer-return"),
        pytest.param(False, True, False, False, True, False, 0, id="desktop-keeps-focus"),
        pytest.param(True, True, False, True, True, False, 0, id="deliberate-focus-wins"),
        pytest.param(True, True, True, False, True, False, 0, id="visible-footer-unchanged"),
        pytest.param(True, False, False, False, True, False, 0, id="outside-footer-unchanged"),
        pytest.param(True, True, False, False, False, False, 0, id="locked-toggle-hidden"),
        pytest.param(True, True, False, False, True, True, 0, id="disabled-toggle-ignored"),
    ],
)
def test_focus_return_obeys_the_target_and_destination(
    compact, inside, old_visible, destination, toggle_visible, disabled, expected
):
    setup = json.dumps(
        {
            "compact": compact,
            "inside": inside,
            "oldVisible": old_visible,
            "destination": destination,
            "toggleVisible": toggle_visible,
            "disabled": disabled,
        }
    )
    script = (
        "const setup = "
        + setup
        + ";\n"
        + r"""
let handler;
let focused = 0;
const document = {addEventListener: (name, callback) => {handler = callback;}};
const phoneLayout = {matches: setup.compact};
const controlsSheet = {contains: () => setup.inside};
const controlsToggle = {
  disabled: setup.disabled,
  getClientRects: () => setup.toggleVisible ? [{}] : [],
  focus: () => {focused++;}
};
"""
        + HANDLER
        + r"""
handler({relatedTarget: setup.destination ? {} : null,
  target: {getClientRects: () => setup.oldVisible ? [{}] : []}});
console.log(JSON.stringify(focused));
"""
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
