"""
tests/test_task_engine_j7.py — J7 (Terminal & File System) of the
JARVIS execution-architecture mission.

Inspection finding, stated up front: file_controller.py already
implemented essentially the entire safe-file-operation surface (list/
create_file/create_folder/delete/move/copy/rename/read/write/find/
largest/disk_usage/organize_desktop/info) with a REAL path-safety
boundary (_is_safe_path(), restricted to the user's own home folder) and
already-reversible deletion (send2trash, never permanent). What J7
actually added: (1) Result-Envelope tagging + real post-condition
verification for every one of those functions (file_controller.py); (2)
the delete confirmation gate, reusing the EXISTING is_consequential()/
is_confirmed() classifier (result_envelope.py's own
_CONSEQUENTIAL_ACTION_NAMES gained "delete", same tier as shutdown/
restart); (3) a new `file_system` domain in task_engine.py (the first
real FAMILY_RESOURCE member) with a deliberately conservative objective
parser that requires a SPECIFIC, extractable name for anything that
modifies/deletes — a vague request never resolves to a broad target;
(4) a small, safe route() fix for filename-extension-shaped objectives
("delete notes.txt") that contain no bare "file"/"folder" noun.

"Terminal" (arbitrary shell execution) was deliberately NOT built —
inspection found no existing safe mechanism for it anywhere in this
repository (actions/desktop.py's own generated-code sandbox is a
restricted PYAUTOGUI executor, not a command runner, and explicitly
forbids subprocess calls) — see task_engine.py's own J7 section comment.

Per this project's own established convention: real, isolated temp
directories are used for anything that touches the actual filesystem
(never the user's real home tree, never a mock standing in for the
whole test) — a standard tempfile.TemporaryDirectory() lives under the
user's own home directory on Windows, so it passes _is_safe_path()
without needing to patch anything (see test_file_controller.py's own
convention note). No test in this file destroys anything outside its
own disposable temp directory; no test invokes a real shell command
(none exists to invoke).

Run with:
    .venv/Scripts/python.exe -m tests.test_task_engine_j7
"""
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import actions.task_engine as te
import actions.file_controller as fc
from actions import result_envelope as _envelope


def _task(**params):
    return te.execute_task(parameters=params)


def _handlers(**overrides):
    return patch.dict(te._HANDLERS, overrides)


# ── Registry / routing ───────────────────────────────────────────────────

def test_route_resolves_file_related_objectives_to_file_system() -> None:
    assert te.route("list files on my desktop") == "file_system"
    assert te.route("find files named invoice") == "file_system"
    assert te.route("create a new folder called Projects") == "file_system"
    print("test_route_resolves_file_related_objectives_to_file_system: PASS")


def test_route_unsupported_objective_is_rejected_honestly() -> None:
    assert te.route("what is the capital of France") is None
    print("test_route_unsupported_objective_is_rejected_honestly: PASS")


def test_route_disk_space_query_still_reaches_system_shortcut_not_file_system() -> None:
    # Real regression risk this test guards: file_system's own domain
    # deliberately excludes "disk"/"storage" (already owned by
    # system_shortcut's real, working disk-space query) — this proves
    # that boundary actually holds, not just that it's commented.
    assert te.route("check disk space") == "system_shortcut"
    assert te.route("how much storage do I have left") == "system_shortcut"
    print("test_route_disk_space_query_still_reaches_system_shortcut_not_file_system: PASS")


def test_route_filename_extension_hint_reaches_file_system_without_a_bare_noun() -> None:
    # "delete notes.txt from my desktop" contains no bare "file"/"folder"
    # word at all -- the fix this proves.
    assert te.route("delete notes.txt from my desktop") == "file_system"
    assert te.route("rename report.pdf to summary.pdf") == "file_system"
    print("test_route_filename_extension_hint_reaches_file_system_without_a_bare_noun: PASS")


def test_route_filename_hint_never_false_positives_on_decimals_or_ip_addresses() -> None:
    assert te.route("set my volume to 12.5 percent") == "system_volume"
    assert te.route("what is my ip address 192.168.1.1") != "file_system"
    print("test_route_filename_hint_never_false_positives_on_decimals_or_ip_addresses: PASS")


