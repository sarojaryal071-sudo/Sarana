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


def test_every_query_has_a_command_and_aliases() -> None:
    setup()
    reg = ss._load_registry()
    for entry in reg["queries"]:
        assert entry.get("command"), entry
        assert entry.get("aliases"), f"{entry['id']} has no aliases to match against"
    print("test_every_query_has_a_command_and_aliases: PASS")


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
    test_every_query_has_a_command_and_aliases()
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
    test_system_shortcut_with_no_target_asks_instead_of_guessing()
    test_system_shortcut_with_unknown_target_says_so_and_never_calls_anything()
    test_system_shortcut_routes_a_pane_match_through_open_pane()
    test_system_shortcut_routes_a_query_match_through_run_query()
    test_list_shortcuts_mentions_a_known_entry()
    test_computer_settings_system_shortcut_action_routes_the_value_through()
    test_computer_settings_list_system_shortcuts_action_calls_list_shortcuts()
    print("\nAll system_shortcuts tests passed.")
