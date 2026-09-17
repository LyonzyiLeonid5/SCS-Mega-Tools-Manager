# SCS Tools Manager

SCS Tools Manager is a desktop application for installing, running, organising and creating tools for SCS Software games, including Euro Truck Simulator 2 and American Truck Simulator.

It provides one workspace for both ready-made community tools and your own HTML, Python, JavaScript or EXE-based utilities. The interface is available in English and Russian.

## Main capabilities

- Browse verified tools from the online catalogue.
- Download, update, remove and launch tool archives.
- Verify downloaded archives by filename and SHA-256 checksum.
- Detect accidental duplicate UIDs and prevent unsafe duplicate launches.
- Run HTML tools with Python or JavaScript actions inside the manager on Windows and Linux.
- Run HTML tools through a private `127.0.0.1` server on macOS when the embedded WebEngine is unavailable.
- Launch compatible EXE tools on Windows.
- Install a tool for quick launching through a `.scstool` file, Start Menu shortcut and/or desktop shortcut.
- Create a tool in three guided stages: metadata, project files, and visual HTML editing.
- Edit HTML, CSS classes, global styles and element styles visually.
- Connect HTML controls to Python or JavaScript actions.
- Keep editor drafts, the last open page and a history of recent HTML edits.
- View application actions, warnings and errors in the application log.

## Application sections

### Home

The home page contains an overview of the manager, tool statistics, links to the author’s communities and a quick guide to the interface.

### Store

The Store reads the verified catalogue from the configured website. Each card displays the tool banner, icon, category, description, runtime, version, update date and archive size.

The manager compares an installed archive with the catalogue by both filename and SHA-256:

- **Download** — the archive is not present.
- **Update** — an archive with the same name exists but its checksum differs.
- **Open** — the verified archive is already downloaded.

Tool pages display the full README, author links, download controls and installation controls.

### Downloaded

This page lists all ZIP archives in the local `tools` folder. From here you can launch, update, delete or install a tool. Installed tools can also be uninstalled; this removes their `.scstool` launcher and optional shortcuts without deleting the downloaded ZIP archive.

### Running

Opened tools appear as tabs. HTML tools can communicate with Python or JavaScript through the tool bridge. On Windows, EXE tools may be hosted as native windows. The page also supports reloading an already-open tool after an update.

### Create

The tool editor consists of three stages:

1. **Tool information** — name, UID, descriptions, icon, banner, runtime and required runtime files.
2. **Project structure** — add, edit, import and organise project files inside the tool ZIP archive.
3. **Visual editor** — edit HTML source and visual layout, styles, classes, global CSS, actions and element bindings. The editor includes undo/redo and a Test button.

The UID is a 64-character identifier. It must be unique: the editor checks existing local tools before creating an archive.

## Tool archive format

Tools are distributed as ZIP archives. A basic HTML + Python tool contains:

```text
my_tool.zip
├── info.json
├── app.html
├── logic.py
├── icon.png
└── banner.png
```

`info.json` stores the tool metadata and runtime configuration. A simplified example:

```json
{
  "uid": "64-character-unique-uid",
  "name": "My Tool",
  "desc_en": "An English description is required for publication.",
  "desc_ru": "Russian description is optional.",
  "version": "1.0.0",
  "icon": "icon.png",
  "banner": "banner.png",
  "runtime": {
    "type": "html_python",
    "entry": "app.html",
    "logic": "logic.py"
  }
}
```

Supported runtime types include `html_python`, `html_js` and Windows EXE tools. EXE tools are marked unavailable on non-Windows systems.

## HTML actions

An HTML control can invoke a backend action using `data-scs-action`:

```html
<button data-scs-action="create_list">Create list</button>
```

The manager passes form values to the configured Python or JavaScript logic file. The result is returned to the page and can be handled by `window.renderTool(data)`.

## Installation and `.scstool` files

Installing a tool always creates a `.scstool` launch file containing its UID. You can additionally choose a desktop shortcut, a Start Menu shortcut and a single-file launcher with the tool archive embedded.

On Windows, `.scstool` files are associated with SCS Tools Manager. If a launch file refers to a tool that is not downloaded, the manager can retrieve its verified archive from the catalogue. A launcher containing an unknown embedded archive is clearly marked as unverified before it is restored.

## Verified catalogue publication

The verified catalogue is described by `site/verified_tools.json`. Every catalogue entry includes at least a UID, name, English description, category, banner, icon, runtime, update date, download filename, size and SHA-256 checksum.

For publication, prepare a ZIP archive of the tool and a ZIP archive or link containing the publication files, such as `manifest.json`, the banner, icon and language-specific READMEs. `README.md` is the English description; `README_ru.md` and other translated versions are optional.

## Building distributions

Run the build script on the target operating system:

```powershell
python build.py
```

The script installs missing Python build dependencies when needed and creates native packages for the current platform.

- **Windows:** portable EXE, Setup EXE and MSI in `dist\Windows`.
- **Linux:** DEB and RPM packages in `dist\Linux`.
- **macOS:** DMG and PKG packages in `dist\MacOS`.

Windows installers require Inno Setup and WiX. Linux package creation requires the native packaging utilities provided by the target distribution. macOS packaging requires Xcode Command Line Tools.

Builds are native: create Windows packages on Windows, Linux packages on Linux, and macOS packages on macOS.

## Local data and logging

The installed application keeps user-specific files in the local application-data directory. This includes settings, downloaded tools, launchers, cache files and logs. Removing the application itself does not automatically delete these local user files.

The application log records user actions, warnings and unexpected errors. It is useful when reporting a problem.

## License

The installer uses the GPL-2.0 license. See the included license material for the full terms.

## Links

- Discord: <https://discord.com/invite/trAftSH7gk>
- GitHub: <https://github.com/LyonzyiLeonid5/>
- Website: <https://lyonzyileonid5.website.yandexcloud.net/>
- Telegram (Russian-speaking users): <https://t.me/Lyonzyi_Leonid5>