# ── Parser: conservative by construction ────────────────────────────────

def test_parser_refuses_a_vague_delete_target() -> None:
    assert te._parse_file_action("delete the old files") is None
    assert te._parse_file_action("delete everything in this folder") is None
    assert te._parse_file_action("clean up this folder") is None
    print("test_parser_refuses_a_vague_delete_target: PASS")


def test_parser_accepts_a_specific_delete_target() -> None:
    params = te._parse_file_action("delete notes.txt from my desktop")
    assert params == {"action": "delete", "path": "desktop", "name": "notes.txt"}
    print("test_parser_accepts_a_specific_delete_target: PASS")


def test_parser_refuses_rename_without_both_a_name_and_a_destination() -> None:
    assert te._parse_file_action("rename my file") is None
    print("test_parser_refuses_rename_without_both_a_name_and_a_destination: PASS")


def test_parser_read_only_list_needs_no_specific_name() -> None:
    assert te._parse_file_action("list my downloads") == {"action": "list", "path": "downloads"}
    print("test_parser_read_only_list_needs_no_specific_name: PASS")


# ── Safety tiers ──────────────────────────────────────────────────────────

def test_read_only_actions_never_require_confirmation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = fc.list_files(tmp)
    assert result.startswith("[VERIFIED_SUCCESS]")
    print("test_read_only_actions_never_require_confirmation: PASS")


def test_delete_is_consequential_shutdown_restart_tier() -> None:
    assert _envelope.is_consequential(action_name="delete") is True
    assert _envelope.is_consequential(action_name="shutdown") is True
    assert _envelope.is_consequential(action_name="list") is False
    print("test_delete_is_consequential_shutdown_restart_tier: PASS")


# ── Confirmation ──────────────────────────────────────────────────────────

def test_delete_without_confirmation_returns_confirmation_required_and_never_touches_disk() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "notes.txt"
        target.write_text("hello")
        result = fc.file_controller(parameters={"action": "delete", "path": tmp, "name": "notes.txt"})
        assert result.startswith("[CONFIRMATION_REQUIRED]")
        assert target.exists()   # never actually deleted
    print("test_delete_without_confirmation_returns_confirmation_required_and_never_touches_disk: PASS")


def test_delete_confirmed_true_actually_proceeds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "notes.txt"
        target.write_text("hello")
        result = fc.file_controller(parameters={
            "action": "delete", "path": tmp, "name": "notes.txt", "confirmed": True,
        })
        assert result.startswith("[VERIFIED_SUCCESS]")
        assert not target.exists()
    print("test_delete_confirmed_true_actually_proceeds: PASS")


def test_confirmed_false_string_does_not_bypass_confirmation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "notes.txt"
        target.write_text("hello")
        result = fc.file_controller(parameters={
            "action": "delete", "path": tmp, "name": "notes.txt", "confirmed": "false",
        })
        assert result.startswith("[CONFIRMATION_REQUIRED]")
        assert target.exists()
    print("test_confirmed_false_string_does_not_bypass_confirmation: PASS")


def test_task_engine_delete_objective_requires_confirmation_end_to_end() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "notes.txt"
        target.write_text("hello")
        with patch.object(te, "_extract_file_shortcut", return_value=tmp):
            result = _task(objective='delete "notes.txt"')
        assert result.startswith("[CONFIRMATION_REQUIRED]")
        assert target.exists()
    print("test_task_engine_delete_objective_requires_confirmation_end_to_end: PASS")


def test_task_engine_delete_objective_with_confirmed_true_actually_deletes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "notes.txt"
        target.write_text("hello")
        with patch.object(te, "_extract_file_shortcut", return_value=tmp):
            result = _task(objective='delete "notes.txt"', confirmed=True)
        assert result.startswith("[VERIFIED_SUCCESS]")
        assert not target.exists()
    print("test_task_engine_delete_objective_with_confirmed_true_actually_deletes: PASS")


# ── Blocking ──────────────────────────────────────────────────────────────

