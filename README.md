# Eche - Discord Bot Control Panel

Open-source Discord bot + desktop control panel.

**Repo:** https://github.com/sevinOG/eche · **Version:** see root `VERSION` (currently **1.3.1**)

**Formerly Echelon** — same project, rebranded to **Eche** (product, folders, EXE names). Older docs or shortcuts may still say "Echelon"; treat them as legacy names for this repo.

## Four forks only

| Fork | Folder | What |
| --- | --- | --- |
| **App (portable)** | `eche/` | **Build output** (onedir: `Eche.exe` + `_internal/`) — produced by `BUILD.bat` or auto-built by installer, not a pre-shipped binary tree in git |
| **App source** | `eche_source/` | Python source + `BUILD.bat` (installer auto-builds this now, devs can still run manually) |
| **Installer (portable)** | `eche_installer/` / `prebuilt/` | Wizard: `Eche-Installer.exe` — **recommended ready-to-run path (auto-builds Eche.exe)** |
| **Installer source** | `eche_installer_source/` | Installer UI + `build.bat` |

---

## Recommended: ready to run (installer) - NEW USERS START HERE

**Download Eche-Installer.exe**

1. Save and run **Eche-Installer.exe**

2. Keep **Install from GitHub** selected (now auto-builds Eche.exe - no batch files needed)

3. Choose a folder → **Install** → wait 2-3 min first time (needs Python from python.org, check **Add python.exe to PATH**)

4. **Eche.exe** launches automatically from `..\eche\Eche.exe` - Start Menu shortcut `Eche.lnk` also points to it

If auto-build fails, check `START_HERE.txt` in your install folder - it will tell you if Python is missing.

More hand-holding: START_HERE.md. Privacy: PRIVACY.md.

Unsigned open-source EXEs often trip SmartScreen/Defender until you add code signing. Prefer the official GitHub link; Edge **Keep** / SmartScreen **Run anyway**.

> A fresh `git clone` does **not** include a frozen `eche/Eche.exe` + `_internal/` tree. That folder is created when you run the installer OR `eche_source\BUILD.bat`. For a working app without building, use the installer above.

---

## Install: Command Line

### For Everyone (Installer - Auto-build)

This is the fastest way. The installer fetches source and builds `Eche.exe` automatically.

```powershell
# 1. Download installer (if you have curl/wget) or download from GitHub Releases page
# 2. Run it:
.\prebuilt\Eche-Installer.exe
# or
.\eche_installer\Eche-Installer.exe
```

Then pick folder → Install → Eche.exe auto-launches.

No `SETUP_AND_BUILD.bat` or `RUN_ECHE.bat` needed for this path.

### For Developers (Git + terminal)

Use this only if you're editing code. For normal use, use the installer above.

#### 0) Install Git and Python first (Windows / PowerShell)

Open **PowerShell**. Use **exact package IDs** (`--id` + `-e`). Do **not** pass bare names like `git` or `python`.

```powershell
# Git for Windows  — package id: Git.Git
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements

# Python 3.12      — package id: Python.Python.3.12  (3.11+ works; stay off Python 2)
winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
```

| Tool | Exact winget package id | Notes |
| --- | --- | --- |
| Git | `Git.Git` | Git for Windows |
| Python 3.12 (recommended) | `Python.Python.3.12` | Official CPython from PSF |
| Python 3.11 (also fine) | `Python.Python.3.11` | Same flags as above |
| Python 3.13 (also fine) | `Python.Python.3.13` | Same flags as above |

**After install:** close PowerShell, open a **new** window, then:

```powershell
git --version
python --version
py -0p
```

**Manual downloads (if winget is missing):**
git-scm.com/downloads · python.org/downloads (enable **Add python.exe to PATH**).

#### 1) Clone

```powershell
git clone https://github.com/sevinOG/eche.git
cd eche
```

#### 2) Build the portable app (onedir) — for devs after clone

