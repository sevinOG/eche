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

### Prerequisites

- [Git](https://git-scm.com/downloads)
- [Python 3.11+](https://www.python.org/downloads/) (Windows: check **Add python.exe to PATH**)
- Windows for the portable freeze (`.bat` scripts). Linux/mac can run the GUI from source with Qt available.

### 1) Clone

```bash
git clone https://github.com/sevinOG/eche.git
cd eche
```

### 2) Run from source (fastest for development)

**PowerShell / cmd (Windows):**

```bat
cd eche_source
python -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe eche_app.py
```

Or after the venv exists:

```bat
cd eche_source
RUN_ECHE.bat
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

Windows:

```bat
cd eche_source
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
BUILD.bat
```

One-shot helper (venv + deps + freeze):

```bat
cd eche_source
SETUP_AND_BUILD.bat
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

```bat
cd eche_installer_source
build.bat
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
| Clone | `git clone https://github.com/sevinOG/eche.git` |
| Dev GUI | `cd eche_source` → venv + `python eche_app.py` |
| Dev GUI (bat) | `eche_source\RUN_ECHE.bat` |
| Freeze portable | `eche_source\BUILD.bat` |
| Setup + freeze | `eche_source\SETUP_AND_BUILD.bat` |
| Build installer | `eche_installer_source\build.bat` |
| Pull latest | `git pull` |

---

## Maintainers

**Grok (xAI)** — [MAINTAINERS.md](MAINTAINERS.md).

## License

MIT — [LICENSE](LICENSE).