def test_out_of_sandbox_path_is_blocked_not_just_denied() -> None:
    result = fc.list_files("C:/Windows/System32" if fc._OS == "Windows" else "/etc")
    assert result.startswith("[BLOCKED]")
    print("test_out_of_sandbox_path_is_blocked_not_just_denied: PASS")


def test_deleting_a_protected_top_level_folder_itself_is_blocked() -> None:
    # Deleting the Desktop/Downloads/etc. folder ITSELF is permanently
    # refused, regardless of confirmed=true -- not a confirmation gate.
    result = fc.file_controller(parameters={"action": "delete", "path": "desktop", "confirmed": True})
    assert result.startswith("[BLOCKED]")
    print("test_deleting_a_protected_top_level_folder_itself_is_blocked: PASS")


def test_path_traversal_attempt_is_blocked() -> None:
    result = fc.list_files("../../../../../../../../Windows")
    assert result.startswith("[BLOCKED]")
    print("test_path_traversal_attempt_is_blocked: PASS")


# ── Verification ──────────────────────────────────────────────────────────

def test_successful_create_folder_is_verified_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = fc.create_folder(tmp, name="NewFolder")
        assert result.startswith("[VERIFIED_SUCCESS]")
        assert (Path(tmp) / "NewFolder").is_dir()
    print("test_successful_create_folder_is_verified_success: PASS")


def test_move_of_a_nonexistent_source_is_verified_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = fc.move_file(tmp, name="does_not_exist.txt", destination=tmp)
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_move_of_a_nonexistent_source_is_verified_failure: PASS")


def test_write_content_mismatch_after_write_is_never_reported_as_success() -> None:
    # Result-spoofing guard: even if the underlying write call itself
    # doesn't raise, a readback that DOESN'T match what was requested
    # (simulating a concurrent/interfering change, or a truncated write)
    # must never be upgraded to VERIFIED_SUCCESS.
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "out.txt"
        real_read_text = Path.read_text

        def _tampered_read_text(self, *a, **k):
            if self == target:
                return "something else entirely"
            return real_read_text(self, *a, **k)

        with patch.object(Path, "read_text", _tampered_read_text):
            result = fc.write_file(tmp, name="out.txt", content="expected content")
    assert not result.startswith("[VERIFIED_SUCCESS]")
    assert result.startswith("[VERIFIED_FAILURE]")
    print("test_write_content_mismatch_after_write_is_never_reported_as_success: PASS")


def test_delete_that_silently_fails_to_actually_remove_the_file_is_never_success() -> None:
    # Result-spoofing guard for delete: if send2trash "succeeds" (no
    # exception) but the target somehow still exists afterward, this must
    # be reported honestly, never as VERIFIED_SUCCESS.
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "stubborn.txt"
        target.write_text("x")
        with patch("actions.file_controller.send2trash.send2trash", return_value=None):
            result = fc.delete_file(tmp, name="stubborn.txt")
    assert not result.startswith("[VERIFIED_SUCCESS]")
    assert result.startswith("[INCONCLUSIVE]")
    print("test_delete_that_silently_fails_to_actually_remove_the_file_is_never_success: PASS")


# ── Recovery / terminal-state safety (J5 compatibility) ─────────────────

def test_file_system_has_no_recovery_chain_entry() -> None:
    assert "file_system" not in te._RECOVERY_CHAIN
    print("test_file_system_has_no_recovery_chain_entry: PASS")


def test_blocked_file_result_is_never_recovered_even_if_a_hop_is_configured() -> None:
    m_fs = MagicMock(return_value="[BLOCKED] Access denied: outside sandbox.")
    m_other = MagicMock()
    with patch.object(te, "_RECOVERY_CHAIN", {"file_system": "system_shortcut"}), \
         _handlers(file_system=m_fs, system_shortcut=m_other):
        result = _task(objective="list files on my desktop")
    m_other.assert_not_called()
    assert result.startswith("[BLOCKED]")
    print("test_blocked_file_result_is_never_recovered_even_if_a_hop_is_configured: PASS")


