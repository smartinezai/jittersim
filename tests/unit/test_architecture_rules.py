"""Architecture rules that no single file shows.

docs/architecture.md says what owns what. These checks keep three of those statements
true without anybody having to remember them:

1. Presentation code (the dashboard assembler) imports nothing from the HTTP server, the
   configuration storage, the networking package or the engine.
2. Every script, stylesheet and page in `src/jittersim/api` is either put into the
   dashboard by `assemble_dashboard()` or listed below with a reason. A file that nobody
   assembles never reaches the browser, and nothing else notices.
3. Every data file under `src/jittersim` is matched by the `package-data` globs of its
   package in `pyproject.toml`, so the installed wheel and the Pi contain it.

The checks read source text and package metadata only. They need no Playwright, no browser
and none of the project's dependencies, so they run on every supported Python (3.9 and up).
`tests/unit/test_dashboard_source.py` and the browser journeys check what the assembled page
does. These checks only care that the right files are part of it.

Hash-pinned files (`PANIC_SOURCE_FILES` in `src/jittersim/updates.py`) are not changed by this
file. The last test only keeps the pull request template in step with that list.
"""

import ast
import builtins
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src"
PACKAGE = SOURCE / "jittersim"
API = PACKAGE / "api"

# Modules that only assemble or describe the page. They may read packaged files and the
# standard library. They must not reach the server, stored user data, the privileged
# helper's package or the engine (docs/architecture.md, "Dashboard source").
PRESENTATION_MODULES = ("src/jittersim/api/dashboard.py",)
FORBIDDEN_FOR_PRESENTATION = (
    "jittersim.api.server",
    "jittersim.config.storage",
    "jittersim.networking",
    "jittersim.engine",
)

DASHBOARD_TEMPLATE = "dashboard.html"
ASSET_SUFFIXES = (".js", ".css", ".html")

# Files in src/jittersim/api that are packaged but deliberately not inlined into the page.
# One entry per file: {"name.js": "the reason it is not assembled"}. Empty today because
# every asset is assembled. Add an entry only when a file is served or used some other way,
# and say how. Forgetting to assemble a new script is the mistake this list must not hide.
UNASSEMBLED_ASSETS = {}

# Data files under src/jittersim that package-data does not have to cover. Same rule as
# above: one commented entry per path relative to src/jittersim. Empty today.
UNPACKAGED_DATA_FILES = {}


def _is_within(name, prefix):
    return name == prefix or name.startswith(prefix + ".")


def imported_modules(source, module, is_package=False):
    """Every absolute module name that `source` imports, wherever in the file.

    `module` is the dotted name of the file being read. A `from x import y` adds both `x` and
    `x.y`, because `y` may itself be a module (`from jittersim.api import server`). A
    relative import is resolved against `module`. Calls of `importlib.import_module` and
    `__import__` with a literal name count too.
    """
    package_parts = module.split(".") if is_package else module.split(".")[:-1]
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package_parts[: len(package_parts) - (node.level - 1)]
                if node.module:
                    base_parts = base_parts + node.module.split(".")
                base = ".".join(base_parts)
            else:
                base = node.module or ""
            names.add(base)
            names.update(base + "." + alias.name for alias in node.names)
        elif isinstance(node, ast.Call) and node.args:
            function = node.func
            called = function.attr if isinstance(function, ast.Attribute) else getattr(
                function, "id", ""
            )
            literal = node.args[0]
            if (
                called in ("import_module", "__import__")
                and isinstance(literal, ast.Constant)
                and isinstance(literal.value, str)
            ):
                names.add(literal.value)
    return names


def forbidden_imports(source, module, forbidden=FORBIDDEN_FOR_PRESENTATION):
    return sorted(
        name
        for name in imported_modules(source, module)
        if any(_is_within(name, prefix) for prefix in forbidden)
    )


def _module_name(relative):
    return ".".join(Path(relative).with_suffix("").parts[1:])


@pytest.mark.parametrize("relative", PRESENTATION_MODULES)
def test_presentation_code_imports_no_server_storage_networking_or_engine(relative):
    path = ROOT / relative
    assert path.is_file(), f"{relative} is listed as presentation code but does not exist"
    found = forbidden_imports(path.read_text(encoding="utf-8"), _module_name(relative))
    assert found == [], (
        f"{relative} must not import {', '.join(found)}. Presentation code stays independent "
        "of the HTTP server, stored data, networking and the engine (docs/architecture.md). "
        "Pass the data in from api/server.py instead."
    )


