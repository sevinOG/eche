# eche_installer_source — deploy / recover wizard

The wizard is a **small one-download EXE**. The **application** it installs is **onedir** (`Eche.exe` + `_internal/`), not a one-file dropper-style freeze. See monorepo [README.md](../README.md).

## Scripts

| Script | What |
|--------|------|
| **`build.bat`** | Build `dist\Eche-Installer.exe` |
| **`install.bat`** | Launch the wizard (auto-builds if missing) |

Uses `eche_source\.venv` when present. Always runs  
`python -m PyInstaller` (never the fragile `pyinstaller.exe` launcher).

## Wizard directions

1. Install **app** from GitHub (source → `RUN_ECHE.bat` / optional freeze)  
2. Install **app** from portable onedir folder / `Eche.exe`  
3. Install **app** from source tree  
4. Recover **source** from a portable app  

