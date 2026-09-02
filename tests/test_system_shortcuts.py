"""
tests/test_system_shortcuts.py — actions/system_shortcuts.py, the
data-driven fast-path library (config/system_shortcuts.json) that lets
JARVIS deep-link into a specific Windows Settings page or run one
built-in read-only cmdlet, instead of falling back to slow, coordinate-
guessing UI automation for things like "check bluetooth devices" or
"what's my battery level" — the same "skip discovery when you already
know the shape of the answer" idea that already makes YouTube playback
fast, generalized to other common Windows queries.

Per this project's own established testing practice: NO test here ever
opens a real Settings window or runs a real PowerShell/netsh command —
os.startfile and subprocess.run are mocked in every test that would
otherwise trigger them. What's verified is that the CORRECT command/URI
is chosen and the output is parsed/wrapped honestly, exactly the
"implementation -> mocked call -> correct dispatch logic" standard this
project's other suites (test_youtube_browser_reuse.py, etc.) already use.

Run with:
    .venv/Scripts/python.exe -m tests.test_system_shortcuts
"""
import json
from unittest.mock import patch, MagicMock

import actions.system_shortcuts as ss
import actions.computer_settings as cs


def setup():
    ss.reload_registry()  # each test starts from a clean, real-file load


# ── registry loading ─────────────────────────────────────────────────

def test_registry_loads_and_has_both_panes_and_queries() -> None:
    setup()
    reg = ss._load_registry()
    assert len(reg.get("panes", [])) >= 10, "expected a real curated set of Settings panes"
    assert len(reg.get("queries", [])) >= 5, "expected a real curated set of read-only queries"
    print("test_registry_loads_and_has_both_panes_and_queries: PASS")


def test_every_pane_has_a_valid_ms_settings_uri() -> None:
    setup()
    reg = ss._load_registry()
    for entry in reg["panes"]:
        assert entry["uri"].startswith("ms-settings:"), entry
        assert entry.get("aliases"), f"{entry['id']} has no aliases to match against"
    print("test_every_pane_has_a_valid_ms_settings_uri: PASS")


def test_every_query_has_a_command_or_psutil_handler_and_aliases() -> None:
    setup()
    reg = ss._load_registry()
    for entry in reg["queries"]:
        has_shell_command = bool(entry.get("command"))
        has_psutil_handler = entry.get("kind") == "psutil" and bool(entry.get("handler"))
        assert has_shell_command or has_psutil_handler, entry
        assert entry.get("aliases"), f"{entry['id']} has no aliases to match against"
    print("test_every_query_has_a_command_or_psutil_handler_and_aliases: PASS")


def test_installed_apps_query_deliberately_avoids_the_win32_product_trap() -> None:
    # Get-CimInstance Win32_Product silently triggers an MSI reconfigure
    # pass on every installed package and can be slow/disruptive — a
    # well-known trap. Regression guard: if this ever creeps back in,
    # this test catches it.
    setup()
    reg = ss._load_registry()
    entry = next(e for e in reg["queries"] if e["id"] == "installed_apps")
    assert "Win32_Product" not in entry["command"]
    print("test_installed_apps_query_deliberately_avoids_the_win32_product_trap: PASS")


# ── matching (deterministic, no LLM call) ───────────────────────────────

def test_resolve_matches_bluetooth_devices_to_the_query_not_the_settings_pane() -> None:
    setup()
    kind, entry = ss.resolve("check bluetooth devices")
    assert kind == "query"
    assert entry["id"] == "bluetooth_devices"
    print("test_resolve_matches_bluetooth_devices_to_the_query_not_the_settings_pane: PASS")

def test_resolve_matches_open_bluetooth_settings_to_the_pane() -> None:
    setup()
    kind, entry = ss.resolve("open bluetooth settings")
    assert kind == "pane"
    assert entry["id"] == "bluetooth_settings"
    print("test_resolve_matches_open_bluetooth_settings_to_the_pane: PASS")

