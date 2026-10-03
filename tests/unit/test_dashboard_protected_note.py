from jittersim.api.server import DASHBOARD_HTML


def test_protected_rows_say_traffic_is_not_monitored():
    assert "Protected from all profiles and quotas · Traffic is not monitored" in DASHBOARD_HTML


def test_traffic_discovery_says_protected_devices_never_appear():
    assert "Protected devices are never monitored and never appear here." in DASHBOARD_HTML
