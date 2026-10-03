"""The actual HTTPS command must bound reads and never repeat an uncertain install."""

import time
from unittest.mock import patch

import pytest

from jittersim.networking.helper import HelperClientError
from tests.unit.test_deploy_over_https import (
    base_arguments, https, lab as shared_lab, logged, pytestmark as transport_pytestmark,
)

https_lab = shared_lab
pytestmark = transport_pytestmark


@pytest.mark.parametrize("answer", ["late-success", "read-failure"])
def test_install_wait_counts_slow_status_reads_and_refuses_late_success(https_lab, answer):
    lab = https_lab
    helper = lab["pi"].server.helper_client
    original = helper.release_status
    post_install_reads = []

    def slow_status():
        if lab["pi"].installs:
            post_install_reads.append(time.monotonic())
            # The install POST's answer uses one status snapshot too.
            if len(post_install_reads) > 1:
                time.sleep(3)
                if answer == "read-failure":
                    raise HelperClientError("test status read failed")
        return original()

    with patch.object(helper, "release_status", slow_status):
        started = time.monotonic()
        result = https(lab, *base_arguments(lab), env={"JITTERSIM_INSTALL_WAIT_SECONDS": "1"})
        elapsed = time.monotonic() - started
    assert result.returncode == 1, result.stdout + result.stderr
    assert "after 1 seconds" in result.stderr
    assert elapsed < 2.5, "a status read must be cancelled within the remaining allowance"
    assert len(lab["pi"].installs) == 1
    assert logged(lab).count("/api/updates/install") == 1


def test_uncertain_install_answer_is_never_posted_again(https_lab):
    lab = https_lab
    helper = lab["pi"].server.helper_client
    original = helper.install_release

    def uncertain_install(**arguments):
        original(**arguments)
        raise HelperClientError("test lost the install answer")

    with patch.object(helper, "install_release", uncertain_install):
        result = https(lab, *base_arguments(lab))
    assert result.returncode == 1
    assert len(lab["pi"].installs) == 1
    assert logged(lab).count("/api/updates/install") == 1


def test_missing_clock_runtime_stops_before_upload_or_install(https_lab):
    lab = https_lab
    runtime = lab["tmp"] / "bin" / "python3"
    runtime.write_text("#!/bin/sh\nexit 127\n")
    runtime.chmod(0o755)
    result = https(lab, *base_arguments(lab))
    assert result.returncode == 1
    assert "Python 3" in result.stderr
    assert lab["pi"].installs == []
    assert "/api/updates/upload" not in logged(lab)


@pytest.mark.parametrize("allowance", ["-1", "0", "abc", "09", "999999999999999999999"])
def test_invalid_wait_allowance_stops_before_mutation(https_lab, allowance):
    lab = https_lab
    result = https(lab, *base_arguments(lab), env={"JITTERSIM_INSTALL_WAIT_SECONDS": allowance})
    assert result.returncode == 1
    assert "1 to 900" in result.stderr
    assert "/api/updates/upload" not in logged(lab)
    assert lab["pi"].installs == []