@pytest.mark.parametrize(
    "source, expected",
    [
        ("from jittersim.api import server\n", "jittersim.api.server"),
        ("from .server import ControlServer\n", "jittersim.api.server"),
        ("from . import server\n", "jittersim.api.server"),
        ("from .. import networking\n", "jittersim.networking"),
        ("from ..engine.runtime import RuntimeScheduler\n", "jittersim.engine.runtime"),
        ("def late():\n    import jittersim.networking.helper\n", "jittersim.networking.helper"),
        ("from jittersim.config import storage\n", "jittersim.config.storage"),
        ("import importlib\nimportlib.import_module('jittersim.engine.plan')\n",
         "jittersim.engine.plan"),
        ("__import__('jittersim.api.server')\n", "jittersim.api.server"),
    ],
)
def test_import_scanner_sees_every_way_to_import_a_forbidden_module(source, expected):
    # The scanner is the whole test above. Prove that it can fail.
    assert expected in forbidden_imports(source, "jittersim.api.dashboard")


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\n",
        "from jittersim.config import models\n",
        "from jittersim.api import dashboard\n",
        "import importlib\nimportlib.import_module(name)\n",
        "from .dashboard import assemble_dashboard\n",
    ],
)
def test_import_scanner_allows_what_presentation_code_may_import(source):
    assert forbidden_imports(source, "jittersim.api.dashboard") == []


