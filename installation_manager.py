"""Per-user installation support for SCS tool launchers (.scstool)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from PyQt5.QtGui import QImage
from tool_manager import Tool, ToolRegistry, app_data_path


LAUNCHER_EXTENSION = ".scstool"
LAUNCHER_FORMAT = "scs-tools-manager-launcher-v1"
PROG_ID = "SCS.Tools.Manager.scstool"
UID_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


def _launcher_dir() -> Path:
    path = app_data_path() / "tools" / "installed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _records_path() -> Path:
    return _launcher_dir() / "installations.json"


def _load_records() -> dict:
    try:
        value = json.loads(_records_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _save_records(records: dict) -> None:
    target = _records_path(); handle, temporary = tempfile.mkstemp(prefix="scs_install_", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output: json.dump(records, output, ensure_ascii=False, indent=2)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            try: os.unlink(temporary)
            except OSError: pass


def _safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", value).strip(". ")[:80]
    return name or "SCS Tool"


def launcher_uid(uid: str) -> str:
    """Return the mandatory hexadecimal UID stored in every .scstool file."""
    if not isinstance(uid, str) or not uid: raise ValueError("invalid tool UID")
    return uid.lower() if UID_RE.fullmatch(uid) else hashlib.sha256(uid.encode("utf-8")).hexdigest()


def launcher_path(tool: Tool) -> Path:
    return _launcher_dir() / f"{_safe_name(tool.name)}-{launcher_uid(tool.uid)[:12]}{LAUNCHER_EXTENSION}"


def read_launcher(path: Path) -> dict:
    try: data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError): raise ValueError("invalid launcher")
    uid = data.get("uid") if isinstance(data, dict) else None
    if not isinstance(uid, str) or not UID_RE.fullmatch(uid): raise ValueError("invalid UID")
    if data.get("format") != LAUNCHER_FORMAT: raise ValueError("invalid launcher format")
    return data


def is_installed(uid: str) -> bool:
    try: key = launcher_uid(uid)
    except ValueError: return False
    record = _load_records().get(key)
    if not isinstance(record, dict): return False
    path = Path(str(record.get("launcher", "")))
    try: return path.is_file() and read_launcher(path).get("uid", "").lower() == key
    except ValueError: return False


def uninstall_tool(uid: str) -> None:
    """Remove only manager-created launchers and shortcuts for one tool."""
    try: key = launcher_uid(uid)
    except ValueError: return
    records = _load_records(); record = records.pop(key, None)
    if not isinstance(record, dict): return
    paths = [record.get("launcher"), *(record.get("shortcuts") or [])]
    for value in paths:
        try:
            path = Path(str(value))
            if path.is_file(): path.unlink()
        except OSError: pass
    _save_records(records)


def _main_command() -> str:
    if getattr(sys, "frozen", False): return f'"{sys.executable}" "%1"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.is_file() else Path(sys.executable)
    return f'"{interpreter}" "{Path(__file__).with_name("main.py")}" "%1"'


def _icon_path() -> Path:
    return Path(getattr(sys, "_MEIPASS", app_data_path())) / "assets" / "icon.ico"


def _tool_icon_path(tool: Tool) -> Path:
    """Persist a ZIP's PNG/ICO icon as an ICO suitable for a Windows shortcut."""
    icon_name = tool.data.get("icon")
    if not isinstance(icon_name, str) or not icon_name or Path(icon_name).name != icon_name: return _icon_path()
    try:
        with zipfile.ZipFile(tool.archive) as archive: raw = archive.read(icon_name)
        image = QImage();
        if not image.loadFromData(raw): return _icon_path()
        destination = _launcher_dir() / "icons" / f"{launcher_uid(tool.uid)}.ico"; destination.parent.mkdir(parents=True, exist_ok=True)
        return destination if image.save(str(destination), "ICO") else _icon_path()
    except (OSError, KeyError, zipfile.BadZipFile): return _icon_path()


def register_file_association() -> None:
    """Register a per-user association; no administrator rights are required."""
    if os.name != "nt": return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.scstool") as key: winreg.SetValueEx(key, "", 0, winreg.REG_SZ, PROG_ID)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}") as key: winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "SCS Tools Manager tool")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}\DefaultIcon") as key: winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{_icon_path()}",0')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}\shell\open\command") as key: winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _main_command())