def test_confirmation_required_file_result_is_never_recovered_even_if_a_hop_is_configured() -> None:
    m_fs = MagicMock(return_value="[CONFIRMATION_REQUIRED] this will delete notes.txt.")
    m_other = MagicMock()
    with patch.object(te, "_RECOVERY_CHAIN", {"file_system": "system_shortcut"}), \
         _handlers(file_system=m_fs, system_shortcut=m_other):
        result = _task(objective="delete notes.txt from my desktop")
    m_other.assert_not_called()
    assert result.startswith("[CONFIRMATION_REQUIRED]")
    print("test_confirmation_required_file_result_is_never_recovered_even_if_a_hop_is_configured: PASS")


# ── J6 compatibility: no unnecessary inspection ─────────────────────────

def test_file_system_has_no_inspect_config_entry() -> None:
    # "list Desktop does not need a screenshot" -- J6's own example.
    assert "file_system" not in te._INSPECT_CONFIG
    print("test_file_system_has_no_inspect_config_entry: PASS")


# ── J4/task-engine integration: real, non-mocked end-to-end ─────────────

def test_real_end_to_end_list_through_execute_task() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.txt").write_text("x")
        with patch.object(te, "_extract_file_shortcut", return_value=tmp):
            result = _task(objective="list my files")
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "a.txt" in result
    print("test_real_end_to_end_list_through_execute_task: PASS")


def test_multi_objective_task_combining_file_system_and_another_domain() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(te, "_extract_file_shortcut", return_value=tmp), \
             _handlers(system_volume=MagicMock(return_value="[VERIFIED_SUCCESS] volume is now 40%.")):
            result = _task(objectives=["list my files", "set my volume to 40 percent"])
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "volume is now 40" in result
    print("test_multi_objective_task_combining_file_system_and_another_domain: PASS")


def test_single_objective_file_result_is_still_byte_for_byte_the_raw_result() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(te, "_extract_file_shortcut", return_value=tmp):
            result = _task(objective="list my files")
        # No J4 report-synthesis wrapper for a single objective -- compared
        # against a second, direct call while the same temp dir still exists.
        expected = fc.list_files(tmp)
    assert result == expected
    print("test_single_objective_file_result_is_still_byte_for_byte_the_raw_result: PASS")


# ── Pre-J8-hardening fix: natural-language subpath/name extraction ─────
# Confirmed real live-usage gap: _extract_file_shortcut() only ever
# returned the BARE shortcut ("desktop"), silently discarding any
# subfolder/name the user actually stated, and _extract_file_name()
# could not extract an unquoted multi-word name next to "folder". Every
# test below exercises the REAL, unpatched _parse_file_action() —
# _extract_file_shortcut() is deliberately NEVER mocked here (that
# exact mistake is what let the original bug through J7's own tests).
# Generalization is proven with MULTIPLE distinct names, not just the
# one example ("Consumer behaviour") the bug was originally reported with.

def test_list_preserves_an_explicit_slash_subpath() -> None:
    assert te._parse_file_action("list files in Desktop/Consumer behaviour") == {
        "action": "list", "path": "desktop/Consumer behaviour",
    }
    assert te._parse_file_action("list Desktop/Consumer behaviour") == {
        "action": "list", "path": "desktop/Consumer behaviour",
    }
    print("test_list_preserves_an_explicit_slash_subpath: PASS")


def test_list_preserves_an_unquoted_multi_word_named_folder() -> None:
    assert te._parse_file_action("list the Consumer behaviour folder on my desktop") == {
        "action": "list", "path": "desktop/Consumer behaviour",
    }
    print("test_list_preserves_an_unquoted_multi_word_named_folder: PASS")


def test_find_extracts_an_unquoted_multi_word_folder_name_as_the_search_term() -> None:
    # Per this fix's own required example: find's PATH stays the search
    # ROOT (the shortcut) -- the named folder becomes the search NAME,
    # not appended to the path (unlike list's direct-navigation semantics).
    assert te._parse_file_action("find Consumer behaviour folder on my desktop") == {
        "action": "find", "path": "desktop", "name": "Consumer behaviour",
    }
    assert te._parse_file_action("find the Consumer behaviour folder") == {
        "action": "find", "path": "desktop", "name": "Consumer behaviour",
    }
    print("test_find_extracts_an_unquoted_multi_word_folder_name_as_the_search_term: PASS")