def _files_read_by_assemble_dashboard():
    """The resolved paths that `assemble_dashboard()` opens, and the page it returns.

    The file is loaded by path because importing `jittersim.api.dashboard` would first import
    the whole HTTP server and its dependencies, and this check needs none of them. Reading is
    recorded rather than searched for in the page, because the assembler inserts one asset
    into the middle of another (`workflows.js` goes in front of the last call in `dashboard.js`).
    """
    spec = importlib.util.spec_from_file_location(
        "jittersim_dashboard_assembler_under_test", API / "dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    opened = set()
    real_path_open = Path.open
    real_open = builtins.open

    def recording_path_open(self, *args, **kwargs):
        opened.add(Path(self).resolve())
        return real_path_open(self, *args, **kwargs)

    def recording_open(file, *args, **kwargs):
        if isinstance(file, (str, Path)):
            opened.add(Path(file).resolve())
        return real_open(file, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "open", recording_path_open)
        patch.setattr(builtins, "open", recording_open)
        page = module.assemble_dashboard()
    return opened, page


def _assets():
    return sorted(path for path in API.iterdir() if path.suffix in ASSET_SUFFIXES)


def test_every_dashboard_asset_is_assembled_or_explicitly_allowed():
    opened, page = _files_read_by_assemble_dashboard()
    # Guard the recorder: the assembler always reads its template, and returns a page.
    assert (API / DASHBOARD_TEMPLATE).resolve() in opened, "the file reads were not recorded"
    assert "</html>" in page
    forgotten = [
        path.name
        for path in _assets()
        if path.name not in UNASSEMBLED_ASSETS and path.resolve() not in opened
    ]
    assert forgotten == [], (
        f"{', '.join(forgotten)} in src/jittersim/api never reaches the dashboard because "
        "assemble_dashboard() in api/dashboard.py does not read it. Add it there, or list it "
        "in UNASSEMBLED_ASSETS in this test with the reason."
    )


def test_the_asset_allow_list_has_no_stale_entries():
    opened, _ = _files_read_by_assemble_dashboard()
    names = {path.name: path for path in _assets()}
    for name, reason in UNASSEMBLED_ASSETS.items():
        assert reason.strip(), f"{name} is allowed without a reason"
        assert name in names, f"{name} is in UNASSEMBLED_ASSETS but no longer exists"
        assert names[name].resolve() not in opened, (
            f"{name} is in UNASSEMBLED_ASSETS but assemble_dashboard() now reads it. "
            "Remove the entry."
        )


def parse_package_data(pyproject_text):
    """The `[tool.setuptools.package-data]` table as {package: [globs]}.

    A small reader for the one table this repository writes, so the check runs on Python 3.9
    where `tomllib` does not exist. `test_package_data_reader_matches_tomllib` compares it
    with the real parser wherever that is available.
    """
    section = re.search(
        r"^\[tool\.setuptools\.package-data\]\s*$(.*?)(?=^\[|\Z)",
        pyproject_text,
        re.MULTILINE | re.DOTALL,
    )
    assert section, "pyproject.toml has no [tool.setuptools.package-data] table"
    table = {}
    for entry in re.finditer(
        r'^\s*"?([\w.\-]+)"?\s*=\s*\[(.*?)\]', section.group(1), re.MULTILINE | re.DOTALL
    ):
        table[entry.group(1)] = re.findall(r'"([^"]+)"', entry.group(2))
    return table


def _pyproject_text():
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_package_data_reader_reads_the_expected_shape():
    text = (
        "[tool.x]\na = 1\n\n[tool.setuptools.package-data]\n"
        '"p.q" = ["*.a",\n  "*.b"]\nr = ["*.c"]\n\n[z]\n'
    )
    assert parse_package_data(text) == {"p.q": ["*.a", "*.b"], "r": ["*.c"]}


def test_package_data_reader_matches_tomllib():
    tomllib = pytest.importorskip("tomllib")  # Python 3.11 and newer
    real = tomllib.loads(_pyproject_text())["tool"]["setuptools"]["package-data"]
    assert parse_package_data(_pyproject_text()) == real


def _owning_package(path):
    """The dotted name of the importable package directory that holds `path`."""
    directory = path.parent
    while directory != SOURCE and not (directory / "__init__.py").is_file():
        directory = directory.parent
    return ".".join(directory.relative_to(SOURCE).parts)


def _data_files():
    skipped = ("__pycache__", ".egg-info")
    return sorted(
        path
        for path in PACKAGE.rglob("*")
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix not in (".py", ".pyc")
        and not any(part.endswith(skipped) for part in path.relative_to(SOURCE).parts)
    )


def test_dashboard_assets_are_among_the_data_files_this_check_covers():
    # Guard the guard: if the file walk found nothing, the next test would pass for no reason.
    covered = {path.name for path in _data_files() if path.parent == API}
    assert {path.name for path in _assets()} <= covered


def test_package_data_globs_cover_every_data_file_under_the_package():
    table = parse_package_data(_pyproject_text())
    uncovered = []
    for path in _data_files():
        relative = path.relative_to(PACKAGE).as_posix()
        if relative in UNPACKAGED_DATA_FILES:
            continue
        package = _owning_package(path)
        directory = SOURCE.joinpath(*package.split("."))
        matched = {found for pattern in table.get(package, []) for found in directory.glob(pattern)}
        if path not in matched:
            uncovered.append(f"{relative} (package {package})")
    assert uncovered == [], (
        f"{', '.join(uncovered)} would be left out of the wheel. Add a glob for it to "
        "[tool.setuptools.package-data] in pyproject.toml, or list it in "
        "UNPACKAGED_DATA_FILES in this test with the reason."
    )


def test_the_unpackaged_data_allow_list_has_no_stale_entries():
    present = {path.relative_to(PACKAGE).as_posix() for path in _data_files()}
    for name, reason in UNPACKAGED_DATA_FILES.items():
        assert reason.strip(), f"{name} is allowed without a reason"
        assert name in present, f"{name} is in UNPACKAGED_DATA_FILES but no longer exists"


def _pinned_source_files():
    tree = ast.parse((PACKAGE / "updates.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PANIC_SOURCE_FILES"
            for target in node.targets
        ):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("PANIC_SOURCE_FILES is no longer a literal in src/jittersim/updates.py")


def test_pull_request_template_names_every_hash_pinned_file():
    pinned = _pinned_source_files()
    assert pinned, "PANIC_SOURCE_FILES is empty"
    template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    missing = [relative for relative in pinned if relative not in template]
    assert missing == [], (
        f".github/pull_request_template.md must name {', '.join(missing)}: the hash-pinned "
        "files in PANIC_SOURCE_FILES (src/jittersim/updates.py). Update the template when "
        "that list changes."
    )
