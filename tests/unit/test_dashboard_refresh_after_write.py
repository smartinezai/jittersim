"""Check which parts of the dashboard are read again after a save.

A save names the paths it wrote. The page reads again only the parts that can have changed, and
the page being shown. Every view that shows saved data has to be named for the path that changes
it, or it keeps showing the old data after the save. A path the table does not know reads
everything.
"""

import json
import re
import shutil
import subprocess

import pytest

from jittersim.api.server import DASHBOARD_HTML, WRITE_PATHS

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required to run the dashboard")
SCRIPT = re.search(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL).group(1)


def between(start, end):
    first = SCRIPT.index(start)
    return SCRIPT[first : SCRIPT.index(end, first)]


# Every reader refreshDashboard can start. The stubs only note that they were called.
LOADERS = (
    "loadProfiles loadHealth loadObservations loadDevicesForSchedule loadState loadGeneralProfile "
    "loadGroups loadSchedules loadTimers loadScenarios loadActivity loadProgressions loadPolicies "
    "loadConnectedDevices loadUnaffectedDevices loadUplink loadGatewayPersistence loadQuotas "
    "loadServiceAssignments loadServiceLists loadConditions loadEveryoneElse loadAdminTools "
    "loadDashboardAccessList loadTestSessions loadSettingsHistory loadWorkspace loadAccessStatus "
    "pollReleaseProgress loadUpdateCheckPreference"
).split()

HARNESS = r"""
const called = [];
let dashboardLoaded = true;
let adminTokenVerified = true;
let page = "devices";
function currentPage() { return page; }
async function loadPrivateDataPanel(id, job) { called.push("panel:" + id); return job(); }
async function show(path) { called.push("show:" + path); }
function renderGettingStarted() {}
function populateWorkflowMenus() {}
function populateAccessTargets() {}
"""

CALLED = "called.filter((name) => /^(load|poll)/.test(name))"


def program(body):
    stubs = "\n".join(
        "async function %s() { called.push(%s); }" % (name, json.dumps(name)) for name in LOADERS
    )
    return (
        HARNESS
        + stubs
        + between("function pagePollJobs(", "async function pollDashboard(")
        + between("function readOldAdminTools(", "function startEditing(")
        + between("const READ_AFTER_WRITE = [", "function readOldAdminTools(")
        + "\n(async () => {\n"
        + body
        + "\n})().catch((error) => { console.error(error); process.exit(1); });\n"
    )


