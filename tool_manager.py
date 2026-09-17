"""Загрузка ZIP-инструментов и их запуск внутри SCS Tools Manager."""
from __future__ import annotations
import json
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import zipfile
import contextlib
import io
import runpy
import threading
import builtins
import ctypes
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from PyQt5.QtCore import QUrl


backend_log = logging.getLogger("scs_tools_manager")


def app_data_path() -> Path:
    """Return the writable application-data folder for packaged builds.

    Program Files contains only the installed program. Downloaded tools, cache
    files and user-created resources belong in the current user's Local AppData.
    Source runs intentionally remain self-contained in the repository.
    """
    if not getattr(sys, "frozen", False): return Path(__file__).resolve().parent
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "SCS Tools Manager"
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class Tool:
    uid: str
    archive: Path
    data: dict[str, Any]
    downloaded_at: datetime
    updated_at: datetime
    errors: list[str] = field(default_factory=list)

    @property
    def name(self): return str(self.data.get("name") or self.archive.stem)
    def description_for(self, lang_code: str, missing="Description not provided") -> str:
        """Предпочитает desc_ru/desc_en; descriptions и description поддержаны для совместимости."""
        value = self.data.get(f"desc_{lang_code}")
        if isinstance(value, str) and value.strip(): return value
        translations = self.data.get("descriptions")
        if isinstance(translations, dict):
            value = translations.get(lang_code) or translations.get("en") or translations.get("ru")
            if isinstance(value, str) and value.strip(): return value
        value = self.data.get("description")
        return str(value) if isinstance(value, str) and value.strip() else missing
    @property
    def version(self): return str(self.data.get("version") or "")
    @property
    def runtime(self): return self.data.get("runtime") if isinstance(self.data.get("runtime"), dict) else {}