def test_resolve_matches_display_settings() -> None:
    setup()
    kind, entry = ss.resolve("display settings")
    assert kind == "pane"
    assert entry["id"] == "display"
    print("test_resolve_matches_display_settings: PASS")

def test_resolve_returns_none_for_a_weak_or_nonsense_target_rather_than_guessing() -> None:
    setup()
    assert ss.resolve("") is None
    assert ss.resolve("xyzzy plugh quux") is None
    print("test_resolve_returns_none_for_a_weak_or_nonsense_target_rather_than_guessing: PASS")


# ── panes: fire-and-forget, honest (no fabricated verification) ───────

def test_open_pane_calls_os_startfile_with_the_exact_registered_uri() -> None:
    setup()
    entry = {"id": "bluetooth_settings", "name": "Bluetooth & devices", "uri": "ms-settings:bluetooth"}
    with patch.object(ss, "_OS", "Windows"), patch.object(ss.os, "startfile") as m_start:
        result = ss.open_pane(entry)
    m_start.assert_called_once_with("ms-settings:bluetooth")
    assert "Opened Bluetooth & devices" in result
    print("test_open_pane_calls_os_startfile_with_the_exact_registered_uri: PASS")

def test_open_pane_never_crashes_if_the_uri_cannot_be_opened() -> None:
    setup()
    entry = {"id": "x", "name": "X", "uri": "ms-settings:doesnotexist"}
    with patch.object(ss, "_OS", "Windows"), patch.object(ss.os, "startfile", side_effect=OSError("no handler")):
        result = ss.open_pane(entry)
    assert "Could not open" in result
    print("test_open_pane_never_crashes_if_the_uri_cannot_be_opened: PASS")


# ── queries: real cmdlet output, wrapped in the shared Result Envelope ─

def _fake_run(stdout="", returncode=0, stderr=""):
    m = MagicMock()
    m.stdout, m.returncode, m.stderr = stdout, returncode, stderr
    return m

def test_run_query_reports_verified_success_with_real_parsed_data() -> None:
    setup()
    entry = {"id": "bluetooth_devices", "name": "Bluetooth devices",
             "command": "Get-PnpDevice ...", "empty_message": "none found"}
    payload = json.dumps([{"FriendlyName": "My Earbuds", "Status": "OK"}])
    with patch.object(ss.subprocess, "run", return_value=_fake_run(stdout=payload)):
        result = ss.run_query(entry)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "My Earbuds" in result
    print("test_run_query_reports_verified_success_with_real_parsed_data: PASS")

def test_run_query_reports_verified_failure_on_genuinely_empty_result_not_a_guess() -> None:
    setup()
    entry = {"id": "bluetooth_devices", "name": "Bluetooth devices",
             "command": "Get-PnpDevice ...", "empty_message": "no Bluetooth devices were found"}
    with patch.object(ss.subprocess, "run", return_value=_fake_run(stdout="")):
        result = ss.run_query(entry)
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "no Bluetooth devices were found" in result
    print("test_run_query_reports_verified_failure_on_genuinely_empty_result_not_a_guess: PASS")

def test_run_query_reports_inconclusive_on_unparsable_output_never_fabricates() -> None:
    setup()
    entry = {"id": "x", "name": "X", "command": "..."}
    with patch.object(ss.subprocess, "run", return_value=_fake_run(stdout="not valid json {{{")):
        result = ss.run_query(entry)
    assert result.startswith("[INCONCLUSIVE]")
    print("test_run_query_reports_inconclusive_on_unparsable_output_never_fabricates: PASS")

def test_run_query_reports_inconclusive_on_timeout_never_crashes() -> None:
    setup()
    import subprocess as real_subprocess
    entry = {"id": "x", "name": "X", "command": "..."}
    with patch.object(ss.subprocess, "run", side_effect=real_subprocess.TimeoutExpired(cmd="ps", timeout=10)):
        result = ss.run_query(entry)
    assert result.startswith("[INCONCLUSIVE]")
    assert "timed out" in result
    print("test_run_query_reports_inconclusive_on_timeout_never_crashes: PASS")

