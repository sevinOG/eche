# echelon_installer — deploy / recover

## Scripts

| Script | What |
|--------|------|
| **`build.bat`** | Build `dist\Echelon-Installer.exe` |
| **`install.bat`** | Launch the wizard (auto-builds if missing) |

Uses `echelon_source\.venv` when present. Always runs  
`python -m PyInstaller` (never the fragile `pyinstaller.exe` launcher).

## Wizard directions

1. Install **app** from portable / EXE  
2. Install **app** from source tree  
3. Recover **source** from a portable app  
