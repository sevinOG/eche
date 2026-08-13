```markdown
# Eche

Open-source Discord bot + desktop control panel.

Tokens and secrets stay on your machine. See [PRIVACY.md](PRIVACY.md).

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

**Option A — browser**

1. Download:  
   [Eche-Installer.exe](https://github.com/sevinOG/eche/raw/main/eche_installer/final/Eche-Installer.exe)  
2. Run it (SmartScreen may warn on unsigned builds — use this official link only → **More info** → **Run anyway**).
3. Prefer **Install from GitHub**, pick a folder, install.
4. When finished, open **Eche.exe** (keep the `_internal` folder next to it).

**Option B — PowerShell**

```powershell
Invoke-WebRequest -Uri "https://github.com/sevinOG/eche/raw/main/eche_installer/final/Eche-Installer.exe" -OutFile "Eche-Installer.exe"
.\Eche-Installer.exe
```

**Option C — sparse clone (installer only)**

```cmd
mkdir eche-setup
cd eche-setup
git clone --filter=blob:none --no-checkout https://github.com/sevinOG/eche.git .
git sparse-checkout set --no-cone eche_installer/final
git checkout main
eche_installer\final\Eche-Installer.exe
```

### What the installer does

```
Eche-Installer.exe
       ↓
Fetches eche_source/ from GitHub
       ↓
Builds portable app (first time ~2–3 min; needs Python on PATH)
       ↓
Eche.exe + _internal/  (onedir layout)
```

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

Or: `RUN_ECHE.bat`

Optional verbose logs: set `ECHE_DEBUG=1` before launching.

### Build the installer

```cmd
cd eche_installer_source
build.bat
```

Output is typically under `dist\` and may be copied to `eche_installer\final\`.

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
  eche_installer/final/Eche-Installer.exe   ← ready-to-run installer
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

| Goal | Command |
|------|---------|
| Download installer (PowerShell) | `Invoke-WebRequest -Uri "https://github.com/sevinOG/eche/raw/main/eche_installer/final/Eche-Installer.exe" -OutFile "Eche-Installer.exe"` |
| Sparse clone installer only | See Quick start → Option C |
| Full clone | `git clone https://github.com/sevinOG/eche.git` |
| Build portable app | `eche_source\SETUP_AND_BUILD.bat` |
| Build installer | `eche_installer_source\build.bat` |
| Run from source | `eche_source\.venv\Scripts\python.exe eche_app.py` |

---

## After install / first run

1. Launch **Eche.exe** (not a random `.bat` if the EXE exists).
2. Set your Discord bot token in Settings (stored locally).
3. Run the bot from the control panel.

If you only have source: install Python → `SETUP_AND_BUILD.bat` once → use `..\eche\Eche.exe` or `dist\Eche\Eche.exe`.

---

## Privacy

No Eche cloud account. Tokens stay local except what you send to Discord / optional providers (e.g. Groq, Unsplash). Details: [PRIVACY.md](PRIVACY.md).

**Never paste tokens or API keys into GitHub Issues.**

---

## Troubleshooting

| Symptom | What to try |
|---------|-------------|
| SmartScreen blocks installer | Official GitHub download only → More info → Run anyway |
| No `Eche.exe` after install | Ensure Python is on PATH; re-run install or `eche_source\SETUP_AND_BUILD.bat` |
| Black console window on launch | Run `Eche.exe` next to `_internal`, not `python` / a broken `.bat` |
| Missing desktop shortcut | Create a shortcut to `Eche.exe` manually, or reinstall after a successful build |
| Build fails | Close running `Eche.exe`, delete `build` and `dist` under `eche_source`, retry; prefer Python 3.12 |

---

## Version

See root [VERSION](VERSION). Packaging notes and onedir policy are described above; installer and app versions may differ slightly until both are rebuilt from the same tag.
```