def test_run_query_parses_netsh_wifi_networks() -> None:
    setup()
    entry = {"id": "wifi_networks", "name": "Nearby Wi-Fi networks", "command": "netsh ...",
             "parser": "netsh_networks", "empty_message": "no networks"}
    sample = "SSID 1 : HomeNetwork\n    Network type : Infrastructure\nSSID 2 : CafeWiFi\n"
    with patch.object(ss.subprocess, "run", return_value=_fake_run(stdout=sample)):
        result = ss.run_query(entry)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "HomeNetwork" in result and "CafeWiFi" in result
    print("test_run_query_parses_netsh_wifi_networks: PASS")

def test_run_query_parses_netsh_current_interface() -> None:
    setup()
    entry = {"id": "wifi_current", "name": "Current Wi-Fi connection", "command": "netsh ...",
             "parser": "netsh_interface", "empty_message": "no connection"}
    sample = "    Name                   : Wi-Fi\n    State                  : connected\n    SSID                   : HomeNetwork\n    Signal                 : 87%\n"
    with patch.object(ss.subprocess, "run", return_value=_fake_run(stdout=sample)):
        result = ss.run_query(entry)
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "HomeNetwork" in result
    print("test_run_query_parses_netsh_current_interface: PASS")


# ── Phase 2: psutil-backed queries (no subprocess spawn) ────────────────

def test_disk_usage_network_info_battery_running_processes_are_psutil_kind() -> None:
    # Regression guard: if these ever silently revert to a "shell" entry,
    # this catches it even if nobody notices run_query() taking the slow
    # path again.
    setup()
    reg = ss._load_registry()
    by_id = {e["id"]: e for e in reg["queries"]}
    for qid in ("disk_usage", "network_info", "battery_status", "running_processes"):
        assert by_id[qid]["kind"] == "psutil", qid
        assert by_id[qid]["handler"] in ss._PSUTIL_HANDLERS, qid
    print("test_disk_usage_network_info_battery_running_processes_are_psutil_kind: PASS")

def test_run_query_psutil_kind_never_spawns_a_subprocess() -> None:
    # _PSUTIL_HANDLERS captures each function object at module-load time
    # (the same "dict holds the reference, patching the bare attribute
    # doesn't reach it" pitfall computer_settings.py's own ACTION_MAP
    # tests already had to account for) — so the dict ENTRY must be
    # patched, not the module-level _psutil_disk_usage name.
    setup()
    entry = {"id": "disk_usage", "name": "Disk usage", "kind": "psutil", "handler": "disk_usage",
              "empty_message": "no drives"}
    fake_handler = MagicMock(return_value=[{"Drive": "C:\\", "UsedGB": 100.0, "FreeGB": 50.0}])
    with patch.dict(ss._PSUTIL_HANDLERS, {"disk_usage": fake_handler}), \
         patch.object(ss.subprocess, "run") as m_run:
        result = ss.run_query(entry)
    m_run.assert_not_called()
    fake_handler.assert_called_once()
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "C:\\" in result
    print("test_run_query_psutil_kind_never_spawns_a_subprocess: PASS")

def test_run_query_psutil_kind_reports_failure_on_genuinely_empty_result() -> None:
    setup()
    entry = {"id": "battery_status", "name": "Battery status", "kind": "psutil",
              "handler": "battery_status", "empty_message": "no battery was found"}
    with patch.dict(ss._PSUTIL_HANDLERS, {"battery_status": MagicMock(return_value=[])}):
        result = ss.run_query(entry)
    assert result.startswith("[VERIFIED_FAILURE]")
    assert "no battery was found" in result
    print("test_run_query_psutil_kind_reports_failure_on_genuinely_empty_result: PASS")

