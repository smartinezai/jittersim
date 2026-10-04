"""Reject packet checks that could replace live host rules."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.integration import linux_exemption_packets


@pytest.mark.parametrize(
    ("separate_namespace", "interfaces"),
    [(False, ["lo"]), (True, ["lo", "wlan0"])],
)
def test_packet_check_refuses_the_host_and_populated_namespaces(
    monkeypatch, separate_namespace, interfaces
):
    monkeypatch.setattr(Path, "exists", lambda path: False)
    monkeypatch.setattr(
        Path,
        "stat",
        lambda path: SimpleNamespace(
            st_ino=2 if separate_namespace and str(path) == "/proc/self/ns/net" else 1
        ),
    )
    monkeypatch.setattr(
        linux_exemption_packets.subprocess,
        "check_output",
        lambda *args, **kwargs: json.dumps([{"ifname": name} for name in interfaces]),
    )
    mutations = []
    monkeypatch.setattr(linux_exemption_packets, "run", lambda *args: mutations.append(args))
    with pytest.raises(RuntimeError):
        linux_exemption_packets.main()
    assert not mutations
