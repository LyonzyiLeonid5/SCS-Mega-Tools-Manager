"""Build native SCS Tools Manager distributions for the current platform.

Outputs:
    Windows: dist/windows/ (portable EXE, Setup EXE, and MSI)
    Linux:   dist/Linux/ (*.deb and *.rpm)
    macOS:   dist/MacOS/ (*.dmg and *.pkg)

PyInstaller packages native binaries only. Run this script on each target OS
(and target CPU architecture) to produce its actual installer packages.
"""
from __future__ import annotations

import hashlib
import html
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


APP_NAME = "SCS Tools Manager"
EXE_NAME = "SCS_Tools_Manager.exe"
SETUP_NAME = "SCS_Tools_Manager_Setup.exe"
MSI_NAME = "SCS_Tools_Manager.msi"
PROG_ID = "SCS.Tools.Manager.scstool"
VERSION = os.environ.get("SCS_TOOLS_VERSION", "1.0.0")
UPGRADE_CODE = "{E8B8E57B-8E9F-4A9B-9238-6B41BCA30E78}"
NODE_URL = "https://nodejs.org/dist/v24.21.0/node-v24.21.0-x64.msi"
WIX_URL = "https://github.com/wixtoolset/wix/releases/download/v7.0.0/wix-cli-x64.msi"
INNO_URL = "https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe"
INSTALLER_REFERENCES = Path("Build") / "References"
PYTHON_PACKAGES = {
    "PyInstaller.__main__": "PyInstaller",
    "PyQt5": "PyQt5",
    "PyQt5.QtWebEngineWidgets": "PyQtWebEngine",
    "certifi": "certifi",
}


class DependencyInstallationError(RuntimeError):
    """A dependency installer was cancelled or failed."""


def find_program(env_name: str, names: tuple[str, ...], standard_paths: tuple[Path, ...]) -> Path | None:
    configured = os.environ.get(env_name, "").strip()
    if configured:
        candidate = Path(configured)
        if candidate.is_file(): return candidate
        found = shutil.which(configured)
        if found: return Path(found)
    for name in names:
        found = shutil.which(name)
        if found: return Path(found)
    return next((path for path in standard_paths if path.is_file()), None)


def log(message: str) -> None:
    print(f"[build] {message}", flush=True)


