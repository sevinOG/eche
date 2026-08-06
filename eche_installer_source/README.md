# eche_installer_source — from zero to installer

This folder is the **installer wizard** (source + build scripts).

**From-zero path**

| Step | What | Required? |
|------|------|-----------|
| **1** | Install **Git** + **Python** | Yes |
| **2** | Get / build / run **Eche-Installer** | Yes (this README) |
| **3** | Clone **app source** and run/build the bot yourself | Optional (more dev access) |

The wizard is a small setup EXE. The **application** it installs is **onedir** (`Eche.exe` + `_internal/`), not a one-file dropper-style freeze. Full monorepo docs: [../README.md](../README.md).

---

## Step 1 — Install Git and Python (PowerShell)

Open **PowerShell**. Use **exact package IDs** (`--id` + `-e`). Do **not** pass bare names like `git` or `python` — winget’s fuzzy match is unreliable.

```powershell
# Git for Windows  — package id: Git.Git
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements

# Python 3.12      — package id: Python.Python.3.12  (3.11+ works; stay off Python 2)
winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
```

| Tool | Exact winget package id | Notes |
|------|-------------------------|--------|
| Git | `Git.Git` | Git for Windows |
| Python 3.12 (recommended) | `Python.Python.3.12` | Official CPython from PSF |
| Python 3.11 (also fine) | `Python.Python.3.11` | Same flags as above |
| Python 3.13 (also fine) | `Python.Python.3.13` | Same flags as above |

**After install:** close PowerShell completely, open a **new** window so `PATH` updates, then check:

```powershell
git --version
python --version
py -0p
```

You want Git 2.x and Python 3.11+ (not 2.7).

**Manual downloads (if winget is missing):**  
[git-scm.com/downloads](https://git-scm.com/downloads) · [python.org/downloads](https://www.python.org/downloads/) (enable **Add python.exe to PATH**).

---

## Step 2 — Install the installer (terminal)

Pick **one** path: download the prebuilt wizard, **or** build it from this source tree.

### Option A — Download prebuilt installer (fastest)

Still in PowerShell (new window after Step 1):

```powershell
# Download official Eche-Installer.exe from GitHub (this monorepo)
$uri = "https://github.com/sevinOG/eche/raw/main/prebuilt/Eche-Installer.exe"
$out = Join-Path $env:USERPROFILE "Downloads\Eche-Installer.exe"
Invoke-WebRequest -Uri $uri -OutFile $out -UseBasicParsing
Write-Host "Saved: $out"
Start-Process $out
```

Then in the wizard:

1. Keep **Install from GitHub** selected (beginner default)
2. Choose an install folder → **Install**
3. Open the folder → **`RUN_ECHE.bat`** or **`Eche.exe`** if present

If SmartScreen blocks an unsigned open-source EXE: Edge **Keep** / SmartScreen **Run anyway**. Prefer only this official GitHub URL.

### Option B — Build the installer from source (this folder)

```powershell
# Clone monorepo (if you do not have it yet)
git clone https://github.com/sevinOG/eche.git
cd eche

# Build wizard from installer source
cd eche_installer_source
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\build.bat
```

Launch:

```powershell
# from eche_installer_source
.\install.bat
# or:
Start-Process .\dist\Eche-Installer.exe
```

Output: `dist\Eche-Installer.exe` (also publishable under `../prebuilt/`).

**Dev run without freezing** (edit UI, no EXE yet):

```powershell
cd eche_installer_source
.\.venv\Scripts\python.exe run.py
```

`build.bat` prefers `../eche_source/.venv` when present; always runs `python -m PyInstaller` (never the fragile `pyinstaller.exe` stub).

---

## Step 3 — Optional: app source (greater dev access)

Skip this if the wizard + GitHub install is enough. Use it when you want to **edit the bot**, freeze a portable onedir yourself, or run without the wizard.

```powershell
# If not already cloned:
git clone https://github.com/sevinOG/eche.git
cd eche\eche_source

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Run GUI from source
.\.venv\Scripts\python.exe eche_app.py
# or:
.\RUN_ECHE.bat

# Optional: freeze portable onedir → ../eche/Eche.exe + _internal/
.\BUILD.bat
# or one-shot:
.\SETUP_AND_BUILD.bat
```

App source docs: [../eche_source/README.md](../eche_source/README.md) · monorepo: [../README.md](../README.md).

---

## Reference (moved down) — scripts & wizard modes

### Scripts in this folder

| Script | What |
|--------|------|
| **`build.bat`** | Build `dist\Eche-Installer.exe` |
| **`install.bat`** | Launch the wizard (auto-builds if missing) |
| **`run.py`** | Dev: run installer UI without freezing |
| **`build.spec`** | PyInstaller spec (installer EXE; UPX off) |
| **`requirements.txt`** | `PyQt6`, `pywin32` (Windows) |

### Wizard directions (once the installer is open)

1. Install **app** from GitHub (source → `RUN_ECHE.bat` / optional freeze)
2. Install **app** from portable onedir folder / `Eche.exe`
3. Install **app** from local source tree
4. Recover **source** from a portable app

### Packaging notes

| Piece | Layout |
|-------|--------|
| This wizard EXE | Small one-download setup tool |
| App it installs / you build | **Onedir**: `Eche.exe` + `_internal/` (not one-file) |
| UPX | Off (AV false positives) |

### Four forks (monorepo)

| Fork | Folder | What |
|------|--------|------|
| App (portable) | `../eche/` | `Eche.exe` + `_internal/` |
| App source | `../eche_source/` | Bot + GUI source |
| Installer (portable) | `../eche_installer/` | Built wizard convenience copy |
| Installer source | **this folder** | Wizard recipe |

### Legacy / Echelon notes

Older shortcuts, folder names, or docs may still say **Echelon** / `echelon_*`. The product and repo are **Eche** / [sevinOG/eche](https://github.com/sevinOG/eche). The installer still accepts legacy path names when recovering old trees; new installs use `eche_*` only.
