# Prebuilt

**[Eche-Installer.exe](https://github.com/sevinOG/eche/raw/main/prebuilt/Eche-Installer.exe)** — **recommended** ready-to-run Windows installer wizard.

This is the primary “download and go” path. A git clone does **not** ship a frozen `eche/Eche.exe` tree; that appears only after `../eche_source/BUILD.bat`.

The **app** itself is **onedir** (`Eche.exe` + `_internal/`), not one-file PyInstaller. Code-signing this installer EXE reduces SmartScreen friction.

Rebuild installer: `../eche_installer_source/build.bat`  
Build portable app: `../eche_source/BUILD.bat`  

Full docs: [../README.md](../README.md) · Privacy: [../PRIVACY.md](../PRIVACY.md).