def ensure_manager_shortcut() -> None:
    """Create the manager shortcut in the current user's Local AppData.

    Installers run elevated, so they must not write to a possibly different
    user's Local AppData. The first normal application launch creates this
    shortcut together with the writable application folders.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False): return
    destination = app_data_path() / "SCS Tools Manager.lnk"
    if destination.is_file(): return
    try: _create_shortcut(destination, Path(sys.executable), _icon_path())
    except OSError: pass


def _create_shortcut(shortcut_path: Path, target: Path, icon: Path) -> None:
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from win32com.client import Dispatch
        shell = Dispatch("WScript.Shell"); shortcut = shell.CreateShortcut(str(shortcut_path))
        shortcut.TargetPath = str(target); shortcut.WorkingDirectory = str(target.parent); shortcut.IconLocation = f"{icon},0"; shortcut.Save()
        return
    except Exception:
        pass
    environment = os.environ.copy(); environment.update({"SCS_SHORTCUT_PATH": str(shortcut_path), "SCS_SHORTCUT_TARGET": str(target), "SCS_SHORTCUT_ICON": str(icon)})
    script = "$shell=New-Object -ComObject WScript.Shell; $link=$shell.CreateShortcut($env:SCS_SHORTCUT_PATH); $link.TargetPath=$env:SCS_SHORTCUT_TARGET; $link.WorkingDirectory=Split-Path -Parent $env:SCS_SHORTCUT_TARGET; $link.IconLocation=$env:SCS_SHORTCUT_ICON+',0'; $link.Save()"
    try: subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)
    except (OSError, subprocess.SubprocessError) as error: raise OSError("Unable to create Windows shortcut") from error


def install_tool(tool: Tool, *, one_file: bool, start_menu: bool, desktop: bool) -> dict:
    tool_key = launcher_uid(tool.uid)
    destination = launcher_path(tool)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {"format": LAUNCHER_FORMAT, "uid": tool_key}
    if one_file:
        raw = tool.archive.read_bytes(); payload.update({"archive_name": tool.archive.name, "archive_sha256": hashlib.sha256(raw).hexdigest(), "archive_base64": base64.b64encode(raw).decode("ascii")})
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"); os.replace(temporary, destination)
    register_file_association()
    icon = _tool_icon_path(tool); shortcuts = []
    if start_menu:
        start_root = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "SCS Tools"
        path = start_root / f"{_safe_name(tool.name)}.lnk"; _create_shortcut(path, destination, icon); shortcuts.append(str(path))
    if desktop:
        path = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / f"{_safe_name(tool.name)}.lnk"; _create_shortcut(path, destination, icon); shortcuts.append(str(path))
    records = _load_records(); records[tool_key] = {"launcher": str(destination), "one_file": bool(one_file), "shortcuts": shortcuts}; _save_records(records)
    return {"launcher": destination, "shortcuts": shortcuts}


def restore_embedded_archive(data: dict, registry: ToolRegistry) -> str:
    """Restore an archive embedded in a one-file launcher, then return its UID."""
    uid = str(data["uid"]).lower(); encoded = data.get("archive_base64")
    if not encoded: return uid
    try: raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError): raise ValueError("invalid embedded archive")
    if hashlib.sha256(raw).hexdigest() != data.get("archive_sha256"): raise ValueError("embedded archive checksum mismatch")
    archive_name = Path(str(data.get("archive_name", ""))).name
    if not archive_name or archive_name != str(data.get("archive_name")) or archive_name.lower().endswith(".zip") is False: raise ValueError("invalid archive name")
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(raw)) as archive:
            info = json.loads(archive.read("info.json").decode("utf-8-sig"))
            if launcher_uid(str(info.get("uid", ""))) != uid: raise ValueError("UID mismatch")
    except (KeyError, UnicodeError, zipfile.BadZipFile, json.JSONDecodeError): raise ValueError("invalid embedded archive")
    target = registry.tools_dir / archive_name
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != hashlib.sha256(raw).hexdigest():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp"); temporary.write_bytes(raw); os.replace(temporary, target)
    return uid
