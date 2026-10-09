"""Releases carry semantic version numbers: MAJOR.MINOR.PATCH from a vX.Y.Z tag."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import jittersim
from jittersim.updates import SEMANTIC_VERSION, UpdateError, request_update, version_key
from jittersim import updates
from tests.release_support import have_ssh_keygen, make_key, signed_release

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "deploy" / "raspberry-pi" / "build-release.sh"


class VersionFormTests(unittest.TestCase):
    def test_only_numbers_and_dev_builds_are_versions(self) -> None:
        for good in ("0.2.0", "1.0.0", "10.20.30", "0.2.0.dev+a0dc551", "1.4.2.dev+a0dc5516d59de5797a71e114a8717675f035ff25"):
            with self.subTest(good=good):
                self.assertTrue(SEMANTIC_VERSION.fullmatch(good))
        for bad in ("v0.2.0", "0.2", "a0dc551", "v1", "01.2.3", "0.2.0-rc1", "0.2.0.dev", "0.2.0.dev+xyz", "0.2.0 ", ""):
            with self.subTest(bad=bad):
                self.assertFalse(SEMANTIC_VERSION.fullmatch(bad))

    def test_numbers_order_by_value_and_a_dev_build_comes_before_its_release(self) -> None:
        ordered = ["0.2.0.dev+a0dc551", "0.2.0", "0.2.1", "0.10.0", "1.0.0"]
        self.assertEqual(sorted(reversed(ordered), key=version_key), ordered)
        self.assertEqual(version_key("0.2.0.dev+a0dc551"), version_key("0.2.0.dev+b1ed662"))

    def test_the_package_number_is_a_release_number_and_matches_the_tag(self) -> None:
        self.assertRegex(jittersim.__version__, r"\d+\.\d+\.\d+\Z")
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertEqual(re.search(r'^version = "(.*)"', pyproject, re.MULTILINE).group(1), jittersim.__version__)
        if os.environ.get("GITHUB_REF_TYPE") == "tag":
            self.assertEqual(os.environ["GITHUB_REF_NAME"], "v" + jittersim.__version__)


@unittest.skipUnless(have_ssh_keygen(), "ssh-keygen signs the releases")
class DowngradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "updates"
        self.code = Path(self.directory.name) / "code"
        self.config = Path(self.directory.name) / "config"
        self.config.mkdir()
        self.code.mkdir()
        self.private, public = make_key(Path(self.directory.name) / "keys")
        updates.trust_key(public, config=self.config, root=self.root)

    def release(self, installed, staged):
        if installed is not None:
            updates.atomic_json(self.code / ".jittersim-release.json",
                                {"version": installed, "sequence": 100})
        archive = signed_release(Path(self.directory.name), self.private, version=staged, sequence=101)
        updates.stage_release(archive, root=self.root, code=self.code, config=self.config)

    def request(self, version, **options):
        request_update(version, root=self.root, code=self.code, config=self.config,
                       run=lambda *args: None, **options)

    def test_an_older_number_is_not_installed_over_a_newer_one(self) -> None:
        self.release("0.3.0", "0.2.0")
        with self.assertRaisesRegex(UpdateError, "older"):
            self.request("0.2.0")
        self.assertFalse((self.root / "request.json").exists())

    def test_a_newer_or_equal_number_installs(self) -> None:
        for installed, staged in (("0.2.0", "0.3.0"), ("0.2.0", "0.2.0"), ("0.2.0.dev+a0dc551", "0.2.0"),
                                  ("0.2.0.dev+a0dc551", "0.2.0.dev+b1ed662")):
            with self.subTest(installed=installed, staged=staged):
                self.release(installed, staged)
                self.request(staged)
                self.assertTrue((self.root / "request.json").exists())
                (self.root / "request.json").unlink()

    def test_an_installation_without_a_number_can_be_replaced(self) -> None:
        self.release("aef052e", "0.2.0")
        self.request("0.2.0")
        self.assertTrue((self.root / "request.json").exists())


@unittest.skipIf(shutil.which("git") is None, "git is required to build a release")
class BuildScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name) / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "deploy").mkdir()
        (self.repo / "pyproject.toml").write_text('[project]\nname = "jittersim"\nversion = "0.2.0"\n')
        (self.repo / "README.md").write_text("readme\n")
        (self.repo / "src" / "a.py").write_text("x = 1\n")
        (self.repo / "src/jittersim").mkdir()
        (self.repo / "src/jittersim/__init__.py").write_text('__version__ = "0.2.0"\n')
        (self.repo / "deploy" / "b.sh").write_text("echo b\n")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "first")
        self.out = Path(self.directory.name) / "out"
        self.out.mkdir()

    def git(self, *arguments):
        subprocess.run(["git", *arguments], cwd=self.repo, check=True, capture_output=True)

    def build(self, ref, target):
        return subprocess.run(
            ["sh", str(BUILD), ref, str(target)], cwd=self.repo, capture_output=True, text=True
        )

    def test_a_tagged_commit_builds_a_release_named_after_its_number(self) -> None:
        self.git("tag", "v0.2.0")
        result = self.build("v0.2.0", self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.out / "jittersim-0.2.0.tar.gz").is_file())
        self.assertIn("Release version: 0.2.0", result.stderr)

    def test_an_untagged_commit_builds_a_dev_build_named_after_its_commit(self) -> None:
        commit = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"], cwd=self.repo, capture_output=True, text=True
        ).stdout.strip()
        result = self.build("HEAD", self.out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.out / f"jittersim-0.2.0.dev+{commit}.tar.gz").is_file())
        self.assertIn(f"Release version: 0.2.0.dev+{commit}", result.stderr)

    def test_a_tag_that_disagrees_with_the_file_is_refused(self) -> None:
        self.git("tag", "v0.3.0")
        result = self.build("v0.3.0", self.out)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match", result.stderr)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_the_version_alone_can_be_asked_for(self) -> None:
        commit = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"], cwd=self.repo, capture_output=True, text=True
        ).stdout.strip()
        untagged = subprocess.run(["sh", str(BUILD), "--version", "HEAD"], cwd=self.repo,
                                  capture_output=True, text=True)
        self.assertEqual((untagged.returncode, untagged.stdout, untagged.stderr),
                         (0, f"0.2.0.dev+{commit}\n", ""))
        self.git("tag", "v0.2.0")
        tagged = subprocess.run(["sh", str(BUILD), "--version", "v0.2.0"], cwd=self.repo,
                                capture_output=True, text=True)
        self.assertEqual((tagged.returncode, tagged.stdout), (0, "0.2.0\n"))
        self.assertEqual(list(self.out.iterdir()), [])

    def test_asking_for_the_version_of_a_wrong_tag_fails(self) -> None:
        self.git("tag", "v0.3.0")
        result = subprocess.run(["sh", str(BUILD), "--version", "v0.3.0"], cwd=self.repo,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_a_named_archive_still_works(self) -> None:
        target = self.out / "custom.tar.gz"
        result = self.build("HEAD", target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(target.is_file())


class InstallerTests(unittest.TestCase):
    def test_the_installer_prints_the_number_it_installs(self) -> None:
        script = (ROOT / "deploy" / "raspberry-pi" / "install.sh").read_text()
        self.assertIn('echo "Installed JitterSim $(', script)
        self.assertIn("jittersim.__version__", script)
        self.assertLess(script.index("Installed JitterSim"), script.index("JitterSim installed. Read"))

    def test_the_release_notes_say_how_to_cut_a_release(self) -> None:
        readme = " ".join((ROOT / "deploy" / "raspberry-pi" / "README.md").read_text().split())
        for phrase in ("scripts/release.sh MAJOR.MINOR.PATCH", "git tag vMAJOR.MINOR.PATCH", "dev+",
                       "older number"):
            self.assertIn(phrase, readme)


if __name__ == "__main__":
    unittest.main()