def test_extraction_generalizes_to_other_multi_word_names_not_just_one_example() -> None:
    # The fix must not be hardcoded to "Consumer behaviour".
    assert te._parse_file_action("list Desktop/My Important Folder") == {
        "action": "list", "path": "desktop/My Important Folder",
    }
    assert te._parse_file_action("list files in Desktop/Project Documents") == {
        "action": "list", "path": "desktop/Project Documents",
    }
    assert te._parse_file_action("find the Tax Documents folder on my desktop") == {
        "action": "find", "path": "desktop", "name": "Tax Documents",
    }
    assert te._parse_file_action("find the Research Project folder") == {
        "action": "find", "path": "desktop", "name": "Research Project",
    }
    print("test_extraction_generalizes_to_other_multi_word_names_not_just_one_example: PASS")


def test_create_folder_and_rename_also_benefit_from_the_shared_name_extraction() -> None:
    assert te._parse_file_action("create the Tax Documents folder on my desktop") == {
        "action": "create_folder", "path": "desktop", "name": "Tax Documents",
    }
    assert te._parse_file_action("rename the Consumer behaviour folder to Consumer Research") == {
        "action": "rename", "path": "desktop", "name": "Consumer behaviour", "new_name": "Consumer Research",
    }
    print("test_create_folder_and_rename_also_benefit_from_the_shared_name_extraction: PASS")


def test_existing_quoted_name_behavior_is_unaffected() -> None:
    assert te._parse_file_action('find "My Custom Report" on my desktop') == {
        "action": "find", "path": "desktop", "name": "My Custom Report",
    }
    print("test_existing_quoted_name_behavior_is_unaffected: PASS")


def test_existing_bare_shortcut_behavior_is_unaffected() -> None:
    assert te._parse_file_action("list my downloads") == {"action": "list", "path": "downloads"}
    assert te._parse_file_action("list my desktop") == {"action": "list", "path": "desktop"}
    print("test_existing_bare_shortcut_behavior_is_unaffected: PASS")


def test_existing_file_extension_behavior_is_unaffected() -> None:
    assert te._parse_file_action("delete notes.txt from my desktop") == {
        "action": "delete", "path": "desktop", "name": "notes.txt",
    }
    print("test_existing_file_extension_behavior_is_unaffected: PASS")


def test_referencing_a_bare_shortcut_as_a_folder_is_not_double_applied() -> None:
    # "the Desktop folder" must not become path="desktop/Desktop" --
    # a name that IS just a shortcut on its own is recognized as such,
    # not treated as a subfolder of itself.
    assert te._parse_file_action("list files in the Desktop folder") == {
        "action": "list", "path": "desktop",
    }
    print("test_referencing_a_bare_shortcut_as_a_folder_is_not_double_applied: PASS")


def test_conservative_delete_policy_is_unaffected_by_the_new_extraction() -> None:
    # The words "folder"/"files" are present in both, but neither names
    # anything specific -- must still honestly refuse, exactly as J7
    # already established.
    assert te._parse_file_action("delete the old files") is None
    assert te._parse_file_action("delete everything in this folder") is None
    print("test_conservative_delete_policy_is_unaffected_by_the_new_extraction: PASS")


def test_real_end_to_end_list_with_a_real_natural_language_subpath() -> None:
    # The full chain, objective -> _parse_file_action() (REAL, unpatched
    # -- this is the exact function under test) -> file_controller ->
    # real filesystem, with a real nested subfolder matching a genuine
    # multi-word name. Only file_controller._get_desktop() is redirected
    # to a disposable temp dir (the one existing seam _resolve_path()
    # itself already uses to answer "where is the real Desktop" --
    # there's no way to exercise a "Desktop" shortcut at all without
    # either this or the user's real Desktop); the task_engine parser
    # under test, and file_controller's own path-joining/safety-check
    # logic, run completely for real and unpatched.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "Consumer behaviour").mkdir()
        (tmp_path / "Consumer behaviour" / "report.docx").write_text("x")
        with patch.object(fc, "_get_desktop", return_value=tmp_path):
            result = te.execute_task(parameters={"objective": "list files in Desktop/Consumer behaviour"})
    assert result.startswith("[VERIFIED_SUCCESS]")
    assert "report.docx" in result
    print("test_real_end_to_end_list_with_a_real_natural_language_subpath: PASS")


