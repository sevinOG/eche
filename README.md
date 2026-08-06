# Eche

Open-source Discord bot + desktop control panel.

**Repo:** [github.com/sevinOG/eche](https://github.com/sevinOG/eche)

## Four forks only

| Fork | Folder | What |
|------|--------|------|
| **App (portable)** | `eche/` | Onedir app: `Eche.exe` + `_internal/` |
| **App source** | `eche_source/` | Python source + `BUILD.bat` / `SETUP_AND_BUILD.bat` |
| **Installer (portable)** | `eche_installer/` | Wizard: `dist\Eche-Installer.exe` |
| **Installer source** | `eche_installer_source/` | Installer UI + `build.bat` |

---

## Install: beginners (one download)

**[Download Eche-Installer.exe](https://github.com/sevinOG/eche/raw/main/prebuilt/Eche-Installer.exe)**

1. Save and run **Eche-Installer.exe**
2. Keep **Install from GitHub** selected
3. Choose a folder → **Install**
4. Open the folder → run **`RUN_ECHE.bat`** (source) or **`Eche.exe`** if present

More hand-holding: [START_HERE.md](START_HERE.md).

Unsigned open-source EXEs often trip SmartScreen/Defender. Prefer the official GitHub link; Edge **Keep** / SmartScreen **Run anyway**.

---

## Install: Git + terminal

### 0) Install Git and Python first (Windows / PowerShell)

Open **PowerShell** (normal is fine; use “Run as administrator” only if winget asks).

Use **exact package IDs** (`--id` + `-e`). Do **not** pass bare names like `git` or `python` — winget’s fuzzy match is unreliable.

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

You want Git 2.x and Python 3.11+ (not 2.7). If `python` still fails but `py -3.12` works, either use the full path from `py -0p` or open a fresh terminal again.

**Manual downloads (if winget is missing):**  
[git-scm.com/downloads](https://git-scm.com/downloads) · [python.org/downloads](https://www.python.org/downloads/) (enable **Add python.exe to PATH**).

Windows is required for the portable freeze (`.bat` scripts). Linux/mac can run the GUI from source with Qt available.

### 1) Clone

```powershell
git clone https://github.com/sevinOG/eche.git
cd eche
```

### 2) Run from source (fastest for development)

**PowerShell (Windows):**

```powershell
cd eche_source
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe eche_app.py
```

Or after the venv exists:

```powershell
cd eche_source
.\RUN_ECHE.bat
```

**bash (Git Bash / WSL / macOS / Linux):**

```bash
cd eche_source
python3 -m venv .venv
source .venv/bin/activate   # Windows Git Bash: source .venv/Scripts/activate
pip install -U pip
pip install -r requirements.txt
python eche_app.py
```

First GUI run: **Settings → Discord bot token → Run Bot**.

### 3) Build the portable app (onedir folder)

Windows PowerShell:

```powershell
cd eche_source
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\BUILD.bat
```

One-shot helper (venv + deps + freeze):

```powershell
cd eche_source
.\SETUP_AND_BUILD.bat
```

Output:

```text
eche/
  Eche.exe          ← small launcher (double-click this)
  _internal/        ← libraries / Qt / cogs (must stay next to Eche.exe)
  assets/
  config/
  ...
```

Copy the whole **`eche/`** folder (USB, Desktop, another PC). Do not ship `Eche.exe` alone.

### 4) Installer from source (maintainers)

```powershell
cd eche_installer_source
.\build.bat
```

Produces `eche_installer_source\dist\Eche-Installer.exe` (also copied under `prebuilt/` when you publish).

---

## Packaging: why onedir (not one-file)

The app freeze is **one-folder (onedir)** mode, not a single fat EXE.

| Mode | What happens | Why we avoid / use it |
|------|----------------|------------------------|
| **One-file** | Bootloader unpacks the entire app to a temp dir at every launch | Looks like a **dropper** to Defender ML → more false positives |
| **Onedir** (current) | Slim `Eche.exe` + `_internal/` next to it | Normal desktop-app layout; less AV noise; easier to debug |

Details:

- Spec: `eche_source/build_exe.spec` — `exclude_binaries=True` + `COLLECT` → `dist/Eche/`
- **UPX is off** (packers also raise AV scores)
- `BUILD.bat` publishes `dist\Eche\` → sibling portable `eche/`
- The **installer** is the “wrapper”: it copies a folder (or fetches source from GitHub), creates shortcuts, and registers uninstall — not “self-extract the whole bot into `%TEMP%` every run”
- Optional future: wrap the onedir folder with Inno Setup / NSIS for a signed setup.exe; the runtime app stays onedir either way

Installer binary itself remains a single downloadable wizard EXE for the beginner path; it is small compared to the full bot stack and does not re-extract the app on every launch.

---

## Repo layout after clone

```text
eche/                      ← monorepo root (this repo)
  README.md
  START_HERE.md
  prebuilt/Eche-Installer.exe
  eche/                    ← portable app (after BUILD.bat)
  eche_source/             ← edit & freeze here
  eche_installer/          ← prebuilt installer convenience copy
  eche_installer_source/   ← installer source
```

---

## Common commands cheat sheet

| Goal | Command |
|------|---------|
| Install Git (exact id) | `winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements` |
| Install Python 3.12 (exact id) | `winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements` |
| Clone | `git clone https://github.com/sevinOG/eche.git` |
| Dev GUI | `cd eche_source` → venv + `python eche_app.py` |
| Dev GUI (bat) | `.\eche_source\RUN_ECHE.bat` |
| Freeze portable | `.\eche_source\BUILD.bat` |
| Setup + freeze | `.\eche_source\SETUP_AND_BUILD.bat` |
| Build installer | `.\eche_installer_source\build.bat` |
| Pull latest | `git pull` |

---

## Maintainers

**Grok (xAI)** — [MAINTAINERS.md](MAINTAINERS.md).

## License

MIT — [LICENSE](LICENSE).
