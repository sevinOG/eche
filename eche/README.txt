# Eche

Open-source Discord bot + desktop control panel.

**Repo:** [github.com/sevinOG/eche](https://github.com/sevinOG/eche) · **Version:** see root [`VERSION`](VERSION) (currently **1.3.0**)

**Formerly Echelon** — same project, rebranded to **Eche** (product, folders, EXE names). Older docs or shortcuts may still say “Echelon”; treat them as legacy names for this repo.

## Four forks only

| Fork | Folder | What |
|------|--------|------|
| **App (portable)** | `eche/` | **Build output** (onedir: `Eche.exe` + `_internal/`) — produced by `BUILD.bat`, not a pre-shipped binary tree in git |
| **App source** | `eche_source/` | Python source + `BUILD.bat` / `SETUP_AND_BUILD.bat` |
| **Installer (portable)** | `eche_installer/` / `prebuilt/` | Wizard: `Eche-Installer.exe` — **recommended ready-to-run path** |
| **Installer source** | `eche_installer_source/` | Installer UI + `build.bat` |

---

## Recommended: ready to run (installer)

**[Download Eche-Installer.exe](https://github.com/sevinOG/eche/raw/main/prebuilt/Eche-Installer.exe)**

1. Save and run **Eche-Installer.exe**
2. Keep **Install from GitHub** selected
3. Choose a folder → **Install**
4. Open the folder → prefer **`Eche.exe`** if present; otherwise run **`SETUP_AND_BUILD.bat`** once to create it (or **`RUN_ECHE.bat`** for a Python-only launch)

More hand-holding: [START_HERE.md](START_HERE.md). Privacy: [PRIVACY.md](PRIVACY.md).

Unsigned open-source EXEs often trip SmartScreen/Defender until you add code signing. Prefer the official GitHub link; Edge **Keep** / SmartScreen **Run anyway**.

> A fresh `git clone` does **not** include a frozen `eche/Eche.exe` + `_internal/` tree. That folder is created when you run `eche_source\BUILD.bat`. For a working app without building, use the installer above.

---

## Install: Git + terminal (developers)

### 0) Install Git and Python first (Windows / PowerShell)

Open **PowerShell**. Use **exact package IDs** (`--id` + `-e`). Do **not** pass bare names like `git` or `python`.

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

**After install:** close PowerShell, open a **new** window, then:

```powershell
git --version
python --version
py -0p
```

**Manual downloads (if winget is missing):**  
[git-scm.com/downloads](https://git-scm.com/downloads) · [python.org/downloads](https://www.python.org/downloads/) (enable **Add python.exe to PATH**).

### 1) Clone

```powershell
git clone https://github.com/sevinOG/eche.git
cd eche
```

### 2) Build the portable app (onedir) — preferred after clone

Best for beginners who cloned the repo: one freeze, then double-click **`Eche.exe`** like a normal app.

```powershell
cd eche_source
.\SETUP_AND_BUILD.bat
```

(`SETUP_AND_BUILD.bat` creates a venv, installs dependencies, and freezes. Needs Python from step 0.)

Creates/refreshes sibling **`eche/`**:

```text
eche/
  Eche.exe          ← double-click this
  _internal/        ← must stay next to Eche.exe
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

### 3) Run from source (optional — developers)

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

### 4) Installer from source (maintainers)

```powershell
cd eche_installer_source
.\build.bat
```

Produces `dist\Eche-Installer.exe` (publish under `prebuilt/` when ready). Code-signing this EXE is recommended for SmartScreen.

---

## Packaging: why onedir (not one-file)

| Mode | What happens | Why we avoid / use it |
|------|----------------|------------------------|
| **One-file** | Unpacks to temp every launch | Dropper-like → Defender ML false positives |
| **Onedir** (current) | Slim `Eche.exe` + `_internal/` | Normal app layout; less AV noise |

- Spec: `eche_source/build_exe.spec` — `exclude_binaries=True` + `COLLECT`
- **UPX off**
- Installer wraps/copies folders or fetches source; it does not re-extract the bot stack on every double-click

---

## Privacy

Tokens and keys stay on your machine except when talking to **Discord**, **Groq** (if enabled), or **Unsplash** (image command). See **[PRIVACY.md](PRIVACY.md)**.

---

## Repo layout after clone

```text
eche/                      ← monorepo root
  README.md  START_HERE.md  PRIVACY.md  VERSION
  prebuilt/Eche-Installer.exe   ← recommended ready path
  eche/                    ← portable onedir *after* BUILD.bat (not pre-frozen in git)
  eche_source/             ← app source
  eche_installer/          ← installer convenience copy
  eche_installer_source/   ← installer source
```

---

## Common commands

| Goal | Command |
|------|---------|
| Install Git | `winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements` |
| Install Python 3.12 | `winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements` |
| Clone | `git clone https://github.com/sevinOG/eche.git` |
| **Build portable (preferred)** | `.\eche_source\SETUP_AND_BUILD.bat` → run `.\eche\Eche.exe` |
| Freeze only | `.\eche_source\BUILD.bat` |
| Run from source (dev) | `cd eche_source` → venv + `python eche_app.py` |
| Build installer | `.\eche_installer_source\build.bat` |
| Debug logs | `$env:ECHE_DEBUG=1` then run bot/GUI |

---

## Maintainers & contact

Prefer **[GitHub Issues](https://github.com/sevinOG/eche/issues)** for bugs and features.  
See [MAINTAINERS.md](MAINTAINERS.md) (no secrets in issues).

## License

MIT — [LICENSE](LICENSE).
