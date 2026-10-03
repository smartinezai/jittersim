"""One retry intent stays outside the network lock and never replays."""

import tempfile
import threading
from pathlib import Path

import pytest

from jittersim import updates
from jittersim.networking.helper import HelperClientError
from tests.unit.test_panic_restart_latch import helper

RETRY_ID = "a" * 64
STATUS = {"phase": "queued", "request_id": "b" * 32, "request_kind": "install"}


@pytest.fixture
def socket_directory():
    with tempfile.TemporaryDirectory(prefix="js70-", dir="/tmp") as temporary:
        yield Path(temporary).resolve()


def test_actual_retry_request_can_wait_without_occupying_panic_lock(socket_directory, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls, answers = [], []

    def retry(identifier):
        calls.append(identifier)
        entered.set()
        assert release.wait(5)
        return STATUS

    monkeypatch.setattr(updates, "retry_update", retry, raising=False)
    with helper(socket_directory) as (_, client):
        worker = threading.Thread(
            target=lambda: answers.append(client.retry_update(retry_id=RETRY_ID))
        )
        worker.start()
        try:
            assert entered.wait(2)
            assert client._with_timeout(0.5).normal_access()["ok"] is True
            release.set()
            worker.join(3)
            assert not worker.is_alive()
            assert answers == [{"ok": True, **STATUS}]
            assert calls == [RETRY_ID]
        finally:
            release.set()
            worker.join(3)


def test_known_backend_refusal_survives_transport_for_http_conflict(socket_directory, monkeypatch):
    def refuse(identifier):
        raise updates.UpdateError("the failed update changed; check it again before retrying")

    monkeypatch.setattr(updates, "retry_update", refuse, raising=False)
    with helper(socket_directory) as (_, client):
        with pytest.raises(HelperClientError) as error:
            client.retry_update(retry_id=RETRY_ID)
        assert error.value.reason == "update-retry-refused"


def test_missing_backend_refuses_without_using_ordinary_update_install(
    socket_directory, monkeypatch
):
    monkeypatch.delattr(updates, "retry_update", raising=False)
    with helper(socket_directory) as (_, client):
        with pytest.raises(HelperClientError, match="needs an update"):
            client.retry_update(retry_id=RETRY_ID)


def test_invalid_or_extended_helper_retry_payload_never_calls_backend(
    socket_directory, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        updates, "retry_update", lambda identifier: calls.append(identifier), raising=False
    )
    with helper(socket_directory) as (_, client):
        for payload in (
            {"action": "update-retry", "retry_id": True},
            {"action": "update-retry", "retry_id": RETRY_ID, "version": "new"},
        ):
            with pytest.raises(HelperClientError) as error:
                client._request(payload)
            assert error.value.reason == "update-retry-refused"
    assert calls == []


def test_lost_retry_response_does_not_send_the_write_again(tmp_path, monkeypatch):
    calls = []

    def sent(payload, **kwargs):
        calls.append(payload)
        raise HelperClientError("could not contact the networking helper")

    from jittersim.networking.helper import HelperClient

    client = HelperClient(socket_path=tmp_path / "unused")
    monkeypatch.setattr(client, "_request", sent)
    with pytest.raises(HelperClientError):
        client.retry_update(retry_id=RETRY_ID)
    assert calls == [{"action": "update-retry", "retry_id": RETRY_ID}]


@pytest.mark.parametrize(
    "field,value", [("request_id", None), ("request_id", "g" * 32), ("request_kind", "other")]
)
def test_unconfirmed_backend_identity_never_reports_a_successful_retry(
    socket_directory, monkeypatch, field, value
):
    calls = []

    def retry(identifier):
        calls.append(identifier)
        return {**STATUS, field: value}

    monkeypatch.setattr(updates, "retry_update", retry, raising=False)
    with helper(socket_directory) as (_, client):
        with pytest.raises(HelperClientError, match="could not be confirmed"):
            client.retry_update(retry_id=RETRY_ID)
    assert calls == [RETRY_ID]
