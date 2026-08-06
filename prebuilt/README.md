# Prebuilt

**[Eche-Installer.exe](https://github.com/sevinOG/eche/raw/main/prebuilt/Eche-Installer.exe)** — one-tap Windows installer wizard.

The **app** itself is not a single fat EXE: after install/build you get an **onedir** folder (`Eche.exe` + `_internal/`). That layout is intentional (fewer AV false positives than one-file PyInstaller).

Rebuild installer: `../eche_installer_source/build.bat`  
Build portable app: `../eche_source/BUILD.bat`  

Full docs: [../README.md](../README.md) (includes **git clone / terminal** install).
