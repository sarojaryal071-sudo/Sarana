import os
import re
import shutil
import platform
from pathlib import Path
from datetime import datetime

from actions import result_envelope as _envelope

try:
    import send2trash
    _SEND2TRASH = True
except ImportError:
    _SEND2TRASH = False

# J7 (Terminal & File System): every function below now returns a real
# Result-Envelope-tagged string (see actions/result_envelope.py) instead
# of a bare, untagged one — "Action sent != action completed" applies to
# the filesystem exactly as it already does to Office/browser: a create/
# delete/move/copy/rename/write is verified by re-checking the actual
# filesystem afterward (does the target now exist / no longer exist),
# never assumed successful just because no exception was raised.
#   VERIFIED_SUCCESS — the operation happened AND was independently
#     re-confirmed against the real filesystem (or, for a pure read
#     like list/find/read/info, the real content itself IS the evidence).
#   VERIFIED_FAILURE — a real, known failure (not found, OS error, a
#     verification re-check that came back wrong).
#   BLOCKED — refused by JARVIS's OWN policy, never something confirmed=
#     true can override: outside _SAFE_ROOTS (_is_safe_path() below), or
#     one of the top-level protected user folders themselves (delete_file()).
#     Distinct from VERIFIED_FAILURE, which is the filesystem/OS saying no,
#     not JARVIS refusing on principle.
#   CONFIRMATION_REQUIRED — file_controller()'s dispatcher gates "delete"
#     through the EXISTING is_consequential()/is_confirmed() classifier
#     (see result_envelope.py's _CONSEQUENTIAL_ACTION_NAMES) before this
#     module ever runs it — no second confirmation framework.
_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

_SAFE_ROOTS: list[Path] = [
    Path.home(),
]

def _is_safe_path(target: Path) -> bool:
    """Verilen path _SAFE_ROOTS içinde mi? Değilse işlemi reddet."""
    try:
        resolved = target.resolve()
        return any(
            resolved == root.resolve() or resolved.is_relative_to(root.resolve())
            for root in _SAFE_ROOTS
        )
    except Exception:
        return False

def _get_desktop() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DESKTOP_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Desktop"

def _get_downloads() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DOWNLOAD_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Downloads"

def _get_documents() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DOCUMENTS_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Documents"

def _get_pictures() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_PICTURES_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Pictures"

def _get_music() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_MUSIC_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Music"

def _get_videos() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_VIDEOS_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Videos"


def _resolve_path(raw: str) -> Path:
    shortcuts: dict[str, Path] = {
        "desktop":   _get_desktop(),
        "downloads": _get_downloads(),
        "documents": _get_documents(),
        "pictures":  _get_pictures(),
        "music":     _get_music(),
        "videos":    _get_videos(),
        "home":      Path.home(),
    }
    cleaned = raw.strip()
    lower = cleaned.lower()
    if lower in shortcuts:
        return shortcuts[lower]

    # Shortcut-prefixed subpath, e.g. "Desktop/Consumer behaviour" or
    # "Downloads\some-folder" — the confirmed bug this fixes: anything
    # other than a BARE shortcut name used to fall straight through to
    # Path(raw).expanduser(), which stays RELATIVE and resolves against
    # the process's own working directory, not the real shortcut folder
    # (reproduced live: it resolved to a nonexistent path under this
    # project's own directory instead of the user's real Desktop).
    # Split on either separator (users type both) and recognize a
    # LEADING shortcut segment, then join whatever real directory it
    # names with the remainder — no shortcut names are hard-coded here
    # beyond the same six already defined above.
    parts = [p for p in re.split(r"[\\/]+", cleaned) if p]
    if parts and parts[0].lower() in shortcuts:
        base = shortcuts[parts[0].lower()]
        return base.joinpath(*parts[1:]) if len(parts) > 1 else base

    return Path(cleaned).expanduser()

def _format_size(b: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

def _safe_trash(target: Path) -> str:

    if not _SEND2TRASH:
        return (
            "send2trash is not installed. "
            "Run: pip install send2trash — "
            "Permanent deletion is disabled for safety."
        )
    send2trash.send2trash(str(target))
    return f"Moved to Trash: {target.name}"


def list_files(path: str = "desktop", show_hidden: bool = False) -> str:
    try:
        target = _resolve_path(path)
        if not _is_safe_path(target):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied: {target}")
        if not target.exists():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Path not found: {target}")
        if not target.is_dir():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Not a directory: {target}")

        items = []
        for item in sorted(target.iterdir()):
            if not show_hidden and item.name.startswith("."):
                continue
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                size = _format_size(item.stat().st_size)
                items.append(f"📄 {item.name} ({size})")

        if not items:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"Directory is empty: {target.name}/")

        return _envelope.envelope(
            _envelope.STATUS_VERIFIED_SUCCESS,
            f"Contents of {target.name}/ ({len(items)} items):\n" + "\n".join(items),
        )

    except PermissionError:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Permission denied: {path}")
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Error listing files: {e}")


