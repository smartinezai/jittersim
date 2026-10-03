import gzip
import http.client
import threading
from contextlib import contextmanager
from unittest.mock import patch

from jittersim.api import server as api
from jittersim.config.storage import ConfigRepository


@contextmanager
def dashboard(name):
    repository = ConfigRepository(':memory:')
    server = api.ControlServer(('127.0.0.1', 0), repository, local_name=name)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        worker.join(5)
        server.server_close()
        repository.close()
        assert not worker.is_alive()


def request(server, path='/', headers=None):
    connection = http.client.HTTPConnection(*server.server_address[:2], timeout=3)
    try:
        connection.request('GET', path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_two_actual_server_pages_keep_their_bytes_gzip_and_validators_after_eviction():
    with dashboard('alpha.local') as alpha, dashboard('bravo.local') as bravo:
        a_plain = request(alpha)
        b_plain = request(bravo)
        assert a_plain[0] == b_plain[0] == 200
        assert a_plain[2] == alpha.dashboard_page_html.encode()
        assert b_plain[2] == bravo.dashboard_page_html.encode()
        assert b'alpha.local' in a_plain[2] and b'bravo.local' not in a_plain[2]
        assert b'bravo.local' in b_plain[2] and b'alpha.local' not in b_plain[2]
        assert a_plain[1]['ETag'] != b_plain[1]['ETag']
        assert a_plain[1]['ETag'].startswith('W/"')
        for current, plain, other in ((alpha, a_plain, b_plain), (bravo, b_plain, a_plain)):
            status, headers, body = request(current, headers={'Accept-Encoding': 'gzip'})
            assert status == 200 and gzip.decompress(body) == plain[2]
            assert headers['ETag'] == plain[1]['ETag']
            assert int(headers['Content-Length']) == len(body)
            assert request(current, headers={'If-None-Match': other[1]['ETag']})[0] == 200
            status, headers, body = request(current, headers={
                'If-None-Match': plain[1]['ETag'][2:], 'Accept-Encoding': 'gzip'})
            assert status == 304 and body == b''
            assert headers['Vary'] == 'Accept-Encoding'
            assert headers['Cache-Control'] == 'no-cache'
            assert headers['X-Content-Type-Options'] == 'nosniff'
            assert 'Content-Security-Policy' in headers
        assert len(api._DASHBOARD_PAGE_CACHE) == 1


def test_eviction_between_root_body_selection_and_send_still_negotiates_own_instance():
    with dashboard('alpha.local') as alpha, dashboard('bravo.local') as bravo:
        original = alpha.response_for

        def evict(path, **values):
            selected = original(path, **values)
            api.dashboard_page_bytes(bravo.dashboard_page_html)
            return selected

        with patch.object(alpha, 'response_for', side_effect=evict):
            status, headers, body = request(alpha, headers={'Accept-Encoding': 'gzip'})
            assert status == 200 and headers['Content-Encoding'] == 'gzip'
            assert gzip.decompress(body) == alpha.dashboard_page_html.encode()
            status, repeated, body = request(alpha, headers={'If-None-Match': headers['ETag']})
            assert status == 304 and body == b'' and repeated['ETag'] == headers['ETag']


def test_instance_gzip_refusal_and_json_no_store_are_preserved():
    with dashboard('alpha.local') as current:
        for accept in ('*;q=1, gzip;q=0', 'gzip;q=0, *;q=1'):
            status, headers, body = request(current, headers={'Accept-Encoding': accept})
            assert status == 200 and 'Content-Encoding' not in headers
            assert body == current.dashboard_page_html.encode()
        status, headers, body = request(current, '/api/health', {'Accept-Encoding': 'gzip'})
        assert status == 200 and headers['Cache-Control'] == 'no-store'
        assert 'ETag' not in headers and 'Content-Encoding' not in headers
        assert body.startswith(b'{')