def test_run_query_psutil_kind_never_crashes_on_a_handler_exception() -> None:
    setup()
    entry = {"id": "x", "name": "X", "kind": "psutil", "handler": "disk_usage"}
    with patch.dict(ss._PSUTIL_HANDLERS, {"disk_usage": MagicMock(side_effect=RuntimeError("boom"))}):
        result = ss.run_query(entry)
    assert result.startswith("[INCONCLUSIVE]")
    print("test_run_query_psutil_kind_never_crashes_on_a_handler_exception: PASS")

def test_psutil_disk_usage_reads_real_partitions_and_skips_unreadable_ones() -> None:
    setup()
    part_ok  = MagicMock(mountpoint="C:\\", device="C:\\")
    part_bad = MagicMock(mountpoint="D:\\", device="D:\\")  # e.g. empty optical drive
    usage = MagicMock(used=100 * 1024**3, free=50 * 1024**3)
    with patch.object(ss.psutil, "disk_partitions", return_value=[part_ok, part_bad]), \
         patch.object(ss.psutil, "disk_usage", side_effect=[usage, OSError("no media")]):
        rows = ss._psutil_disk_usage()
    assert rows == [{"Drive": "C:\\", "UsedGB": 100.0, "FreeGB": 50.0}]
    print("test_psutil_disk_usage_reads_real_partitions_and_skips_unreadable_ones: PASS")

def test_psutil_battery_status_returns_empty_not_none_when_no_battery() -> None:
    setup()
    with patch.object(ss.psutil, "sensors_battery", return_value=None):
        rows = ss._psutil_battery_status()
    assert rows == []
    print("test_psutil_battery_status_returns_empty_not_none_when_no_battery: PASS")

def test_psutil_running_processes_sorts_by_current_cpu_percent_not_cumulative_time() -> None:
    setup()
    low  = MagicMock(); low.info = {"name": "idle.exe"}; low.cpu_percent.return_value = 0.5
    high = MagicMock(); high.info = {"name": "busy.exe"}; high.cpu_percent.return_value = 40.0
    with patch.object(ss.psutil, "process_iter", return_value=[low, high]), \
         patch.object(ss.time, "sleep"):  # don't actually block the test suite
        rows = ss._psutil_running_processes()
    assert rows[0]["Name"] == "busy.exe"
    assert rows[0]["CPU%"] == 40.0
    print("test_psutil_running_processes_sorts_by_current_cpu_percent_not_cumulative_time: PASS")


# ── system_shortcut(): the public dispatcher, never guesses ────────────

def test_system_shortcut_with_no_target_asks_instead_of_guessing() -> None:
    setup()
    with patch.object(ss.os, "startfile") as m_start, patch.object(ss.subprocess, "run") as m_run:
        result = ss.system_shortcut("")
    m_start.assert_not_called()
    m_run.assert_not_called()
    assert "No target given" in result
    print("test_system_shortcut_with_no_target_asks_instead_of_guessing: PASS")

def test_system_shortcut_with_unknown_target_says_so_and_never_calls_anything() -> None:
    setup()
    with patch.object(ss, "_OS", "Windows"), \
         patch.object(ss.os, "startfile") as m_start, patch.object(ss.subprocess, "run") as m_run:
        result = ss.system_shortcut("xyzzy plugh quux nonsense")
    m_start.assert_not_called()
    m_run.assert_not_called()
    assert "No known fast-path shortcut" in result
    print("test_system_shortcut_with_unknown_target_says_so_and_never_calls_anything: PASS")

def test_system_shortcut_routes_a_pane_match_through_open_pane() -> None:
    setup()
    with patch.object(ss, "_OS", "Windows"), patch.object(ss, "open_pane", return_value="opened") as m_open, \
         patch.object(ss, "run_query") as m_query:
        result = ss.system_shortcut("display settings")
    m_open.assert_called_once()
    m_query.assert_not_called()
    assert result == "opened"
    print("test_system_shortcut_routes_a_pane_match_through_open_pane: PASS")