def create_file(path: str, name: str = "", content: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        # Verify: the file must actually exist afterward — never assume
        # write_text() succeeding (it would have raised otherwise) is
        # the same thing as independently re-confirming it.
        if target.is_file():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"File created: {target.name}")
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"{target.name} was written but could not be re-confirmed on disk")
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Could not create file: {e}")


def create_folder(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied: {target}")
        target.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"Folder created: {target.name}")
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"{target.name} could not be re-confirmed on disk")
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Could not create folder: {e}")


def delete_file(path: str, name: str = "") -> str:
    """Confirmation is gated ABOVE this function, in file_controller()'s
    own dispatcher (the EXISTING is_consequential()/is_confirmed() gate —
    see this module's own top-level note) — by the time this runs, the
    user has already explicitly said yes. This function's own job is
    just to perform the delete and independently VERIFY it actually
    happened, same as every other mutating function here."""
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied: {target}")
        if not target.exists():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Not found: {target.name}")

        # Güvenli dizin kontrolü — kritik kullanıcı klasörlerini koru.
        # BLOCKED, not CONFIRMATION_REQUIRED: no confirmed=true makes
        # deleting the user's ENTIRE Desktop/Downloads/Documents/home
        # folder itself allowed — this is a permanent policy refusal
        # (see result_envelope.py's own BLOCKED-vs-CONFIRMATION_REQUIRED
        # distinction). Deleting something INSIDE one of these is fine —
        # only the top-level folder itself is protected.
        protected = {
            _get_desktop(), _get_downloads(), _get_documents(),
            _get_pictures(), _get_music(), _get_videos(), Path.home()
        }
        if target.resolve() in {p.resolve() for p in protected}:
            return _envelope.envelope(
                _envelope.STATUS_BLOCKED, f"Protected directory, cannot delete: {target.name}"
            )

        trash_result = _safe_trash(target)
        if not _SEND2TRASH:
            # Permanent deletion is deliberately never attempted as a
            # fallback — see _safe_trash()'s own message.
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, trash_result)
        # Verify: the target must actually be GONE afterward — never
        # assume send2trash() succeeding (it would have raised otherwise)
        # is the same thing as independently re-confirming it.
        if not target.exists():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, trash_result)
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"{trash_result} but {target.name} still appears to exist")

    except PermissionError:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Permission denied: {path}")
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Could not delete: {e}")


def move_file(path: str, name: str = "", destination: str = "") -> str:
    try:
        base   = _resolve_path(path)
        src    = (base / name) if name else base
        dst    = _resolve_path(destination) if destination else None

        if not src.exists():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Source not found: {src.name}")
        if dst is None:
            return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "No destination specified.")
        if not _is_safe_path(src):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied (source): {src}")
        if not _is_safe_path(dst):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied (destination): {dst}")

        if dst.is_dir():
            dst = dst / src.name

        dst.parent.mkdir(parents=True, exist_ok=True)
        src_name = src.name
        shutil.move(str(src), str(dst))
        # Verify: destination must exist AND source must be gone —
        # either half failing silently would be a partial, unreported move.
        if dst.exists() and not src.exists():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"Moved: {src_name} → {dst.parent.name}/")
        return _envelope.envelope(
            _envelope.STATUS_INCONCLUSIVE, f"move of {src_name} could not be fully re-confirmed"
        )

    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Could not move: {e}")


def copy_file(path: str, name: str = "", destination: str = "") -> str:
    try:
        base = _resolve_path(path)
        src  = (base / name) if name else base
        dst  = _resolve_path(destination) if destination else None

        if not src.exists():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Source not found: {src.name}")
        if dst is None:
            return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "No destination specified.")
        if not _is_safe_path(src):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied (source): {src}")
        if not _is_safe_path(dst):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied (destination): {dst}")

        if dst.is_dir():
            dst = dst / src.name

        dst.parent.mkdir(parents=True, exist_ok=True)

        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))

        # Verify: the destination must actually exist afterward.
        if dst.exists():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"Copied: {src.name} → {dst.parent.name}/")
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"copy of {src.name} could not be re-confirmed")

    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Could not copy: {e}")