def inno_compiler() -> Path | None:
    roots = (Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")), Path(os.environ.get("ProgramFiles", r"C:\Program Files")))
    paths = tuple(root / f"Inno Setup {version}" / "ISCC.exe" for version in ("7", "6") for root in roots)
    return find_program("INNO_SETUP_COMPILER", ("ISCC.exe", "iscc.exe"), paths)


def wix_compiler(minimum_major: int = 0) -> Path | None:
    roots = (Path(os.environ.get("ProgramFiles", r"C:\Program Files")), Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")))
    paths = tuple(root / f"WiX Toolset v{version}.0" / "bin" / "wix.exe" for version in (7, 6, 5, 4) for root in roots)
    candidates: list[Path] = []
    configured = os.environ.get("WIX", "").strip()
    if configured:
        candidate = Path(configured)
        found = candidate if candidate.is_file() else shutil.which(configured)
        if found: candidates.append(Path(found))
    candidates.extend(path for path in paths if path.is_file())
    for name in ("wix.exe", "wix"):
        if found := shutil.which(name): candidates.append(Path(found))
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen: continue
        seen.add(resolved)
        if not minimum_major or (wix_major_version(resolved) or 0) >= minimum_major:
            return resolved
    return None


def node_executable() -> Path | None:
    roots = (Path(os.environ.get("ProgramFiles", r"C:\Program Files")), Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")))
    return find_program("NODE", ("node.exe", "node"), tuple(root / "nodejs" / "node.exe" for root in roots))


def wix_major_version(executable: Path) -> int | None:
    try:
        completed = subprocess.run([str(executable), "--version"], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r"\b(\d+)\.", completed.stdout + completed.stderr)
    return int(match.group(1)) if match else None


def github_latest_asset(repository: str, pattern: str, fallback: str) -> str:
    """Return the newest matching GitHub release asset, with a pinned fallback."""
    request = Request(
        f"https://api.github.com/repos/{repository}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "SCS-Tools-Manager-build"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            release = json.loads(response.read().decode("utf-8"))
        for asset in release.get("assets", []):
            name = str(asset.get("name", ""))
            url = str(asset.get("browser_download_url", ""))
            if re.fullmatch(pattern, name, re.I) and url.startswith("https://"):
                return url
    except (OSError, URLError, ValueError, UnicodeError, json.JSONDecodeError):
        pass
    return fallback


def download_installer(project: Path, url: str) -> Path:
    references = project / INSTALLER_REFERENCES
    references.mkdir(parents=True, exist_ok=True)
    filename = Path(urlparse(url).path).name
    if not filename: raise RuntimeError(f"Cannot determine installer name from {url}")
    target = references / filename
    if target.is_file() and target.stat().st_size:
        log(f"Using cached installer: {target.relative_to(project)}")
        return target
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        log(f"Downloading installer: {url}")
        request = Request(url, headers={"User-Agent": "SCS-Tools-Manager-build"})
        with urlopen(request, timeout=60) as source, temporary.open("wb") as output:
            while chunk := source.read(1024 * 1024): output.write(chunk)
        if not temporary.stat().st_size: raise RuntimeError(f"Downloaded installer is empty: {url}")
        os.replace(temporary, target)
        log(f"Installer downloaded: {target.relative_to(project)}")
    except (OSError, URLError) as error:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Could not download {url}: {error}") from error
    return target


def install_installer(kind: str, installer: Path) -> None:
    if installer.suffix.lower() == ".msi":
        command = ["msiexec.exe", "/i", str(installer)]
    else:
        command = [str(installer)]
    log(f"Opening {kind} installer: {installer.name}")
    completed = subprocess.run(command)
    if completed.returncode not in (0, 3010):
        log(f"Failed to install {kind}.")
        raise DependencyInstallationError(kind)
    log(f"{kind} installation completed.")


def prompt_for_executable(label: str, filename: str) -> Path:
    if not sys.stdin.isatty():
        raise RuntimeError(f"{label} was installed but {filename} was not found. Set the appropriate environment variable or run build.py interactively to provide its installation directory.")
    while True:
        value = input(f"Enter the {label} installation directory (or the full path to {filename}): ").strip().strip('"')
        candidate = Path(value)
        if candidate.is_dir(): candidate /= filename
        if candidate.is_file(): return candidate
        log(f"{filename} was not found at: {candidate}")


def ensure_python_dependencies():
    def module_available(module: str) -> bool:
        try: return importlib.util.find_spec(module) is not None
        except ModuleNotFoundError: return False
    missing = [package for module, package in PYTHON_PACKAGES.items() if not module_available(module)]
    if missing:
        log("Installing Python packages: " + ", ".join(missing))
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", *missing], check=True)
        except subprocess.CalledProcessError as error:
            raise RuntimeError("Could not install Python build dependencies: " + ", ".join(missing)) from error
    return importlib.import_module("PyInstaller.__main__")


def ensure_system_dependencies(project: Path) -> tuple[Path, Path]:
    log("Checking Node.js, WiX Toolset, and Inno Setup.")
    node = node_executable()
    if not node:
        log("Node.js was not found.")
        install_installer("Node.js", download_installer(project, NODE_URL))
        node = node_executable()
    else: log(f"Node.js found: {node}")
    wix = wix_compiler(minimum_major=7)
    if not wix:
        log("WiX Toolset 7 or later was not found.")
        wix_url = github_latest_asset("wixtoolset/wix", r"wix-cli-x64\.msi", WIX_URL)
        install_installer("WiX Toolset", download_installer(project, wix_url))
        wix = wix_compiler(minimum_major=7)
    else: log(f"WiX Toolset found: {wix}")
    inno = inno_compiler()
    if not inno:
        log("Inno Setup was not found.")
        inno_url = github_latest_asset("jrsoftware/issrc", r"innosetup-\d+\.\d+\.\d+-x64\.exe", INNO_URL)
        install_installer("Inno Setup", download_installer(project, inno_url))
        inno = inno_compiler()
    else: log(f"Inno Setup found: {inno}")
    if not node: raise RuntimeError("Node.js installation completed but node.exe was not found. Restart the terminal or set NODE to node.exe.")
    if not wix:
        wix = prompt_for_executable("WiX Toolset 7", "wix.exe")
        if (wix_major_version(wix) or 0) < 7: raise RuntimeError("WiX Toolset 7 or later is required.")
    if not inno: inno = prompt_for_executable("Inno Setup", "ISCC.exe")
    log("Accepting the WiX 7 EULA.")
    subprocess.run([str(wix), "eula", "accept", "wix7"], check=True)
    log("Build dependencies are ready.")
    return inno, wix


def quoted_iss(value: Path | str) -> str:
    return str(value).replace('"', '""')


def xml(value: Path | str) -> str:
    return html.escape(str(value), quote=True)


def ensure_build_dependencies(project: Path):
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", VERSION):
        raise ValueError("SCS_TOOLS_VERSION must be a numeric Windows version, for example 1.0.0")
    pyinstaller = ensure_python_dependencies()
    inno, wix = ensure_system_dependencies(project)
    return inno, wix, pyinstaller


def pyinstaller_args(project: Path, mode: str, dist: Path, work: Path, spec: Path, icon_override: Path | None = None) -> list[str]:
    icon = icon_override or project / "assets" / ("icon.icns" if sys.platform == "darwin" else "icon.ico")
    separator = ";" if os.name == "nt" else ":"
    args = [
        str(project / "main.py"), f"--name={Path(EXE_NAME).stem}", mode, "--windowed",
        f"--add-data={project / 'assets'}{separator}assets", f"--add-data={project / 'LICENSE'}{separator}.",
        "--hidden-import=PyQt5", "--hidden-import=PyQt5.QtWebEngineWidgets", "--hidden-import=PyQt5.QtWebChannel",
        # The macOS helper process and Chromium resources are not always
        # discovered through PyQt's imports alone.  Without them creating a
        # QWebEngineView aborts the application instead of reporting an error.
        "--collect-all=PyQt5.QtWebEngineCore",
        "--collect-data=certifi",
        f"--distpath={dist}", f"--workpath={work}", f"--specpath={spec}", "--clean", "--noconfirm",
    ]
    if icon.is_file(): args.insert(4, f"--icon={icon}")
    if mode == "--onedir": args.insert(3, "--contents-directory=redist")
    return args


def write_inno_script(path: Path, payload: Path, output: Path, icon: Path, license_file: Path) -> None:
    path.write_text(f'''[Setup]
AppId={{{{D47C4FCD-D4C1-4BA4-845B-E5417AB2CA29}}}}
AppName={APP_NAME}
AppVersion={VERSION}
AppPublisher=SCS Tools Manager
DefaultDirName={{autopf}}\\SCS Tools Manager
DefaultGroupName=SCS Tools Manager
DisableProgramGroupPage=yes
UninstallDisplayName=SCS Tools Manager
OutputDir={quoted_iss(output)}
OutputBaseFilename={Path(SETUP_NAME).stem}
SetupIconFile={quoted_iss(icon)}
LicenseFile={quoted_iss(license_file)}
UninstallDisplayIcon={{app}}\\{EXE_NAME}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "{quoted_iss(payload)}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{quoted_iss(license_file)}"; DestDir: "{{app}}"; Flags: ignoreversion

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "startmenu"; Description: "Create an SCS Tools Manager folder in the Start menu"; Flags: unchecked

[Icons]
Name: "{{autodesktop}}\\SCS Tools Manager"; Filename: "{{app}}\\{EXE_NAME}"; WorkingDir: "{{app}}"; Tasks: desktopicon
Name: "{{autoprograms}}\\SCS Tools Manager\\SCS Tools Manager"; Filename: "{{app}}\\{EXE_NAME}"; WorkingDir: "{{app}}"; Tasks: startmenu

[Registry]
Root: HKCR; Subkey: ".scstool"; ValueType: string; ValueName: ""; ValueData: "{PROG_ID}"; Flags: uninsdeletevalue
Root: HKCR; Subkey: "{PROG_ID}"; ValueType: string; ValueName: ""; ValueData: "SCS Tools Manager tool"; Flags: uninsdeletekey
Root: HKCR; Subkey: "{PROG_ID}\\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{{app}}\\{EXE_NAME},0"
Root: HKCR; Subkey: "{PROG_ID}\\shell\\open\\command"; ValueType: string; ValueName: ""; ValueData: """{{app}}\\{EXE_NAME}"" ""%1"""

''', encoding="utf-8")


def wix_id(prefix: str, value: str) -> str:
    return prefix + hashlib.sha1(value.encode("utf-8")).hexdigest()[:20]


def write_license_rtf(source: Path, target: Path) -> Path:
    content = source.read_text(encoding="utf-8").replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\par\n")
    target.write_text(r"{\rtf1\ansi\deff0{\fonttbl{\f0 Segoe UI;}}\f0\fs20\n" + content + "}", encoding="ascii", errors="backslashreplace")
    return target


def write_wix_source(path: Path, payload: Path, icon: Path, license_file: Path) -> None:
    files = sorted(item for item in payload.rglob("*") if item.is_file())
    if not files: raise RuntimeError("Installer payload is empty")
    tree: dict[str, dict] = {"files": [], "children": {}}
    for file in files:
        node = tree
        for part in file.relative_to(payload).parts[:-1]: node = node["children"].setdefault(part, {"files": [], "children": {}})
        node["files"].append(file)
    component_ids: list[str] = []

    def emit(node: dict, relative: Path, indent: str) -> list[str]:
        lines: list[str] = []
        for file in node["files"]:
            rel = file.relative_to(payload); component_id = wix_id("Cmp", rel.as_posix()); file_id = wix_id("File", rel.as_posix()); component_ids.append(component_id)
            lines.extend([f'{indent}<Component Id="{component_id}" Guid="*">', f'{indent}  <File Id="{file_id}" Source="{xml(file.resolve())}" KeyPath="yes" />', f'{indent}</Component>'])
        for name, child in node["children"].items():
            child_rel = relative / name; child_id = wix_id("Dir", child_rel.as_posix())
            lines.append(f'{indent}<Directory Id="{child_id}" Name="{xml(name)}">')
            lines.extend(emit(child, child_rel, indent + "  "))
            lines.append(f'{indent}</Directory>')
        return lines

    payload_lines = emit(tree, Path(), "        ")
    association_id = "ScsToolAssociation"; desktop_id = "DesktopShortcut"; start_menu_id = "StartMenuShortcut"; component_ids.append(association_id)
    refs = "\n".join(f'      <ComponentRef Id="{item}" />' for item in component_ids)
    path.write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs" xmlns:ui="http://wixtoolset.org/schemas/v4/wxs/ui">
  <Package Name="{APP_NAME}" Manufacturer="SCS Tools Manager" Version="{VERSION}" UpgradeCode="{UPGRADE_CODE}" Scope="perMachine">
    <MajorUpgrade DowngradeErrorMessage="A newer version of {APP_NAME} is already installed." />
    <MediaTemplate EmbedCab="yes" />
    <Icon Id="ProductIcon" SourceFile="{xml(icon.resolve())}" />
    <Property Id="ARPPRODUCTICON" Value="ProductIcon" />
    <WixVariable Id="WixUILicenseRtf" Value="{xml(license_file.resolve())}" />
    <StandardDirectory Id="ProgramFiles6432Folder">
      <Directory Id="INSTALLFOLDER" Name="SCS Tools Manager">
{os.linesep.join(payload_lines)}
        <Component Id="{association_id}" Guid="*">
          <RegistryValue Root="HKCR" Key=".scstool" Type="string" Value="{PROG_ID}" KeyPath="yes" />
          <RegistryValue Root="HKCR" Key="{PROG_ID}" Type="string" Value="SCS Tools Manager tool" />
          <RegistryValue Root="HKCR" Key="{PROG_ID}\\DefaultIcon" Type="string" Value="[INSTALLFOLDER]{EXE_NAME},0" />
          <RegistryValue Root="HKCR" Key="{PROG_ID}\\shell\\open\\command" Type="string" Value="&quot;[INSTALLFOLDER]{EXE_NAME}&quot; &quot;%1&quot;" />
        </Component>
      </Directory>
    </StandardDirectory>
    <StandardDirectory Id="DesktopFolder">
      <Component Id="{desktop_id}" Guid="*">
        <Shortcut Id="DesktopShortcutLink" Name="SCS Tools Manager" Target="[INSTALLFOLDER]{EXE_NAME}" WorkingDirectory="INSTALLFOLDER" />
        <RegistryValue Root="HKLM" Key="Software\\SCS Tools Manager" Name="DesktopShortcut" Type="integer" Value="1" KeyPath="yes" />
      </Component>
    </StandardDirectory>
    <StandardDirectory Id="ProgramMenuFolder">
      <Directory Id="SCS_TOOLS_START_MENU" Name="SCS Tools Manager">
        <Component Id="{start_menu_id}" Guid="*">
          <Shortcut Id="StartMenuShortcutLink" Name="SCS Tools Manager" Target="[INSTALLFOLDER]{EXE_NAME}" WorkingDirectory="INSTALLFOLDER" />
          <RemoveFolder Id="RemoveStartMenuFolder" On="uninstall" />
          <RegistryValue Root="HKLM" Key="Software\\SCS Tools Manager" Name="StartMenuShortcut" Type="integer" Value="1" KeyPath="yes" />
        </Component>
      </Directory>
    </StandardDirectory>
    <ui:WixUI Id="WixUI_Mondo" InstallDirectory="INSTALLFOLDER" />
    <Feature Id="MainFeature" Title="{APP_NAME}" Level="1">
{refs}
    </Feature>
    <Feature Id="DesktopShortcutFeature" Title="Create a desktop shortcut" Description="Creates a shortcut for SCS Tools Manager on the desktop." Level="4">
      <ComponentRef Id="{desktop_id}" />
    </Feature>
    <Feature Id="StartMenuShortcutFeature" Title="Create a Start menu folder" Description="Creates an SCS Tools Manager folder in the Start menu." Level="4">
      <ComponentRef Id="{start_menu_id}" />
    </Feature>
  </Package>
</Wix>
''', encoding="utf-8")


def reset_output_folder(target: Path) -> None:
    """Clear one known build output and give a useful error for an open EXE."""
    if target.exists():
        try: shutil.rmtree(target)
        except PermissionError as error:
            raise RuntimeError(f"Cannot replace {target}. Close the running installer or application that is using a file in this folder, then run build.py again.") from error
    target.mkdir(parents=True, exist_ok=True)


def require_commands(*commands: str) -> None:
    missing = [command for command in commands if not shutil.which(command)]
    if missing:
        raise RuntimeError("Missing native packaging dependencies: " + ", ".join(missing) + ". See Build/References/PLATFORM_DEPENDENCIES.md.")


def native_binary_name() -> str:
    return Path(EXE_NAME).stem + (".exe" if os.name == "nt" else "")


def make_linux_icon(project: Path, target: Path) -> Path:
    """Convert the bundled ICO into the PNG installed in Linux app menus."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QImage
    source = project / "assets" / "icon.ico"
    image = QImage(str(source))
    if image.isNull(): raise RuntimeError(f"Unable to read Linux application icon: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    icon = image.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if not icon.save(str(target), "PNG"): raise RuntimeError(f"Unable to write Linux application icon: {target}")
    return target


def make_macos_icon(project: Path, target: Path) -> Path:
    """Build a native .icns application icon from the repository ICO."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QImage
    source = project / "assets" / "icon.ico"
    image = QImage(str(source))
    if image.isNull(): raise RuntimeError(f"Unable to read macOS application icon: {source}")
    iconset = target.with_suffix(".iconset")
    if iconset.exists(): shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    for points, pixels in ((16, 16), (16, 32), (32, 32), (32, 64), (128, 128), (128, 256), (256, 256), (256, 512), (512, 512), (512, 1024)):
        suffix = "@2x" if pixels == points * 2 else ""
        output = iconset / f"icon_{points}x{points}{suffix}.png"
        scaled = image.scaled(pixels, pixels, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if not scaled.save(str(output), "PNG"): raise RuntimeError(f"Unable to write macOS icon image: {output}")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(target)], check=True)
    return target


def sign_macos_webengine_bundle(app_bundle: Path) -> None:
    """Ad-hoc sign QtWebEngine's helper with the entitlements it ships with."""
    helpers = list(app_bundle.rglob("QtWebEngineProcess.app"))
    if not helpers:
        raise RuntimeError("PyInstaller did not include QtWebEngineProcess.app in the macOS bundle")
    for helper in helpers:
        entitlements = helper / "Contents" / "Resources" / "QtWebEngineProcess.entitlements"
        args = ["codesign", "--force", "--sign", "-"]
        if entitlements.is_file(): args.extend(("--entitlements", str(entitlements)))
        subprocess.run(args + [str(helper)], check=True)
    # Sign the outer application only after its nested helper is signed.
    subprocess.run(["codesign", "--force", "--sign", "-", str(app_bundle)], check=True)


def build_linux(project: Path, pyinstaller) -> tuple[Path, Path]:
    """Build real Linux packages only on Linux; PyInstaller is not cross-platform."""
    require_commands("dpkg-deb", "rpm", "rpmbuild")
    dist = project / "dist" / "Linux"; app_dist = project / "build" / "linux-app"
    staging = project / "build" / "linux"; spec = project / "build" / "spec"
    for target in (dist, app_dist, staging): reset_output_folder(target)
    pyinstaller.run(pyinstaller_args(project, "--onedir", app_dist, staging / "pyinstaller", spec))
    payload = app_dist / Path(EXE_NAME).stem; binary = payload / native_binary_name()
    if not binary.is_file(): raise RuntimeError("PyInstaller did not create the Linux folder distribution")
    machine = os.uname().machine.lower(); deb_arch = "amd64" if machine in ("x86_64", "amd64") else machine; rpm_arch = "x86_64" if deb_arch == "amd64" else machine
    package_name = "scs-tools-manager"; icon = make_linux_icon(project, staging / f"{package_name}.png"); deb_root = staging / "deb-root"; (deb_root / "DEBIAN").mkdir(parents=True); (deb_root / "usr" / "lib").mkdir(parents=True)
    shutil.copytree(payload, deb_root / "usr" / "lib" / package_name)
    launcher = deb_root / "usr" / "bin" / package_name; launcher.parent.mkdir(parents=True)
    launcher.write_text(f"#!/bin/sh\nexec /usr/lib/{package_name}/{native_binary_name()} \"$@\"\n", encoding="utf-8"); launcher.chmod(0o755)
    desktop = deb_root / "usr" / "share" / "applications" / f"{package_name}.desktop"; desktop.parent.mkdir(parents=True)
    icon_target = deb_root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / f"{package_name}.png"; icon_target.parent.mkdir(parents=True); shutil.copy2(icon, icon_target)
    desktop.write_text(f"[Desktop Entry]\nType=Application\nName={APP_NAME}\nComment=Manage tools for SCS games\nExec=/usr/bin/{package_name}\nIcon={package_name}\nTerminal=false\nCategories=Utility;Development;\n", encoding="utf-8")
    (deb_root / "DEBIAN" / "control").write_text(f"Package: {package_name}\nVersion: {VERSION}\nSection: utils\nPriority: optional\nArchitecture: {deb_arch}\nMaintainer: SCS Tools Manager\nDepends: libgl1, libegl1, libnss3, libxkbcommon0\nDescription: SCS Tools Manager\n", encoding="utf-8")
    deb = dist / f"{package_name}_{VERSION}_{deb_arch}.deb"; subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(deb_root), str(deb)], check=True)
    top = staging / "rpmbuild"; (top / "SPECS").mkdir(parents=True); (top / "BUILDROOT").mkdir(parents=True)
    spec_file = top / "SPECS" / f"{package_name}.spec"
    # rpmbuild executes the spec through a shell.  Quote the source path so a
    # project folder such as ``~/Рабочий стол/scs mega manager`` works too.
    rpm_payload = str(payload).replace('"', '\\"'); rpm_icon = str(icon).replace('"', '\\"')
    # PyQt's prebuilt WebEngine libraries deliberately carry upstream RPATHs.
    # They remain functional inside the package, but Fedora/RPM's QA checker
    # rejects those third-party binaries unless this check is disabled here.
    spec_file.write_text(f"%global __brp_check_rpaths %{{nil}}\n%global _build_id_links none\nName: {package_name}\nVersion: {VERSION}\nRelease: 1%{{?dist}}\nSummary: SCS Tools Manager\nLicense: GPL-2.0-only\nBuildArch: {rpm_arch}\nRequires: mesa-libGL, nss, libxkbcommon\n%description\nSCS Tools Manager\n%install\nmkdir -p \"%{{buildroot}}/usr/lib/{package_name}\" \"%{{buildroot}}/usr/bin\" \"%{{buildroot}}/usr/share/applications\" \"%{{buildroot}}/usr/share/icons/hicolor/256x256/apps\"\ncp -a \"{rpm_payload}/.\" \"%{{buildroot}}/usr/lib/{package_name}/\"\ncp \"{rpm_icon}\" \"%{{buildroot}}/usr/share/icons/hicolor/256x256/apps/{package_name}.png\"\nprintf '#!/bin/sh\\nexec /usr/lib/{package_name}/{native_binary_name()} \"$@\"\\n' > \"%{{buildroot}}/usr/bin/{package_name}\"\nprintf '[Desktop Entry]\\nType=Application\\nName={APP_NAME}\\nComment=Manage tools for SCS games\\nExec=/usr/bin/{package_name}\\nIcon={package_name}\\nTerminal=false\\nCategories=Utility;Development;\\n' > \"%{{buildroot}}/usr/share/applications/{package_name}.desktop\"\nchmod 0755 \"%{{buildroot}}/usr/bin/{package_name}\"\n%files\n/usr/lib/{package_name}\n/usr/bin/{package_name}\n/usr/share/applications/{package_name}.desktop\n/usr/share/icons/hicolor/256x256/apps/{package_name}.png\n", encoding="utf-8")
    # rpmbuild only constructs a package tree; it does not need an RPM database.
    # Supplying one makes some RPM distributions try to lock a protected database.
    subprocess.run(["rpmbuild", "-bb", str(spec_file), "--define", f"_topdir {top}"], check=True)
    produced = next((path for path in (top / "RPMS" / rpm_arch).glob("*.rpm")), None)
    if not produced: raise RuntimeError("rpmbuild completed but did not produce an RPM")
    rpm = dist / produced.name; shutil.copy2(produced, rpm)
    return deb, rpm


def build_macos(project: Path, pyinstaller) -> tuple[Path, Path]:
    """Build a macOS app, DMG and PKG only on macOS with Apple tooling."""
    require_commands("hdiutil", "pkgbuild", "productbuild", "iconutil", "codesign")
    dist = project / "dist" / "MacOS"; app_dist = project / "build" / "macos-app"
    staging = project / "build" / "macos"; spec = project / "build" / "spec"
    # These folders contain only disposable macOS build output.
    for target in (dist, app_dist, staging): reset_output_folder(target)
    icon = make_macos_icon(project, staging / "SCS_Tools_Manager.icns")
    pyinstaller.run(pyinstaller_args(project, "--onedir", app_dist, staging / "pyinstaller", spec, icon))
    app_bundle = app_dist / f"{Path(EXE_NAME).stem}.app"
    if not app_bundle.is_dir(): raise RuntimeError("PyInstaller did not create the macOS .app bundle")
    sign_macos_webengine_bundle(app_bundle)
    volume = staging / "dmg-root"; volume.mkdir(); shutil.copytree(app_bundle, volume / app_bundle.name); (volume / "Applications").symlink_to("/Applications")
    dmg = dist / f"SCS_Tools_Manager-{VERSION}.dmg"; subprocess.run(["hdiutil", "create", "-volname", APP_NAME, "-srcfolder", str(volume), "-ov", "-format", "UDZO", str(dmg)], check=True)
    component = staging / "component.pkg"; subprocess.run(["pkgbuild", "--component", str(app_bundle), "--install-location", "/Applications", str(component)], check=True)
    pkg = dist / f"SCS_Tools_Manager-{VERSION}.pkg"; subprocess.run(["productbuild", "--package", str(component), str(pkg)], check=True)
    # Keep only distributable installers after a successful macOS build.
    for temporary in (staging, app_dist):
        try: shutil.rmtree(temporary)
        except PermissionError as error:
            raise RuntimeError(f"macOS installers were created, but {temporary} is in use and could not be removed.") from error
    return dmg, pkg


def build() -> None:
    project = Path(__file__).resolve().parent; os.chdir(project)
    if sys.platform.startswith("linux"):
        pyinstaller = ensure_python_dependencies(); outputs = build_linux(project, pyinstaller)
        print("Linux build completed:")
        for item in outputs: print(" -", item.relative_to(project))
        return
    if sys.platform == "darwin":
        pyinstaller = ensure_python_dependencies(); outputs = build_macos(project, pyinstaller)
        print("macOS build completed:")
        for item in outputs: print(" -", item.relative_to(project))
        return
    if os.name != "nt": raise RuntimeError(f"Unsupported build platform: {sys.platform}")
    icon = project / "assets" / "icon.ico"
    license_file = project / "LICENSE"
    if not icon.is_file(): raise FileNotFoundError(f"Icon not found: {icon}")
    if not license_file.is_file(): raise FileNotFoundError(f"License not found: {license_file}")
    inno, wix, pyinstaller = ensure_build_dependencies(project)
    dist = project / "dist" / "Windows"; portable = setup = msi = dist; staging = project / "build" / "release"; spec = project / "build" / "spec"
    for target in (dist, staging): reset_output_folder(target)
    pyinstaller.run(pyinstaller_args(project, "--onefile", portable, project / "build" / "portable", spec))
    pyinstaller.run(pyinstaller_args(project, "--onedir", staging, project / "build" / "folder", spec))
    payload = staging / Path(EXE_NAME).stem
    if not (payload / EXE_NAME).is_file(): raise RuntimeError("PyInstaller did not create the folder distribution")
    installer_dir = project / "build" / "installers"; installer_dir.mkdir(parents=True, exist_ok=True)
    inno_script = installer_dir / "SCS_Tools_Manager.iss"; write_inno_script(inno_script, payload, setup, icon, license_file)
    subprocess.run([str(inno), str(inno_script)], check=True)
    wix_license = write_license_rtf(license_file, installer_dir / "GPL-2.0.rtf")
    wix_source = installer_dir / "SCS_Tools_Manager.wxs"; write_wix_source(wix_source, payload, icon, wix_license)
    wix_args = [str(wix), "build", "-ext", "WixToolset.UI.wixext"]
    accepted_eula = os.environ.get("WIX_ACCEPT_EULA", "").strip()
    if accepted_eula: wix_args.extend(("-acceptEula", accepted_eula))
    wix_args.extend((str(wix_source), "-arch", "x64", "-o", str(msi / MSI_NAME)))
    subprocess.run(wix_args, check=True)
    expected = (portable / EXE_NAME, setup / SETUP_NAME, msi / MSI_NAME)
    missing = [str(item) for item in expected if not item.is_file()]
    if missing: raise RuntimeError("Build completed but output is missing: " + ", ".join(missing))
    print("Build completed:")
    for item in expected: print(" -", item.relative_to(project))


if __name__ == "__main__":
    try:
        build()
    except DependencyInstallationError:
        raise SystemExit(1)
