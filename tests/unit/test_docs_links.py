"""Every relative link in the Markdown pages points at a file and a heading that exist.

A moved or renamed page breaks the links to it without any other check noticing. The README, the
user guide, the Pi guide and the runbooks link to each other a lot. This reads the Markdown text
only: it needs no network, no extra tool and runs on every supported Python.

`docs/history` is skipped on purpose. Those files are exact copies of earlier documents, and their
README says which links in them point to the old folder.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOP_LEVEL = tuple(sorted(ROOT.glob("*.md")))
FOLDERS = ("docs", "deploy", "extensions", "experiments", "scripts", "tests", ".github")
SKIPPED = ("docs/history/",)
# Folders that a checkout may hold without tracking them.
IGNORED_PARTS = {"node_modules", ".venv", "test-results", "build", ".git"}

INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+.*)?$")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")


def tracked_markdown():
    """Every Markdown file that Git tracks, or None where Git is not there to ask."""

    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.md"], cwd=ROOT, capture_output=True, check=True, timeout=30
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    names = [name for name in listing.decode("utf-8").split("\0") if name]
    return [ROOT / name for name in names if (ROOT / name).is_file()] or None


def markdown_files():
    # Git knows every page, so a page in a new folder or in .github is checked without a list to
    # keep. Where Git is missing (a container without it) the known folders are searched instead.
    found = tracked_markdown()
    if found is None:
        found = list(TOP_LEVEL)
        for folder in FOLDERS:
            found.extend(path for path in sorted((ROOT / folder).rglob("*.md")) if path.is_file())
    found = [path for path in found if not IGNORED_PARTS & set(path.parts)]
    return [path for path in sorted(set(found))
            if not any(path.relative_to(ROOT).as_posix().startswith(part) for part in SKIPPED)]


def prose_lines(text, keep_code=False):
    """The lines outside code fences. Inline code is removed unless a heading needs its words."""

    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced:
            yield number, line if keep_code else re.sub(r"`[^`]*`", "", line)


def github_slug(heading):
    """The anchor GitHub makes from a heading: emphasis marks go, the text of code stays as it is
    and an underscore inside a word stays (a heading about `snake_case` links to snake_case)."""

    heading = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    emphasis = re.compile(r"\*|(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])")
    parts = re.split(r"(`[^`]*`)", heading)
    text = "".join(part[1:-1] if part.startswith("`") else emphasis.sub("", part) for part in parts)
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


_anchors = {}


def anchors_of(path):
    if path not in _anchors:
        seen, found = {}, set()
        for _number, line in prose_lines(path.read_text(encoding="utf-8"), keep_code=True):
            match = HEADING.match(line)
            if match:
                slug = github_slug(match.group(1))
                count = seen.get(slug, 0)
                seen[slug] = count + 1
                found.add(slug if count == 0 else f"{slug}-{count}")
        _anchors[path] = found
    return _anchors[path]


def links_of(path):
    for number, line in prose_lines(path.read_text(encoding="utf-8")):
        for match in INLINE_LINK.finditer(line):
            yield number, match.group(1)
        match = REFERENCE_LINK.match(line)
        if match:
            yield number, match.group(1)


def broken_links(path, root=ROOT):
    problems = []
    for number, target in links_of(path):
        if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target) or target.startswith("//"):
            continue  # https:, mailto: and the like are not checked here
        name, _, fragment = target.partition("#")
        destination = path if not name else (path.parent / name).resolve()
        where = f"{path.relative_to(root).as_posix()}:{number}"
        if not destination.exists():
            problems.append(f"{where}: {target} points at a file that does not exist")
        elif fragment and destination.suffix == ".md" and fragment not in anchors_of(destination):
            problems.append(f"{where}: {target} points at a heading that does not exist")
    return problems


def test_the_checker_sees_the_pages_and_their_links():
    pages = {path.relative_to(ROOT).as_posix() for path in markdown_files()}
    assert {"README.md", "docs/user-guide.md", "docs/README.md", "deploy/raspberry-pi/README.md",
            ".github/pull_request_template.md"} <= pages
    assert not [page for page in pages if page.startswith("docs/history/")]
    assert sum(1 for _ in links_of(ROOT / "docs" / "user-guide.md")) > 10


def test_the_checker_finds_a_missing_file_and_a_missing_heading(tmp_path):
    (tmp_path / "other.md").write_text(
        "# Real heading\n\n## Same\n\n## Same\n\n### 8. Spike: let `dnsmasq --nftset` learn\n", encoding="utf-8")
    page = tmp_path / "page.md"
    page.write_text(
        "[ok](other.md#real-heading) [twin](other.md#same-1) [web](https://example.invalid/x#y)\n"
        "[code](other.md#8-spike-let-dnsmasq---nftset-learn)\n"
        "[gone](missing.md) [bad](other.md#nothing) [self](#nope)\n"
        "`[code](missing.md)`\n\n```\n[fenced](missing.md)\n```\n",
        encoding="utf-8",
    )
    problems = broken_links(page, tmp_path)
    assert [problem.split(": ", 1)[1] for problem in problems] == [
        "missing.md points at a file that does not exist",
        "other.md#nothing points at a heading that does not exist",
        "#nope points at a heading that does not exist",
    ]


def test_a_slug_keeps_underscores_inside_words_and_drops_emphasis():
    assert github_slug("The `snake_case` name") == "the-snake_case-name"
    assert github_slug("a_b_c") == "a_b_c"
    assert github_slug("Use _italic_, **bold** and __init__") == "use-italic-bold-and-init"
    assert github_slug("`_private` helper") == "_private-helper"
    assert github_slug("[Link_text](other.md) here") == "link_text-here"


def test_every_tracked_page_is_checked_and_the_folders_are_the_fallback(tmp_path, monkeypatch):
    tracked = {path.relative_to(ROOT).as_posix() for path in markdown_files()}
    assert ".github/pull_request_template.md" in tracked
    # Without Git the folders are searched, and .github is one of them.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("git")))
    fallback = {path.relative_to(ROOT).as_posix() for path in markdown_files()}
    assert ".github/pull_request_template.md" in fallback and "README.md" in fallback


@pytest.mark.parametrize("path", markdown_files(), ids=lambda path: path.relative_to(ROOT).as_posix())
def test_every_relative_link_resolves(path):
    assert not broken_links(path), "\n".join(broken_links(path))
