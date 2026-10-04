"""Nobody on the test Wi-Fi may read the product name before the admin token is accepted.

The page is public: the same bytes go to everyone who opens the Pi's address. So the name must be
absent from the markup, the title, every text the page can show, the headers and the error
bodies. After the token is accepted the page asks a private route for the name.
"""

import http.client
import json
import re
import threading

import pytest

from jittersim.api.server import ControlServer, DASHBOARD_HTML
from jittersim.config.storage import ConfigRepository

NAME = re.compile("jitter\\s*sim", re.IGNORECASE)
TOKEN = "neutral-page-token"

# Identifiers the script may still hold because the person never reads them on the page: the
# browser storage keys, the file names offered for saving after sign-in, and the format tag of a
# measurement file. Each is checked as a whole so a new sentence cannot hide behind them.
ALLOWED_IDENTIFIERS = (
    re.compile(r"^jittersim\.remembered\.[a-z-]+$"),
    re.compile(r"^jittersim\.card\.$"),
    re.compile(r"^jittersim-[a-z-]+\.(?:db|json|md)$"),
    re.compile(r"^jittersim-measurement$"),
)


def markup_without_code(page: str) -> str:
    page = re.sub(r"<script>.*?</script>", "", page, flags=re.DOTALL)
    page = re.sub(r"<style>.*?</style>", "", page, flags=re.DOTALL)
    return re.sub(r"<!--.*?-->", "", page, flags=re.DOTALL)


def script_text() -> str:
    return "\n".join(re.findall(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL))


@pytest.fixture()
def served():
    repository = ConfigRepository(":memory:")
    server = ControlServer(("127.0.0.1", 0), repository, admin_token=TOKEN)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()

    def get(path, headers=None, method="GET"):
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response, response.read()
        finally:
            connection.close()

    try:
        yield get
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        repository.close()


def test_the_title_and_every_visible_part_of_the_page_are_neutral():
    title = re.search(r"<title>(.*?)</title>", DASHBOARD_HTML).group(1)
    assert title == "Sign in"
    assert not NAME.search(markup_without_code(DASHBOARD_HTML))


def test_the_script_holds_the_name_only_in_identifiers_nobody_reads_on_the_page():
    strings = re.findall(r"""(["'`])((?:\\.|(?!\1).)*?)\1""", script_text())
    found = [text for _, text in strings if NAME.search(text)]
    leaks = [text for text in found if not any(rule.match(text) for rule in ALLOWED_IDENTIFIERS)]
    assert not leaks, leaks
    # Comments in the script travel to the browser too.
    outside_strings = re.sub(r"""(["'`])(?:\\.|(?!\1).)*?\1""", "", script_text())
    assert not NAME.search(outside_strings), NAME.findall(outside_strings)


def test_the_public_page_and_its_headers_do_not_name_the_product(served):
    response, body = served("/")
    assert response.status == 200
    assert not NAME.search(markup_without_code(body.decode("utf-8")))
    assert not NAME.search("\n".join(f"{k}: {v}" for k, v in response.getheaders()))
    assert response.getheader("Server") == "server"


def test_the_public_health_check_and_the_sign_in_refusal_do_not_name_the_product(served):
    for path, expected in (("/api/health", 200), ("/api/profiles", 401), ("/nothing-here", 404)):
        response, body = served(path)
        assert response.status == expected, path
        assert not NAME.search(body.decode("utf-8")), path
        assert not NAME.search("\n".join(f"{k}: {v}" for k, v in response.getheaders())), path


def test_an_unsupported_request_gets_a_neutral_error_page(served):
    response, body = served("/", method="DELETE")
    assert response.status >= 400
    assert not NAME.search(body.decode("utf-8", "replace"))
    assert not NAME.search("\n".join(f"{k}: {v}" for k, v in response.getheaders()))


def test_the_favicon_is_a_small_neutral_icon_and_not_a_404(served):
    response, body = served("/favicon.ico")
    assert response.status == 200
    assert response.getheader("Content-Type") == "image/x-icon"
    # An icon file starts with a reserved zero and the type 1.
    assert body[:4] == b"\x00\x00\x01\x00"
    assert 0 < len(body) < 4096
    assert not NAME.search(body.decode("latin-1"))


def test_the_name_is_only_served_after_the_token_is_accepted(served):
    response, _ = served("/api/brand")
    assert response.status == 401
    response, body = served("/api/brand", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status == 200
    brand = json.loads(body)
    assert brand["name"] == "JitterSim"
    assert brand["command"] == "jittersim"
    assert brand["default_ssid"] == "TestNet"
