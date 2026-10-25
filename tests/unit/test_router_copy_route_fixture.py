"""The missing-helper browser fixture snapshots health before route ownership."""

import ast
import copy
import threading
from pathlib import Path
from types import SimpleNamespace


def test_delayed_snapshot_finishes_before_handler_and_no_fetch_during_teardown():
    source = ast.parse(Path('tests/browser/router_copy.py').read_text())
    function = next(node for node in source.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == 'missing_helper_health_handler')
    namespace = {'ADMIN_TOKEN': 'synthetic-admin'}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<fixture>', 'exec'), namespace)
    original = {'helper_configured': True, 'private': {'preserved': [1, 2]},
                'other_health_field': 'unchanged'}
    entered, release = threading.Event(), threading.Event()
    calls, handlers, errors = [], [], []

    def get(url, *, headers):
        calls.append((url, headers))
        entered.set()
        assert release.wait(5), 'controlled snapshot was never released'
        return SimpleNamespace(ok=True, json=lambda: copy.deepcopy(original))

    def prepare():
        try:
            handlers.append(namespace['missing_helper_health_handler'](
                SimpleNamespace(request=SimpleNamespace(get=get)), 'http://synthetic'))
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=prepare)
    worker.start()
    try:
        assert entered.wait(5)
        assert not handlers  # No route can be registered with an unfinished snapshot.
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert not errors, errors
    assert calls == [('http://synthetic/api/health',
                      {'Authorization': 'Bearer synthetic-admin'})]
    replies = []
    route = SimpleNamespace(fulfill=lambda **reply: replies.append(reply))
    # Repeated callbacks, including one already dispatched before deregistration,
    # only fulfill the owned route: no fetch/continuation or teardown network work.
    handlers[0](route)
    handlers[0](route)
    expected = copy.deepcopy(original)
    expected['helper_configured'] = False
    assert replies == [{'json': expected}, {'json': expected}]
    assert original['helper_configured'] is True
    assert len(calls) == 1
