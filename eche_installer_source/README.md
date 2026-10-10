# Eche Installer

This folder is the installer only. The Discord bot and the desktop app live in `eche_source`. App instructions are in the repo [README](../README.md).

The ready-to-run download is `eche_installer/final/Eche-Installer.exe`. Source version **2.1.0**. That exe keeps its old version number until `build.bat` is run again.

## What the wizard installs

| Choice | Result |
|--------|--------|
| GitHub | Downloads `eche_source` from sevinOG/eche `main`, then builds `Eche.exe` if Python is on PATH |
| Portable app | Copies an existing `Eche.exe` folder |
| Local source | Copies an `eche_source` tree |
| Recover source | Pulls source files back out of a portable app folder |

The installer does not install itself. A GitHub install never copies `eche_installer_source`.

## During install

The install page shows a spinner and a timer the whole time. The download and the first `BUILD.bat` are the long parts. Pip and PyInstaller can sit for several minutes. The spinner means the window is still working. Leave it until the finish page appears.

Default folder: `%LOCALAPPDATA%\Eche`.

## Build this installer

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\build.bat
```

`build.bat` uses `eche_source\.venv` when that venv is already there. It runs `python -m PyInstaller`, then copies `dist\Eche-Installer.exe` to `final\`.

`install.bat` opens the wizard and builds it first if the exe is missing.

Code-signing `Eche-Installer.exe` reduces SmartScreen warnings. Unsigned builds are normal for this repo: More info, then Run anyway.
