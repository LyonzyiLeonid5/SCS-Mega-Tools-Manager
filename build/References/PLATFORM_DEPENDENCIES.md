# Native installer build dependencies

Cross-building a PyQt/WebEngine application with PyInstaller is unsupported: build each package on its target operating system and CPU architecture.

## Linux (DEB and RPM)

Required commands:

- `python3`, `python3-pip`, and the Python development headers for your distribution;
- `dpkg-deb` (Debian/Ubuntu package: `dpkg-dev` or the base `dpkg` package);
- `rpm` and `rpmbuild` (Fedora/RHEL/openSUSE package: `rpm-build`; Ubuntu/Debian: `rpm`);
- a working Qt WebEngine runtime/build dependency set for `PyQtWebEngine`.

Install Python packages with `python3 -m pip install PyInstaller PyQt5 PyQtWebEngine`, then run `python3 build.py` on Linux. The output is:

- `dist/Linux/scs-tools-manager_<version>_<arch>.deb`
- `dist/Linux/scs-tools-manager-<version>-1.<arch>.rpm`

The generated packages declare runtime libraries (`libgl`, EGL, NSS, and libxkbcommon), install the application under `/usr/lib/scs-tools-manager`, add a launcher at `/usr/bin/scs-tools-manager`, and add it to the desktop application menu with the bundled application icon. Package names differ by distribution; review them in a target VM before publishing.

## macOS (DMG and PKG)

Required:

- macOS on the architecture being published (Apple Silicon or Intel);
- Xcode Command Line Tools: `xcode-select --install` — supplies `hdiutil`, `pkgbuild`, and `productbuild`;
- Python 3 plus `PyInstaller`, `PyQt5`, and `PyQtWebEngine` installed in that Python environment.

Run `python3 build.py` on macOS. The output is:

- `dist/MacOS/SCS_Tools_Manager-<version>.dmg`
- `dist/MacOS/SCS_Tools_Manager-<version>.pkg`

For public distribution outside Gatekeeper warnings, sign the `.app` and installer with an Apple Developer ID and notarize the final DMG/PKG. The current build intentionally does not pretend to sign or notarize without those credentials.
