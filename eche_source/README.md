# eche_source — edit & build

Application source for **Eche** (formerly Echelon). Freeze output is **onedir** (folder), not one-file.

**Ready-to-run without building?** Use the monorepo installer:  
[prebuilt/Eche-Installer.exe](https://github.com/sevinOG/eche/raw/main/prebuilt/Eche-Installer.exe)  
`../eche/` is only populated after `BUILD.bat` — it is not a pre-shipped binary tree in git.

## Why onedir?

One-file PyInstaller EXEs unpack to a temp directory every launch — Defender often treats that like a dropper.  
Onedir keeps a small `Eche.exe` launcher beside `_internal/` (normal app layout, fewer false positives). UPX is disabled.

## The only freeze script you need

```bat
BUILD.bat
```

Creates/refreshes the portable app in **`../eche/`**:

```text
../eche/Eche.exe
../eche/_internal/   ← required; do not delete
../eche/assets/
...
```

## First setup (Git / terminal)

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
BUILD.bat
```

Or: `SETUP_AND_BUILD.bat` (venv + deps + freeze in one go).

## Dev run (no freeze)

```bat
.venv\Scripts\python.exe eche_app.py
```

Or: `RUN_ECHE.bat`

Optional verbose logs: set `ECHE_DEBUG=1` before launching.

Owner-only Discord commands use Discord’s **application owner** (`@commands.is_owner()`), not a hardcoded user id.

## Spec

`build_exe.spec` — `exclude_binaries=True` + `COLLECT` → `dist/Eche/`.  
See monorepo [README.md](../README.md) · [PRIVACY.md](../PRIVACY.md).