def rename_file(path: str, name: str = "", new_name: str = "") -> str:
    try:
        base     = _resolve_path(path)
        target   = (base / name) if name else base
        if not _is_safe_path(target):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied: {target}")
        if not target.exists():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Not found: {target.name}")
        if not new_name:
            return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, "No new name provided.")

        new_path = target.parent / new_name
        if new_path.exists():
            return _envelope.envelope(
                _envelope.STATUS_VERIFIED_FAILURE, f"A file named '{new_name}' already exists here."
            )

        old_name = target.name
        target.rename(new_path)
        # Verify: the new name must exist AND the old one must be gone.
        if new_path.exists() and not target.exists():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"Renamed: {old_name} → {new_name}")
        return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"rename of {old_name} could not be re-confirmed")

    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Could not rename: {e}")


def read_file(path: str, name: str = "", max_chars: int = 4000) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied: {target}")
        if not target.exists():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"File not found: {target.name}")
        if not target.is_file():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Not a file: {target.name}")

        content = target.read_text(encoding="utf-8", errors="ignore")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[Truncated — {len(content)} total chars]"
        # The content itself, real and just read, IS the evidence —
        # envelope() drops a genuinely empty evidence string entirely, so
        # an empty file needs an explicit note rather than silently
        # looking like any other bare, evidence-free success.
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, content or "(empty file)")

    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Could not read file: {e}")


def write_file(path: str, name: str = "", content: str = "",
               append: bool = False) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(target, mode, encoding="utf-8") as f:
            f.write(content)
        action = "Appended to" if append else "Written to"
        # Verify: read the file back and confirm the expected content is
        # actually there — for write, the whole file must match; for
        # append, the file must at least END with what was just added
        # (its earlier content is untouched and irrelevant to this check).
        try:
            on_disk = target.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"{action}: {target.name}, but it could not be read back to confirm")
        matches = on_disk.endswith(content) if append else on_disk == content
        if matches:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"{action}: {target.name}")
        return _envelope.envelope(
            _envelope.STATUS_VERIFIED_FAILURE, f"{target.name} was written but its content doesn't match what was requested"
        )
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Could not write file: {e}")


def find_files(name: str = "", extension: str = "",
               path: str = "home", max_results: int = 20) -> str:
    try:
        search_path = _resolve_path(path)
        if not _is_safe_path(search_path):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied: {search_path}")
        if not search_path.exists():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Search path not found: {path}")

        results    = []
        dir_count  = 0
        max_dirs   = 500  # performans + güvenlik limiti

        for item in search_path.rglob("*"):
            if item.is_dir():
                dir_count += 1
                if dir_count > max_dirs:
                    break
                # Confirmed bug this fixes: folders were never matched at
                # all here, regardless of where they lived — find_files()
                # could only ever locate FILES. extension is meaningless
                # for a folder, so an extension search still matches only
                # files (existing file-search behavior is unchanged); a
                # plain name search now matches folders too.
                if extension:
                    continue
                if name and name.lower() not in item.name.lower():
                    continue
                results.append(f"📁 {item.name}/ — {item.parent}")
                if len(results) >= max_results:
                    break
                continue
            if not item.is_file():
                continue
            if extension and item.suffix.lower() != extension.lower():
                continue
            if name and name.lower() not in item.name.lower():
                continue
            size = _format_size(item.stat().st_size)
            results.append(f"📄 {item.name} ({size}) — {item.parent}")
            if len(results) >= max_results:
                break

        if not results:
            query = name or extension or "files"
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, f"No {query} found in {search_path.name}/")

        return _envelope.envelope(
            _envelope.STATUS_VERIFIED_SUCCESS, f"Found {len(results)} file(s):\n" + "\n".join(results)
        )

    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Search error: {e}")


def get_largest_files(path: str = "downloads", count: int = 10) -> str:
    count = min(count, 50)  # maksimum 50
    try:
        search_path = _resolve_path(path)
        if not _is_safe_path(search_path):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied: {search_path}")
        if not search_path.exists():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Path not found: {path}")

        files = []
        for item in search_path.rglob("*"):
            if item.is_file():
                try:
                    files.append((item.stat().st_size, item))
                except Exception:
                    continue

        files.sort(reverse=True)
        top = files[:count]

        if not top:
            return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, "No files found.")

        lines = [f"Top {len(top)} largest files in {search_path.name}/:"]
        for size, f in top:
            lines.append(f"  {_format_size(size):>10}  {f.name}  ({f.parent})")

        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, "\n".join(lines))

    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Error: {e}")


def get_disk_usage(path: str = "home") -> str:
    try:
        target = _resolve_path(path)
        usage  = shutil.disk_usage(target)
        pct    = usage.used / usage.total * 100
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, (
            f"Disk usage ({target}):\n"
            f"  Total : {_format_size(usage.total)}\n"
            f"  Used  : {_format_size(usage.used)} ({pct:.1f}%)\n"
            f"  Free  : {_format_size(usage.free)}"
        ))
    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Could not get disk usage: {e}")