def run(body):
    result = subprocess.run(
        [NODE, "-"], input=program(body), text=True, capture_output=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def reads(paths, page="devices"):
    """The readers a refresh starts after these paths were written, from this page."""

    return set(run(
        "page = %s; await refreshDashboard(readsAfterWrites(%s));"
        "console.log(JSON.stringify(%s));" % (json.dumps(page), json.dumps(paths), CALLED)
    ))


def scope(paths):
    """The names a save of these paths asks for, or None when it asks for everything."""

    names = run(
        "const names = readsAfterWrites(%s);"
        "console.log(JSON.stringify(names === null ? null : Array.from(names)));"
        % json.dumps(paths)
    )
    return None if names is None else set(names)


def test_a_full_refresh_reads_every_part():
    called = set(run("await refreshDashboard(null); console.log(JSON.stringify(%s));" % CALLED))
    # The release progress and the update preference are not part of every refresh.
    # loadAccessStatus runs inside loadPolicies.
    assert set(LOADERS) - called == {"pollReleaseProgress", "loadUpdateCheckPreference",
                                     "loadAccessStatus"}


def test_no_write_in_hand_reads_everything():
    assert scope([]) is None


@pytest.mark.parametrize("path", sorted(WRITE_PATHS))
def test_every_write_path_names_real_parts_or_reads_everything(path):
    names = scope([path])
    if names is None:
        return
    body = between("async function refreshDashboard(", "function readOldAdminTools(")
    known = set(re.findall(r'(?:run|wanted)\("([A-Za-z]+)"', body))
    assert names <= known, names - known


@pytest.mark.parametrize("path", [
    "/api/router/check", "/api/router/operation", "/api/router/status", "/api/router/wifi",
    "/api/router/wifi-credentials", "/api/router/sync-ap", "/api/router/preview-ap",
    "/api/access-point", "/api/access-point/reset-name", "/api/access/apply",
    "/api/access/device", "/api/access/disable", "/api/site-blocks", "/api/site-blocks/delete",
    "/api/policies", "/api/import", "/api/history/restore", "/api/gateway-persistence",
    "/api/update-preferences", "/api/updates/install", "/api/dashboard-access/register-self",
    "/api/connected-device-labels", "/api/connected-device-labels/delete",
])
def test_writes_that_touch_many_views_read_everything(path):
    # Router, Wi-Fi, access rules and device names show up on most pages.
    assert scope([path]) is None


def test_one_unknown_path_among_known_ones_reads_everything():
    assert scope(["/api/profiles", "/api/router/status"]) is None
    assert scope(["/api/profiles", "/api/something-new"]) is None


APPLY_PARTS = {"state", "general", "conditions", "plan", "health", "connected", "activity", "uplink"}


@pytest.mark.parametrize("path", ["/api/apply", "/api/reset", "/api/normal-access",
                                  "/api/general-profile", "/api/general-profile/stop"])
def test_starting_stopping_and_the_general_profile_refresh_what_is_running(path):
    names = scope([path])
    assert APPLY_PARTS <= names, APPLY_PARTS - names


@pytest.mark.parametrize("path", ["/api/profiles", "/api/profiles/delete"])
def test_a_profile_is_read_again_where_profiles_are_listed(path):
    names = scope([path])
    # The observed names list offers "Add to <profile>" for each saved profile.
    assert {"profiles", "general", "plan", "state", "conditions", "devices", "scenarios",
            "serviceAssignments", "observations"} <= names


@pytest.mark.parametrize("path", ["/api/unaffected-devices", "/api/unaffected-devices/delete"])
def test_protecting_a_device_refreshes_the_lists_it_changes(path):
    names = scope([path])
    # A protected device is no longer watched so the observed names change with it.
    assert {"unaffected", "connected", "plan", "state", "conditions", "observations",
            "activity"} <= names


@pytest.mark.parametrize("path", ["/api/devices", "/api/devices/delete"])
def test_saving_a_device_refreshes_every_list_that_names_devices(path):
    names = scope([path])
    assert {"devicesForSchedule", "devices", "groups", "plan", "state", "conditions",
            "connected", "unaffected"} <= names


def test_the_page_being_shown_is_always_read_too():
    # Its own readers run even when the save did not name them.
    assert "loadTestSessions" in reads(["/api/service-lists"], page="measurements")
    assert "loadTestSessions" not in reads(["/api/service-lists"], page="devices")
    assert {"loadTimers", "loadScenarios", "loadProgressions", "loadSchedules",
            "loadQuotas"} <= reads(["/api/unaffected-devices"], page="schedules")


def test_a_save_reads_far_less_than_everything():
    one = reads(["/api/service-lists"])
    assert "loadUplink" not in one and "loadGatewayPersistence" not in one
    assert len(one) < len(LOADERS) // 2


def test_diagnostics_is_read_when_shown_and_marked_old_when_skipped():
    # The diagnostics text and the website choices cost several reads and are shown on two
    # pages only. A save elsewhere skips them but remembers that they are old.
    result = run(
        "async function cycle(shown, stale) {"
        " called.length = 0; page = shown; adminToolsStale = stale;"
        " await refreshDashboard(readsAfterWrites(['/api/profiles']));"
        " return {stale: adminToolsStale, read: called.includes('loadAdminTools')}; }"
        "const result = {"
        " devices: await cycle('devices', false),"
        " diagnostics: await cycle('diagnostics', true),"
        " traffic: await cycle('traffic', true),"
        " everything: null};"
        "called.length = 0; adminToolsStale = true; await refreshDashboard(null);"
        "result.everything = {stale: adminToolsStale, read: called.includes('loadAdminTools')};"
        "console.log(JSON.stringify(result));"
    )
    assert result["devices"] == {"stale": True, "read": False}
    assert result["diagnostics"] == {"stale": False, "read": True}
    assert result["traffic"] == {"stale": False, "read": True}
    assert result["everything"] == {"stale": False, "read": True}


def test_opening_diagnostics_or_traffic_reads_the_old_tools_once():
    result = run(
        "adminToolsStale = true;"
        "readOldAdminTools('devices'); const elsewhere = [adminToolsStale, called.length];"
        "readOldAdminTools('diagnostics'); const opened = [adminToolsStale, %s];"
        "readOldAdminTools('traffic'); const again = %s;"
        "adminToolsStale = true; dashboardLoaded = false;"
        "readOldAdminTools('traffic'); const beforeLoaded = adminToolsStale;"
        "console.log(JSON.stringify({elsewhere, opened, again, beforeLoaded}));"
        % (CALLED, CALLED)
    )
    assert result["elsewhere"] == [True, 0]
    assert result["opened"] == [False, ["loadAdminTools"]]
    assert result["again"] == ["loadAdminTools"]  # still the one call from before
    assert result["beforeLoaded"] is True


def test_showing_a_page_checks_for_old_diagnostics():
    # The page changes are handled by the second script of the page.
    navigation = re.findall(r"<script>(.*?)</script>", DASHBOARD_HTML, re.DOTALL)[1]
    show = navigation[navigation.index("function showPage("):]
    assert "readOldAdminTools(id)" in show.split("\n    }\n", 1)[0]
