import re
import unittest

from jittersim.api.server import DASHBOARD_HTML

# Browsers compile a pattern attribute with the regular expression v flag. In that mode a hyphen
# inside a character class is only allowed as the range operator between two characters. Any
# other hyphen must be escaped, or the browser drops the whole pattern and accepts any value.
_CLASS = re.compile(r"\[([^\]]*)\]")


def unescaped_hyphens(pattern: str) -> list:
    """Return the character classes that hold a hyphen the v flag would reject."""

    masked = re.sub(r"\\.", "x", pattern)
    bad = []
    for body in _CLASS.findall(masked):
        for index, character in enumerate(body):
            if character != "-":
                continue
            is_range = 0 < index < len(body) - 1 and "-" not in (body[index - 1], body[index + 1])
            if not is_range:
                bad.append("[" + body + "]")
                break
    return bad


class DashboardPatternTests(unittest.TestCase):
    def test_the_scan_flags_a_hyphen_the_v_flag_rejects(self) -> None:
        self.assertEqual(unescaped_hyphens("[a-z0-9][a-z0-9_-]{0,63}"), ["[a-z0-9_-]"])
        self.assertEqual(unescaped_hyphens("[-a-z]"), ["[-a-z]"])
        self.assertEqual(unescaped_hyphens("[a-z0-9][a-z0-9_-]{0,63}[0-9-]"), ["[a-z0-9_-]", "[0-9-]"])

    def test_the_scan_accepts_escaped_hyphens_and_ranges(self) -> None:
        for pattern in (
            r"[a-z0-9][a-z0-9_\-]{0,63}",
            "[a-z]",
            "[0-9a-f]{2}",
            r"\d{1,2}-\d{2}",
            r"[\-a-z]",
        ):
            with self.subTest(pattern=pattern):
                self.assertEqual(unescaped_hyphens(pattern), [])

    def test_every_pattern_in_the_served_page_escapes_its_hyphens(self) -> None:
        patterns = re.findall(r"""\spattern=(?:"([^"]*)"|'([^']*)')""", DASHBOARD_HTML)
        values = [double or single for double, single in patterns]

        # The page has at least the nine ID fields, so an empty scan means the scan is broken.
        self.assertGreaterEqual(len(values), 9)
        for value in values:
            with self.subTest(pattern=value):
                self.assertEqual(unescaped_hyphens(value), [])


if __name__ == "__main__":
    unittest.main()
