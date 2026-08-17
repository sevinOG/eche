
```markdown
# Eche

Eche is an open-source Discord bot + desktop control panel, preloaded with games and utilities.

Eche can run with or without an inference engine, with inference all your context stays on discord. 
Without inference bot reference files and images stay stored on discord only.

*free storage means read/write rate limtations via the discord api*

Tokens and secrets stay on your machine. See [PRIVACY.md](PRIVACY.md).

**Version:** see [VERSION](VERSION) (currently **1.3.0**)

**Repo:** https://github.com/sevinOG/eche

---

## Quick start (recommended)

### 0) Install Git and Python (Windows)

Download:

- https://git-scm.com/downloads  
- https://www.python.org/downloads — check **Add python.exe to PATH**

Or with winget (then **close and reopen** the terminal):

```cmd
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
```

### 1) Download the installer

Use **only** these official GitHub links (unsigned builds may trip SmartScreen — **More info → Run anyway**).

| Method | Link |
|--------|------|
| **Direct download** | [Eche-Installer.exe](https://github.com/sevinOG/eche/raw/main/eche_installer/final/Eche-Installer.exe) |
| **Browse folder** | [eche_installer/final/](https://github.com/sevinOG/eche/tree/main/eche_installer/final) |
| **Repo home** | [github.com/sevinOG/eche](https://github.com/sevinOG/eche) |

**Option A — browser**

1. Download: [Eche-Installer.exe](https://github.com/sevinOG/eche/raw/main/eche_installer/final/Eche-Installer.exe)
2. Run it (SmartScreen: official link only → **More info** → **Run anyway**).
3. Prefer **Install from GitHub**, pick a folder, install.
4. Open **Eche.exe** (keep the `_internal` folder next to it).

**Option B — PowerShell**

```powershell
Invoke-WebRequest -Uri "https://github.com/sevinOG/eche/raw/main/eche_installer/final/Eche-Installer.exe" -OutFile "$env:USERPROFILE\Downloads\Eche-Installer.exe"
& "$env:USERPROFILE\Downloads\Eche-Installer.exe"
```

**Option C — sparse clone (installer only)**

```cmd
mkdir eche
cd eche
git clone --filter=blob:none --no-checkout https://github.com/sevinOG/eche.git .
git sparse-checkout set --no-cone eche_installer/final
git checkout main
eche_installer\final\Eche-Installer.exe
```

### What the installer does

```
Eche-Installer.exe
       ↓
Fetches eche_source/ from GitHub (live main)
       ↓
Builds portable app (first time ~2–3 min; needs Python on PATH)
       ↓
Eche.exe + _internal/  (onedir layout)
```

Default install location is typically `%LOCALAPPDATA%\Eche`.

---

## For developers (full clone)

```cmd
git clone https://github.com/sevinOG/eche.git
cd eche\eche_source
SETUP_AND_BUILD.bat
cd ..\eche
Eche.exe
```

Keep `_internal` next to `Eche.exe`. Do not distribute `Eche.exe` alone.

### Run from source (no freeze)

```cmd
cd eche_source
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe eche_app.py
```

Or double-click **`RUN_ECHE.bat`** (refreshes deps from `requirements.txt` when the venv already exists).

Optional verbose logs: set `ECHE_DEBUG=1` before launching.

### Build the installer

```cmd
cd eche_installer_source
build.bat
```

Copy `dist\Eche-Installer.exe` to `eche_installer\final\` for the public download path above.

---

## AI provider (Groq)

Cloud mode uses the official **`groq`** Python SDK. Default model:

```text
qwen/qwen3.6-27b
```

(Older `llama-3.3-70b-versatile` is deprecated on Groq.)

Dependency is listed in `eche_source/requirements.txt` as `groq>=0.9.0`.  
Source runs / rebuilds install it via `pip install -r requirements.txt`.  
Frozen `Eche.exe` needs a **rebuild** after adding the package so it appears in `_internal`.

---

## Why onedir (not one-file)

| Mode | Issue |
|------|--------|
| One-file | Unpacks under `%TEMP%` every launch — often flagged like a dropper |
| Onedir (we use) | Small `Eche.exe` + `_internal/` beside it — normal app layout |

Spec: `eche_source/build_exe.spec` · UPX off · `console=False`.

---

## Repo layout

```
eche/
  eche_installer/final/Eche-Installer.exe   ← ready-to-run installer (download this)
  eche_installer_source/                    ← installer source + build scripts
  eche_source/                              ← app source + BUILD.bat
  eche/                                     ← portable app after build (local; not required in git)
```

| Path | Role |
|------|------|
| `eche_installer/final/` | Prebuilt installer for end users |
| `eche_source/` | Bot + GUI source; run `SETUP_AND_BUILD.bat` or `BUILD.bat` |
| `eche_installer_source/` | Wizard source; run `build.bat` to produce the installer |
| `eche/` | Output folder after a successful app freeze |

---

## Common commands

| Goal | Command / link |
|------|----------------|
| Download installer | [Eche-Installer.exe](https://github.com/sevinOG/eche/raw/main/eche_installer/final/Eche-Installer.exe) |
| PowerShell download | `Invoke-WebRequest -Uri "https://github.com/sevinOG/eche/raw/main/eche_installer/final/Eche-Installer.exe" -OutFile "Eche-Installer.exe"` |
| Full clone | `git clone https://github.com/sevinOG/eche.git` |
| Build portable app | `eche_source\SETUP_AND_BUILD.bat` |
| Build installer | `eche_installer_source\build.bat` |
| Run from source | `eche_source\RUN_ECHE.bat` |

---

## After install / first run

1. Launch **Eche.exe** (not a random `.bat` if the EXE exists).
2. Settings → Discord bot token (stored locally).
3. **AI & Model** → Cloud (Groq) → API key from [console.groq.com](https://console.groq.com/) → Model ID `qwen/qwen3.6-27b` if needed → **Save**.
4. Run the bot from the control panel.

If you only have source: install Python → `SETUP_AND_BUILD.bat` once → use `..\eche\Eche.exe` or `dist\Eche\Eche.exe`.

---

## Privacy

No Eche cloud account. Tokens stay local except what you send to Discord / optional providers (e.g. Groq, Unsplash). Details: [PRIVACY.md](PRIVACY.md).

**Never paste tokens or API keys into GitHub Issues.**

---

## Troubleshooting

| Symptom | What to try |
|---------|-------------|
| SmartScreen / “virus” flag | Official GitHub download only → More info → Run anyway. Unsigned PyInstaller builds often false-positive. |
| Installer keeps old app | Uninstall → delete `%LOCALAPPDATA%\Eche` → re-download installer → Install from GitHub |
| `groq` package not installed | Source: `pip install -r requirements.txt` or `RUN_ECHE.bat`. EXE: rebuild with `SETUP_AND_BUILD.bat` |
| Model still shows Llama | Settings → set Model ID to `qwen/qwen3.6-27b` → Save → restart bot |
| No `Eche.exe` after install | Python on PATH; re-run install or `eche_source\SETUP_AND_BUILD.bat` |
| Black console on launch | Run `Eche.exe` next to `_internal` |
| Build fails | Close `Eche.exe`, delete `build` and `dist` under `eche_source`, retry; prefer Python 3.12 |

---

## Version

See root [VERSION](VERSION). Installer and app versions may differ slightly until both are rebuilt from the same tree.
```

**Official download (copy-paste):**  
https://github.com/sevinOG/eche/raw/main/eche_installer/final/Eche-Installer.exe
