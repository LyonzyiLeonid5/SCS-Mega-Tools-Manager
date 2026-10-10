"""Небольшое устойчивое хранилище пользовательских настроек."""
import json
import os
import shutil
import sys
from pathlib import Path

DEFAULT_SETTINGS = {
    "language": "ru", "theme": "dark", "last_page": "dashboard",
    "created_tools": [], "html_general_css_properties": [],
    "favorite_tools": [], "detailed_logging": False,
    "library_sort": "name", "tool_last_opened": {},
    "open_tool_uids": [], "notifications": [], "ui_density": "normal",
    "auto_update_check": True, "first_run_tip": True, "downloaded": [],
    "shortcuts": {"command_palette": "Ctrl+K", "dashboard": "Ctrl+1", "store": "Ctrl+2", "library": "Ctrl+3", "running": "Ctrl+4", "create": "Ctrl+5", "debug": "Ctrl+6", "settings": "Ctrl+7"},
    "tool_collections": {}
}


def settings_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "SCS Tools Manager"
        base.mkdir(parents=True, exist_ok=True)
    else:
        base = Path(__file__).resolve().parent
    return base / "settings.json"


def load_settings() -> dict:
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict): return {**DEFAULT_SETTINGS, **{k: v for k, v in data.items() if k in DEFAULT_SETTINGS}}
    except (OSError, json.JSONDecodeError):
        pass
    backup = path.parent / "backup" / "settings.json.bak"
    try:
        data = json.loads(backup.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            restored = {**DEFAULT_SETTINGS, **{k: v for k, v in data.items() if k in DEFAULT_SETTINGS}}
            save_settings(restored)
            return restored
    except (OSError, json.JSONDecodeError):
        pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> None:
    path = settings_path(); temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    backup = path.parent / "backup" / "settings.json.bak"
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    except OSError:
        pass


def backup_text_resources(resources: Path) -> int:
    """Back up only user-editable text resources, never binary tool data."""
    if not resources.is_dir(): return 0
    destination = settings_path().parent / "backup" / "tools_resources"
    extensions = {'.txt', '.json', '.md', '.ini', '.cfg', '.sii', '.xml', '.yaml', '.yml'}
    copied = 0
    for source in resources.rglob('*'):
        if not source.is_file() or source.suffix.lower() not in extensions: continue
        try:
            relative = source.relative_to(resources)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target.with_suffix(target.suffix + '.bak'))
            copied += 1
        except (OSError, ValueError):
            continue
    return copied