def test_system_shortcut_routes_a_query_match_through_run_query() -> None:
    setup()
    with patch.object(ss, "_OS", "Windows"), patch.object(ss, "run_query", return_value="[VERIFIED_SUCCESS] x") as m_query, \
         patch.object(ss, "open_pane") as m_open:
        result = ss.system_shortcut("bluetooth devices")
    m_query.assert_called_once()
    m_open.assert_not_called()
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_system_shortcut_routes_a_query_match_through_run_query: PASS")

def test_list_shortcuts_mentions_a_known_entry() -> None:
    setup()
    result = ss.list_shortcuts()
    assert "Bluetooth" in result
    print("test_list_shortcuts_mentions_a_known_entry: PASS")


# ── computer_settings.py dispatcher wiring ──────────────────────────────

def test_computer_settings_system_shortcut_action_routes_the_value_through() -> None:
    with patch.object(cs._shortcuts, "system_shortcut", return_value="[VERIFIED_SUCCESS] ok") as m:
        result = cs.computer_settings(parameters={"action": "system_shortcut", "value": "bluetooth devices"})
    m.assert_called_once_with("bluetooth devices")
    assert result == "[VERIFIED_SUCCESS] ok"
    print("test_computer_settings_system_shortcut_action_routes_the_value_through: PASS")

def test_computer_settings_list_system_shortcuts_action_calls_list_shortcuts() -> None:
    with patch.object(cs._shortcuts, "list_shortcuts", return_value="a list") as m:
        result = cs.computer_settings(parameters={"action": "list_system_shortcuts"})
    m.assert_called_once()
    assert result == "a list"
    print("test_computer_settings_list_system_shortcuts_action_calls_list_shortcuts: PASS")


if __name__ == "__main__":
    test_registry_loads_and_has_both_panes_and_queries()
    test_every_pane_has_a_valid_ms_settings_uri()
    test_every_query_has_a_command_or_psutil_handler_and_aliases()
    test_installed_apps_query_deliberately_avoids_the_win32_product_trap()
    test_resolve_matches_bluetooth_devices_to_the_query_not_the_settings_pane()
    test_resolve_matches_open_bluetooth_settings_to_the_pane()
    test_resolve_matches_display_settings()
    test_resolve_returns_none_for_a_weak_or_nonsense_target_rather_than_guessing()
    test_open_pane_calls_os_startfile_with_the_exact_registered_uri()
    test_open_pane_never_crashes_if_the_uri_cannot_be_opened()
    test_run_query_reports_verified_success_with_real_parsed_data()
    test_run_query_reports_verified_failure_on_genuinely_empty_result_not_a_guess()
    test_run_query_reports_inconclusive_on_unparsable_output_never_fabricates()
    test_run_query_reports_inconclusive_on_timeout_never_crashes()
    test_run_query_parses_netsh_wifi_networks()
    test_run_query_parses_netsh_current_interface()
    test_disk_usage_network_info_battery_running_processes_are_psutil_kind()
    test_run_query_psutil_kind_never_spawns_a_subprocess()
    test_run_query_psutil_kind_reports_failure_on_genuinely_empty_result()
    test_run_query_psutil_kind_never_crashes_on_a_handler_exception()
    test_psutil_disk_usage_reads_real_partitions_and_skips_unreadable_ones()
    test_psutil_battery_status_returns_empty_not_none_when_no_battery()
    test_psutil_running_processes_sorts_by_current_cpu_percent_not_cumulative_time()
    test_system_shortcut_with_no_target_asks_instead_of_guessing()
    test_system_shortcut_with_unknown_target_says_so_and_never_calls_anything()
    test_system_shortcut_routes_a_pane_match_through_open_pane()
    test_system_shortcut_routes_a_query_match_through_run_query()
    test_list_shortcuts_mentions_a_known_entry()
    test_computer_settings_system_shortcut_action_routes_the_value_through()
    test_computer_settings_list_system_shortcuts_action_calls_list_shortcuts()
    print("\nAll system_shortcuts tests passed.")
