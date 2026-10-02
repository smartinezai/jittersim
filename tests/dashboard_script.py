"""Run named functions of the served dashboard script in node for parity tests."""

import json
import re
import shutil
import subprocess
from typing import Any, Iterable

from jittersim.api.server import DASHBOARD_HTML

SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)
NODE = shutil.which("node")


def _skip_string(source: str, index: int) -> int:
    quote = source[index]
    index += 1
    while source[index] != quote:
        index += 2 if source[index] == "\\" else 1
    return index


def function_source(name: str) -> str:
    """The whole `function NAME(...) {...}` declaration as the page serves it."""

    start = SCRIPT.index(f"function {name}(")
    index = SCRIPT.index("{", SCRIPT.index(")", start))
    depth = 0
    while True:
        character = SCRIPT[index]
        if character in "\"'`":
            index = _skip_string(SCRIPT, index)
        elif SCRIPT.startswith("//", index):
            index = SCRIPT.index("\n", index)
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return SCRIPT[start : index + 1]
        index += 1


def constant_source(name: str) -> str:
    """One `const NAME = ...;` line or block of the page."""

    start = SCRIPT.index(f"const {name} =")
    return SCRIPT[start : SCRIPT.index(";\n", start) + 1]


def run_node(sources: Iterable[str], expression: str) -> Any:
    program = "\n".join(sources) + "\nconsole.log(JSON.stringify(" + expression + "));\n"
    result = subprocess.run(
        [NODE, "-"], input=program, text=True, capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)