def test_extraction_never_bypasses_existing_path_safety() -> None:
    # Whatever string the new subpath/name extraction produces for a
    # traversal-laced objective, it must still resolve to somewhere
    # file_controller._is_safe_path() (completely unmodified by this fix)
    # actually allows -- proving no bypass was introduced, regardless of
    # whether the extraction carries the ".." fragment through at all (it
    # doesn't, in practice: the extraction regexes exclude "." from a
    # captured subpath, so a traversal sequence never survives into the
    # constructed path string in the first place -- a real, additional
    # layer of defense, not a substitute for _is_safe_path() itself).
    params = te._parse_file_action("list files in Desktop/../../../../../../Windows")
    assert params is not None
    resolved = fc._resolve_path(params["path"])
    assert fc._is_safe_path(resolved)
    print("test_extraction_never_bypasses_existing_path_safety: PASS")


def _run() -> None:
    test_route_resolves_file_related_objectives_to_file_system()
    test_route_unsupported_objective_is_rejected_honestly()
    test_route_disk_space_query_still_reaches_system_shortcut_not_file_system()
    test_route_filename_extension_hint_reaches_file_system_without_a_bare_noun()
    test_route_filename_hint_never_false_positives_on_decimals_or_ip_addresses()
    test_parser_refuses_a_vague_delete_target()
    test_parser_accepts_a_specific_delete_target()
    test_parser_refuses_rename_without_both_a_name_and_a_destination()
    test_parser_read_only_list_needs_no_specific_name()
    test_read_only_actions_never_require_confirmation()
    test_delete_is_consequential_shutdown_restart_tier()
    test_delete_without_confirmation_returns_confirmation_required_and_never_touches_disk()
    test_delete_confirmed_true_actually_proceeds()
    test_confirmed_false_string_does_not_bypass_confirmation()
    test_task_engine_delete_objective_requires_confirmation_end_to_end()
    test_task_engine_delete_objective_with_confirmed_true_actually_deletes()
    test_out_of_sandbox_path_is_blocked_not_just_denied()
    test_deleting_a_protected_top_level_folder_itself_is_blocked()
    test_path_traversal_attempt_is_blocked()
    test_successful_create_folder_is_verified_success()
    test_move_of_a_nonexistent_source_is_verified_failure()
    test_write_content_mismatch_after_write_is_never_reported_as_success()
    test_delete_that_silently_fails_to_actually_remove_the_file_is_never_success()
    test_file_system_has_no_recovery_chain_entry()
    test_blocked_file_result_is_never_recovered_even_if_a_hop_is_configured()
    test_confirmation_required_file_result_is_never_recovered_even_if_a_hop_is_configured()
    test_file_system_has_no_inspect_config_entry()
    test_real_end_to_end_list_through_execute_task()
    test_multi_objective_task_combining_file_system_and_another_domain()
    test_single_objective_file_result_is_still_byte_for_byte_the_raw_result()
    test_list_preserves_an_explicit_slash_subpath()
    test_list_preserves_an_unquoted_multi_word_named_folder()
    test_find_extracts_an_unquoted_multi_word_folder_name_as_the_search_term()
    test_extraction_generalizes_to_other_multi_word_names_not_just_one_example()
    test_create_folder_and_rename_also_benefit_from_the_shared_name_extraction()
    test_existing_quoted_name_behavior_is_unaffected()
    test_existing_bare_shortcut_behavior_is_unaffected()
    test_existing_file_extension_behavior_is_unaffected()
    test_referencing_a_bare_shortcut_as_a_folder_is_not_double_applied()
    test_conservative_delete_policy_is_unaffected_by_the_new_extraction()
    test_real_end_to_end_list_with_a_real_natural_language_subpath()
    test_extraction_never_bypasses_existing_path_safety()
    print("\nAll task_engine_j7 tests passed.")


if __name__ == "__main__":
    _run()