Best for developers who cloned the repo: one freeze, then double-click **`Eche.exe`** like a normal app.

```powershell
cd eche_source
.\SETUP_AND_BUILD.bat
```

(`SETUP_AND_BUILD.bat` creates a venv, installs dependencies, and freezes. Needs Python from step 0.)

Creates/refreshes sibling **`eche/`**:

```
eche/

  Eche.exe          <- double-click this

  _internal/        <- must stay next to Eche.exe

  assets/

  ...

```

Then:

```powershell
cd ..\eche
.\Eche.exe
```

Copy the **whole** `eche/` folder if you move it. Do not ship `Eche.exe` alone.

First GUI run: **Settings → Discord bot token → Run Bot**.

#### 3) Run from source (optional — developers)

Skip this if step 2 already gave you `Eche.exe`. Use source run only when editing code without re-freezing every time.

```powershell
cd eche_source
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe eche_app.py
```

Or: `.\RUN_ECHE.bat` after the venv exists.

Optional verbose bot logs: set environment variable `ECHE_DEBUG=1`.

#### 4) Installer from source (maintainers)

```powershell
cd eche_installer_source
.\build.bat
```

Produces `dist\Eche-Installer.exe` (publish under `prebuilt/` when ready). Code-signing this EXE is recommended for SmartScreen.

---

## Packaging: why onedir (not one-file)

| Mode | What happens | Why we avoid / use it |
| --- | --- | --- |
| **One-file** | Unpacks to temp every launch | Dropper-like → Defender ML false positives |
| **Onedir** (current) | Slim `Eche.exe` + `_internal/` | Normal app layout; less AV noise |

- Spec: `eche_source/build_exe.spec` — `exclude_binaries=True` + `COLLECT`

- **UPX off**

- Installer wraps/copies folders or fetches source; it now auto-builds the onedir app instead of asking users to run bats

---

## Privacy

Tokens and keys stay on your machine except when talking to **Discord**, **Groq** (if enabled), or **Unsplash** (image command). See **PRIVACY.md**.

---

## Repo layout after clone

```
eche/                      <- monorepo root

  README.md  START_HERE.md  PRIVACY.md  VERSION

  prebuilt/Eche-Installer.exe   <- recommended ready path (auto-builds)

  eche/                    <- portable onedir *after* BUILD.bat or installer auto-build (not pre-frozen in git)

  eche_source/             <- app source

  eche_installer/          <- installer convenience copy

  eche_installer_source/   <- installer source

```

---

## Common commands (installer first)

| Goal | Command |
| --- | --- |
| **Install for users (auto-build, recommended)** | Run `prebuilt/Eche-Installer.exe` → auto-builds `eche/Eche.exe` + Start Menu shortcut |
| **Install Git** | `winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements` |
| **Install Python 3.12** | `winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements` |
| **Clone (devs)** | `git clone https://github.com/sevinOG/eche.git` |
| **Build portable from clone (dev)** | `.\eche_source\SETUP_AND_BUILD.bat` → run `.\eche\Eche.exe` |
| **Freeze only (dev)** | `.\eche_source\BUILD.bat` |
| **Run from source (dev)** | `cd eche_source` → venv + `python eche_app.py` |
| **Build installer (maintainers)** | `.\eche_installer_source\build.bat` |
| **Debug logs** | `$env:ECHE_DEBUG=1` then run bot/GUI |

---

## Maintainers & contact

Prefer **GitHub Issues** for bugs and features.

See MAINTAINERS.md (no secrets in issues).

## License

MIT — LICENSE.

## What Changed in v1.3.1

- Installer now auto-builds `Eche.exe` (2-3 min first time, needs Python) - removed manual `SETUP_AND_BUILD.bat` step for new users
- Start Menu / Desktop shortcuts now point directly to `Eche.exe` instead of `RUN_ECHE.bat`
- README reordered: installer first, dev commands second
