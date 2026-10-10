"""Enabled primary buttons keep readable white text over their whole highlight gradient."""

import re

from jittersim.api.server import DASHBOARD_HTML

STYLE = re.search(r"<style>(.*?)</style>", DASHBOARD_HTML, re.DOTALL).group(1)
TOKENS = dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-f]{6});", STYLE))


def luminance(value):
    channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    red, green, blue = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first, second):
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def lighten(value, alpha):
    return "#" + "".join(
        "%02x" % round(int(value[index : index + 2], 16) * (1 - alpha) + 255 * alpha) for index in (1, 3, 5)
    )


def test_primary_buttons_keep_four_and_a_half_to_one_with_white_text_at_rest_and_on_hover():
    for rule in (r"button\.primary", r"button\.primary:hover:not\(:disabled\)"):
        bodies = re.findall(rule + r"\s*\{([^{}]*)\}", STYLE)
        # The last rule wins, and the refresh layer is the last one in the page.
        body = bodies[-1]
        highlight = float(re.search(r"linear-gradient\(180deg, rgba\(255, 255, 255, ([0-9.]+)\)", body).group(1))
        token = re.search(r"var\((--bg-primary[a-z-]*)\)\s*;", body).group(1)
        text = TOKENS["--tx-on-primary"]
        brightest = lighten(TOKENS[token], highlight)
        assert contrast(text, TOKENS[token]) >= 4.5, rule
        assert contrast(text, brightest) >= 4.5, (rule, brightest)
