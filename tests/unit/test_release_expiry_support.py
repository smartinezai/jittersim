"""The expiry fixture must own its terminal and bound a waiting child group."""

import subprocess

import pytest

from tests.release_expiry_support import run_owned


@pytest.mark.parametrize("terminal", [False, True])
def test_owned_runner_uses_only_its_own_terminal(terminal):
    result = run_owned(
        ["sh", "-c", 'if [ -t 0 ]; then printf terminal; else printf headless; fi'],
        terminal=terminal, capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert result.stdout == ("terminal" if terminal else "headless")


@pytest.mark.parametrize("terminal", [False, True])
def test_owned_runner_bounds_waiting_descendants(terminal):
    with pytest.raises(subprocess.TimeoutExpired) as stopped:
        run_owned(["sh", "-c", "sleep 30 & printf child-started; wait"], terminal=terminal,
                  capture_output=True, timeout=2)
    assert stopped.value.output == b"child-started"