class ToolRegistry:
    def __init__(self):
        self.tools_dir = app_data_path() / "tools"
        self.cache_dir = app_data_path() / ".tool_cache"
        self.resources_dir = app_data_path() / "tools_resources"
        self.installed_tools_dir = self.tools_dir / "installed"
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.resources_dir.mkdir(parents=True, exist_ok=True)
        self.installed_tools_dir.mkdir(parents=True, exist_ok=True)
        self._logic_lock = threading.Lock()
        backend_log.info("Backend registry ready: tools=%s cache=%s resources=%s", self.tools_dir, self.cache_dir, self.resources_dir)

    def scan(self) -> list[Tool]:
        result = []
        backend_log.info("Backend scan started: %s", self.tools_dir)
        for archive in sorted(self.tools_dir.glob("*.zip")):
            try:
                with zipfile.ZipFile(archive) as z:
                    raw = json.loads(z.read("info.json").decode("utf-8-sig"))
                    uid = raw.get("uid")
                    if not isinstance(uid, str) or len(uid) != 64:
                        backend_log.warning("Backend scan skipped archive with invalid UID: %s", archive.name)
                        continue
                    dates = [datetime(*item.date_time) for item in z.infolist()]
                tool = Tool(uid, archive, raw, datetime.fromtimestamp(archive.stat().st_mtime), max(dates))
                result.append(tool); backend_log.info("Backend scan found tool: name=%r uid=%s archive=%s", tool.name, uid, archive.name)
            except (OSError, zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
                backend_log.warning("Backend scan skipped unreadable archive %s: %s", archive.name, error)
                continue
        backend_log.info("Backend scan completed: found=%d", len(result))
        return result

    def extract(self, tool: Tool) -> Path:
        # Для пути Windows используем SHA-256, не полагаясь на содержимое UID.
        target = self.cache_path(tool)
        stamp = target / ".source"
        source = str(tool.archive.stat().st_mtime_ns)
        if not target.exists() or not stamp.exists() or stamp.read_text() != source:
            backend_log.info("Backend extracting tool: name=%r archive=%s", tool.name, tool.archive.name)
            shutil.rmtree(target, ignore_errors=True); target.mkdir(parents=True)
            with zipfile.ZipFile(tool.archive) as z: z.extractall(target)
            stamp.write_text(source)
        else:
            backend_log.info("Backend using cached tool extraction: name=%r", tool.name)
        return target

    def cache_path(self, tool: Tool) -> Path:
        return self.cache_dir / hashlib.sha256(tool.uid.encode("utf-8")).hexdigest()

    def resources_path(self, tool: Tool) -> Path:
        """Return the only persistent write directory allowed for this tool.

        The path is deliberately derived by the manager; runtime metadata and
        plugins cannot override it.
        """
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", tool.name).strip("._")[:80]
        if not safe: safe = hashlib.sha256(tool.uid.encode("utf-8")).hexdigest()
        return app_data_path() / "tools_resources" / safe

    def logic_runner(self):
        """Returns a Python command without recursively launching the frozen manager."""
        if not getattr(sys, "frozen", False): return [sys.executable]
        configured = os.environ.get("SCS_PYTHON")
        for candidate in (configured, shutil.which("python"), shutil.which("python3")):
            if candidate and Path(candidate).is_file(): return [candidate]
        launcher = shutil.which("py")
        if launcher: return [launcher, "-3"]
        return None

    def run_logic_embedded(self, script: Path, root: Path, env: dict[str, str]) -> str:
        """Execute dynamic tool logic in the bundled interpreter."""
        output = io.StringIO(); previous_cwd = os.getcwd(); previous_env = os.environ.copy(); previous_argv = sys.argv[:]; dll_dir = None
        old_open, old_io_open, old_os_open = builtins.open, io.open, os.open
        resource_root = Path(env["SCS_TOOL_RESOURCES"]).resolve()
        user_profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
        blocked_roots = [Path(os.environ.get("WINDIR", r"C:\Windows")), Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")), Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")), user_profile / "AppData", Path(os.environ.get("APPDATA", r"C:\Users\Default\AppData")), Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))]
        def guarded_path(value):
            try: return Path(value).resolve()
            except (OSError, TypeError, ValueError): return None
        def check_write(value, flags=None):
            if isinstance(value, int): return
            target = guarded_path(value)
            if isinstance(flags, int): writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC))
            else: writing = flags is None or any(flag in str(flags) for flag in ("w", "a", "x", "+"))
            forbidden = target is None or any(target == base or base in target.parents for base in blocked_roots)
            if writing and forbidden: raise PermissionError("Tool write blocked in a protected system directory")
        def safe_open(file, mode="r", *args, **kwargs): check_write(file, mode); return old_open(file, mode, *args, **kwargs)
        def safe_io_open(file, mode="r", *args, **kwargs): check_write(file, mode); return old_io_open(file, mode, *args, **kwargs)
        def safe_os_open(file, flags, *args, **kwargs): check_write(file, flags); return old_os_open(file, flags, *args, **kwargs)
        try:
            with self._logic_lock:
                builtins.open, io.open, os.open = safe_open, safe_io_open, safe_os_open
                os.environ.update(env); os.environ["PATH"] = str(root) + os.pathsep + os.environ.get("PATH", "")
                if os.name == "nt" and hasattr(os, "add_dll_directory"): dll_dir = os.add_dll_directory(str(root))
                os.chdir(root); sys.argv = [str(script)]
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output): runpy.run_path(str(script), run_name="__main__")
            return output.getvalue()
        finally:
            if dll_dir is not None: dll_dir.close()
            builtins.open, io.open, os.open = old_open, old_io_open, old_os_open
            os.chdir(previous_cwd); os.environ.clear(); os.environ.update(previous_env); sys.argv = previous_argv

    def cleanup_tool(self, tool: Tool) -> None:
        backend_log.info("Backend cleaning tool cache: name=%r", tool.name)
        shutil.rmtree(self.cache_path(tool), ignore_errors=True)

    def cleanup_all(self) -> None:
        shutil.rmtree(self.cache_dir, ignore_errors=True)

    def icon(self, tool: Tool) -> Path | None:
        value = tool.data.get("icon")
        if not isinstance(value, str): return None
        path = self.extract(tool) / value
        return path if path.is_file() else None

    def banner(self, tool: Tool) -> Path | None:
        """Return the optional banner bundled with the ZIP, if it is safe."""
        value = tool.data.get("banner")
        if not isinstance(value, str) or not value.strip(): return None
        root = self.extract(tool).resolve()
        path = (root / value).resolve()
        if root not in path.parents or not path.is_file(): return None
        return path

    def html_url(self, tool: Tool) -> QUrl | None:
        runtime = tool.runtime
        if runtime.get("type") not in ("html", "html_python", "html_script"): return None
        entry = runtime.get("entry")
        if not isinstance(entry, str): return None
        path = self.extract(tool) / entry
        return QUrl.fromLocalFile(str(path)) if path.is_file() else None

    def exe_path(self, tool: Tool) -> Path | None:
        runtime = tool.runtime
        if runtime.get("type") != "exe": return None
        entry = runtime.get("entry")
        if not isinstance(entry, str): return None
        path = self.extract(tool) / entry
        return path if path.is_file() else None

    def run_logic(self, tool: Tool, payload: str = "") -> dict[str, Any]:
        runtime, root = tool.runtime, self.extract(tool)
        logic = runtime.get("logic")
        try: action = json.loads(payload).get("action", "") if payload else ""
        except (TypeError, ValueError, json.JSONDecodeError): action = "invalid-payload"
        backend_log.info("Backend logic start: tool=%r action=%r runtime=%s", tool.name, action, runtime.get("type", ""))
        if not isinstance(logic, str): return self.run_external_logic(tool, root, payload)
        if not (root / logic).is_file():
            backend_log.error("Backend logic file missing: tool=%r file=%s", tool.name, logic)
            return {}
        try:
            parsed = json.loads(payload) if payload else {}
            if not isinstance(parsed, dict): raise ValueError("Tool payload must be an object")
            resources = self.resources_path(tool); resources.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy(); env["SCS_TOOL_INPUT"] = json.dumps(parsed, ensure_ascii=False); env["SCS_TOOL_RESOURCES"] = str(resources); env["PATH"] = str(root) + os.pathsep + env.get("PATH", "")
            timeout = max(1, min(float(runtime.get("timeout", 8)), 900))
            process_options = {}
            if os.name == "nt":
                startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startup.wShowWindow = subprocess.SW_HIDE
                process_options.update(startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
            script = root / logic
            if getattr(sys, "frozen", False):
                output = self.run_logic_embedded(script, root, env)
            else:
                runner = self.logic_runner()
                if not runner: return {"status": "error", "code": "python_runtime_missing", "message": "Python runtime is required for this tool"}
                output = subprocess.check_output(runner + [str(script)], cwd=root, text=True, timeout=timeout, stderr=subprocess.STDOUT, env=env, **process_options)
            response = json.loads(output)
            backend_log.info("Backend logic completed: tool=%r action=%r status=%r", tool.name, action, response.get("status"))
            return response
        except PermissionError: return {"status": "security_violation", "message": "ВНИМАНИЕ! ИНСТРУМЕНТ ПЫТАЕТСЯ ВЫЙТИ ЗА ПРЕДЕЛЫ ЗАЩИТЫ. Запуск остановлен.\nWARNING! THE TOOL ATTEMPTED TO BREAK OUTSIDE THE PROTECTION. Launch stopped."}
        except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError, SystemExit): return {"status": "error", "message": "Не удалось запустить логику инструмента"}

    def run_external_logic(self, tool: Tool, root: Path, payload: str = "") -> dict[str, Any]:
        """Run a bundled runtime command (Node, .NET, Java or native EXE)."""
        runtime = tool.runtime; command = runtime.get("command")
        backend_log.info("Backend external runtime start: tool=%r runtime=%s", tool.name, runtime.get("type", ""))
        if runtime.get("type") == "html_script" and not command: command = ["node", "{script}"]
        script_name = runtime.get("script") or runtime.get("entry")
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command): return {}
        resources = self.resources_path(tool); resources.mkdir(parents=True, exist_ok=True)
        script = root / script_name if isinstance(script_name, str) else None
        if script is not None and not script.is_file(): return {}
        values = {"root": str(root), "resources": str(resources), "script": str(script) if script else ""}
        argv = [item.format(**values) for item in command]
        if script is not None and "{script}" not in " ".join(command): argv.append(str(script))
        if argv and not os.path.isabs(argv[0]) and (root / argv[0]).is_file(): argv[0] = str(root / argv[0])
        # Node.js 20+ permission model: allow bundled scripts to read the tool
        # and write only to its manager-owned resources directory.
        if argv and Path(argv[0]).name.lower() in ("node", "node.exe"):
            # Node has an allow-list model. Permit existing user-profile
            # folders except AppData, plus manager-owned data; never grant the
            # profile root itself because it would include AppData.
            profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
            user_dirs = [item for item in profile.iterdir() if item.is_dir() and item.name.lower() != "appdata"] if profile.is_dir() else []
            system_drive = Path(os.environ.get("WINDIR", r"C:\Windows")).drive.upper()
            other_drives = []
            if os.name == "nt":
                mask = ctypes.windll.kernel32.GetLogicalDrives()
                other_drives = [Path(f"{chr(65 + index)}:\\") for index in range(26) if mask & (1 << index) and f"{chr(65 + index)}:" != system_drive]
            allow_write = [app_data_path(), resources, *user_dirs, *other_drives]
            argv[1:1] = ["--permission", f"--allow-fs-read={root}", f"--allow-fs-read={app_data_path()}", *[f"--allow-fs-write={folder}" for folder in allow_write]]
        env = os.environ.copy(); env["SCS_TOOL_INPUT"] = payload or "{}"; env["SCS_TOOL_RESOURCES"] = str(resources); env["PATH"] = str(root) + os.pathsep + env.get("PATH", "")
        options = {}
        if os.name == "nt":
            startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startup.wShowWindow = subprocess.SW_HIDE
            options.update(startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output = subprocess.check_output(argv, cwd=root, text=True, timeout=max(1, min(float(runtime.get("timeout", 30)), 900)), stderr=subprocess.STDOUT, env=env, **options)
            response = json.loads(output)
            backend_log.info("Backend external runtime completed: tool=%r status=%r", tool.name, response.get("status"))
            return response
        except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            detail = str(exc) + " " + str(getattr(exc, "output", ""))
            backend_log.warning("Backend external runtime failed: tool=%r error=%s", tool.name, detail[:500])
            if any(word in detail.lower() for word in ("permission", "denied", "access_denied", "err_access")):
                return {"status": "security_violation", "message": "ВНИМАНИЕ! ИНСТРУМЕНТ ПЫТАЕТСЯ ВЫЙТИ ЗА ПРЕДЕЛЫ ЗАЩИТЫ. Запуск остановлен.\nWARNING! THE TOOL ATTEMPTED TO BREAK OUTSIDE THE PROTECTION. Launch stopped."}
            return {"status": "error", "message": "Unable to run the tool runtime"}

    def launch_exe(self, tool: Tool) -> bool:
        path = self.exe_path(tool)
        if not path:
            backend_log.error("Backend EXE launch failed, entry missing: tool=%r", tool.name)
            return False
        process_options = {}
        if os.name == "nt":
            startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startup.wShowWindow = subprocess.SW_SHOWNORMAL
            process_options.update(startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
        launch_env = os.environ.copy(); launch_env["PATH"] = str(path.parent) + os.pathsep + launch_env.get("PATH", "")
        subprocess.Popen([str(path)], cwd=path.parent, env=launch_env, **process_options)
        backend_log.info("Backend EXE launched: tool=%r path=%s", tool.name, path)
        return True


def flatten(data: Any, prefix=""):
    if isinstance(data, dict):
        for key, value in data.items(): yield from flatten(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(data, list):
        for i, value in enumerate(data): yield from flatten(value, f"{prefix}[{i}]")
    else: yield prefix, str(data)
