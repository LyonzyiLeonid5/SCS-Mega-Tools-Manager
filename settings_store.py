"""Небольшое устойчивое хранилище пользовательских настроек."""
import json
import os
import sys
from pathlib import Path

DEFAULT_SETTINGS = {"language": "ru", "theme": "dark_scs", "last_page": "dashboard", "created_tools": []}


def settings_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "SCS Tools Manager"
        base.mkdir(parents=True, exist_ok=True)
    else:
        base = Path(__file__).resolve().parent
    return base / "settings.json"


def load_settings() -> dict:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        if isinstance(data, dict): return {**DEFAULT_SETTINGS, **{k: v for k, v in data.items() if k in DEFAULT_SETTINGS}}
    except (OSError, json.JSONDecodeError):
        pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> None:
    path = settings_path(); temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
