# Eche

Open-source Discord bot + desktop control panel.

**Repo:** [github.com/sevinOG/eche](https://github.com/sevinOG/eche)

## Four forks only

| Fork | Folder | What |
|------|--------|------|
| **App (portable)** | `eche/` | Onedir app: `Eche.exe` + `_internal/` |
| **App source** | `eche_source/` | Code + `BUILD.bat` / `SETUP_AND_BUILD.bat` |
| **Installer (portable)** | `eche_installer/` | Wizard: `dist\Eche-Installer.exe` |
| **Installer source** | `eche_installer_source/` | Wizard UI + `build.bat` — [from-zero guide](eche_installer_source/README.md) |

---

## From zero (terminal) — recommended learning path

| Step | What | Required? |
|------|------|-----------|
| **1** | Install **Git** + **Python** | Yes |
| **2** | Install / run the **installer** | Yes |
| **3** | Clone **app source** (edit bot, freeze yourself) | Optional |

Deep dive for steps 1–3 lives in **[eche_installer_source/README.md](eche_installer_source/README.md)**. Summary below.

### Step 1 — Git + Python (exact winget package ids)

Open **PowerShell**. Use `--id` + `-e`. Do **not** pass bare names like `git` or `python`.

```powershell
# Git for Windows  — package id: Git.Git
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements

# Python 3.12      — package id: Python.Python.3.12  (3.11+ works; stay off Python 2)
winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
```

| Tool | Exact winget package id |
|------|-------------------------|
| Git | `Git.Git` |
| Python 3.12 (recommended) | `Python.Python.3.12` |
| Python 3.11 | `Python.Python.3.11` |
| Python 3.13 | `Python.Python.3.13` |

Close PowerShell, open a **new** window, then:

```powershell
git --version
python --version
py -0p
```

### Step 2 — Install the installer (terminal)

**Option A — download prebuilt wizard**

```powershell
$uri = "https://github.com/sevinOG/eche/raw/main/prebuilt/Eche-Installer.exe"
$out = Join-Path $env:USERPROFILE "Downloads\Eche-Installer.exe"
Invoke-WebRequest -Uri $uri -OutFile $out -UseBasicParsing
Start-Process $out
```

Wizard: keep **Install from GitHub** → pick a folder → **Install** → open folder → `RUN_ECHE.bat` or `Eche.exe`.

**Option B — build wizard from source**

```powershell
git clone https://github.com/sevinOG/eche.git
cd eche\eche_installer_source
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\build.bat
.\install.bat
```

Full detail: [eche_installer_source/README.md](eche_installer_source/README.md).

### Step 3 — Optional: app source (greater dev access)

Skip if the wizard is enough. Use this to edit the bot or freeze a portable onedir yourself.

```powershell
git clone https://github.com/sevinOG/eche.git
cd eche\eche_source
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe eche_app.py
# optional freeze → ../eche/Eche.exe + _internal/
.\BUILD.bat
```

---

## One download (no terminal) — moved down

Prefer the path above when learning. If you only want a double-click:

**[Download Eche-Installer.exe](https://github.com/sevinOG/eche/raw/main/prebuilt/Eche-Installer.exe)**

1. Save and run **Eche-Installer.exe**
2. Keep **Install from GitHub** selected
3. Choose a folder → **Install**
4. Open the folder → **`RUN_ECHE.bat`** or **`Eche.exe`** if present

Hand-holding: [START_HERE.md](START_HERE.md).

Unsigned open-source EXEs often trip SmartScreen/Defender. Prefer the official GitHub link; Edge **Keep** / SmartScreen **Run anyway**.

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
  eche_installer_source/   ← installer source (from-zero steps 1–3)
```

---

## Common commands cheat sheet

| Goal | Command |
|------|---------|
| Install Git (exact id) | `winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements` |
| Install Python 3.12 (exact id) | `winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements` |
| Download installer | `Invoke-WebRequest` → `prebuilt/Eche-Installer.exe` (see Step 2) |
| Build installer | `.\eche_installer_source\build.bat` |
| Clone | `git clone https://github.com/sevinOG/eche.git` |
| Dev GUI | `cd eche_source` → venv + `python eche_app.py` |
| Dev GUI (bat) | `.\eche_source\RUN_ECHE.bat` |
| Freeze portable | `.\eche_source\BUILD.bat` |
| Setup + freeze | `.\eche_source\SETUP_AND_BUILD.bat` |
| Pull latest | `git pull` |

---

## Maintainers

**Grok (xAI)** — [MAINTAINERS.md](MAINTAINERS.md).

## License

MIT — [LICENSE](LICENSE).