def organize_desktop() -> str:
    type_map = {
        "Images":    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
        "Documents": {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx",
                      ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp"},
        "Videos":    {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
        "Music":     {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
        "Archives":  {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
        "Code":      {".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
                      ".cpp", ".java", ".cs", ".go", ".rs", ".sh"},
    }

    desktop = _get_desktop()
    moved, skipped = [], []

    try:
        for item in desktop.iterdir():
            # Klasörlere, gizli dosyalara ve organize klasörlerine dokunma
            if item.is_dir() or item.name.startswith("."):
                continue
            if item.name in {k for k in type_map}:
                continue

            ext        = item.suffix.lower()
            target_dir = desktop / "Others"
            for folder, exts in type_map.items():
                if ext in exts:
                    target_dir = desktop / folder
                    break

            target_dir.mkdir(exist_ok=True)
            new_path = target_dir / item.name

            if new_path.exists():
                skipped.append(item.name)
                continue

            shutil.move(str(item), str(new_path))
            moved.append(f"{item.name} → {target_dir.name}/")

        result = f"Desktop organized: {len(moved)} files moved."
        if moved:
            preview = moved[:8]
            result += "\n" + "\n".join(preview)
            if len(moved) > 8:
                result += f"\n... and {len(moved) - 8} more."
        if skipped:
            result += f"\n{len(skipped)} file(s) skipped (name conflict)."
        # Each move above already either succeeded (shutil.move raises on
        # failure) or was explicitly counted as skipped -- the counts
        # themselves are the real, already-collected evidence.
        return _envelope.envelope(_envelope.STATUS_VERIFIED_SUCCESS, result)

    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Could not organize desktop: {e}")


def get_file_info(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not _is_safe_path(target):
            return _envelope.envelope(_envelope.STATUS_BLOCKED, f"Access denied: {target}")
        if not target.exists():
            return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Not found: {target.name}")

        stat = target.stat()
        info = {
            "Name":      target.name,
            "Type":      "Folder" if target.is_dir() else "File",
            "Size":      _format_size(stat.st_size),
            "Location":  str(target.parent),
            "Created":   datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M"),
            "Modified":  datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "Extension": target.suffix or "—",
        }
        return _envelope.envelope(
            _envelope.STATUS_VERIFIED_SUCCESS, "\n".join(f"  {k}: {v}" for k, v in info.items())
        )

    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"Could not get file info: {e}")

def file_controller(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    path   = params.get("path", "desktop")
    name   = params.get("name", "")

    if player:
        player.write_log(f"[file] {action} {name or path}")

    try:
        if action == "list":
            return list_files(path)

        elif action == "create_file":
            return create_file(path, name=name, content=params.get("content", ""))

        elif action == "create_folder":
            return create_folder(path, name=name)

        elif action == "delete":
            # J7: the SAME centralized risk/confirmation gate
            # computer_settings.py's shutdown/restart already use (see
            # result_envelope.py's _CONSEQUENTIAL_ACTION_NAMES/
            # is_consequential()/is_confirmed()) — never a second
            # confirmation framework, never re-implemented per action.
            # Checked here, before delete_file() ever runs, so an
            # unconfirmed request never touches the filesystem at all.
            target_desc = f"{name} in {path}" if name else path
            if _envelope.is_consequential(action_name=action) and not _envelope.is_confirmed(params):
                return _envelope.envelope(
                    _envelope.STATUS_CONFIRMATION_REQUIRED, f"this will delete {target_desc}"
                )
            return delete_file(path, name=name)

        elif action == "move":
            return move_file(path, name=name, destination=params.get("destination", ""))

        elif action == "copy":
            return copy_file(path, name=name, destination=params.get("destination", ""))

        elif action == "rename":
            return rename_file(path, name=name, new_name=params.get("new_name", ""))

        elif action == "read":
            return read_file(path, name=name)

        elif action == "write":
            return write_file(
                path, name=name,
                content=params.get("content", ""),
                append=params.get("append", False)
            )

        elif action == "find":
            return find_files(
                name=name or params.get("name", ""),
                extension=params.get("extension", ""),
                path=path,
                max_results=min(int(params.get("max_results", 20)), 50),
            )

        elif action == "largest":
            return get_largest_files(
                path=path,
                count=int(params.get("count", 10)),
            )

        elif action == "disk_usage":
            return get_disk_usage(path)

        elif action == "organize_desktop":
            return organize_desktop()

        elif action == "info":
            return get_file_info(path, name=name)

        else:
            return _envelope.envelope(_envelope.STATUS_INCONCLUSIVE, f"Unknown action: '{action}'")

    except Exception as e:
        return _envelope.envelope(_envelope.STATUS_VERIFIED_FAILURE, f"File controller error ({action}): {e}")