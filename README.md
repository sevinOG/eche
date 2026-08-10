# Eche - Discord Bot Control Panel

Open-source Discord bot + desktop control panel.

**Repo:** https://github.com/sevinOG/eche · **Version:** see root `VERSION` (currently **1.3.1**)

**Formerly Echelon** — same project, rebranded to **Eche** (product, folders, EXE names). Older docs or shortcuts may still say "Echelon"; treat them as legacy names for this repo.

## Four forks only

| Fork | Folder | What |
| --- | --- | --- |
| **App (portable)** | `eche/` | **Build output** (onedir: `Eche.exe` + `_internal/`) — produced by `BUILD.bat` or auto-built by installer |
| **App source** | `eche_source/` | Python source + `BUILD.bat` (installer auto-builds this now) |
| **Installer (portable)** | `eche_installer/` / `prebuilt/` | Wizard: `Eche-Installer.exe` — **recommended** |
| **Installer source** | `eche_installer_source/` | Installer UI + `build.bat` |

---

## Recommended: ready to run (installer) - NEW USERS START HERE

**[Download Eche-Installer.exe](https://github.com/sevinOG/eche/releases/latest/download/Eche-Installer.exe)**  
*If the above direct link doesn't work, get it from **[Releases page](https://github.com/sevinOG/eche/releases)** or `prebuilt/Eche-Installer.exe` in the repo.*

1. Save and run **Eche-Installer.exe**

2. Keep **Install from GitHub** selected (now auto-builds Eche.exe - no batch files needed)

3. Choose a folder → **Install** → wait 2-3 min first time (needs Python from python.org, check **Add python.exe to PATH**)

4. **Eche.exe** launches automatically from `..\eche\Eche.exe` - Start Menu shortcut `Eche.lnk` also points to it

If auto-build fails, check `START_HERE.txt` in your install folder - it will tell you if Python is missing.

---

### Terminal Quick Install (installer only, then installer fetches the rest)

Don't clone the whole repo. Just grab the installer and let it download what you need:

#### Option A: One-liner to get installer (PowerShell)

```powershell
# Download installer only (tiny, ~10MB)
Invoke-WebRequest -Uri "https://github.com/sevinOG/eche/releases/latest/download/Eche-Installer.exe" -OutFile "Eche-Installer.exe"

# Run it - it will fetch the app source from GitHub and auto-build Eche.exe
.\Eche-Installer.exe
```

#### Option B: What the installer can fetch (you pick in the UI)

The installer now pulls from GitHub, you choose tier:

| Tier | What installer downloads | Who it's for |
| --- | --- | --- |
| **Bot only** | `eche/` portable app (`Eche.exe` + `_internal/`) - smallest | Just want to run the bot |
| **Bot + Source** | `eche/` + `eche_source/` full Python source | Want to run + tweak code |
| **Bot + Source + Installer Source** | `eche/` + `eche_source/` + `eche_installer_source/` | Full repo for maintaining installer |

> Current default: **Bot + Source** (fetches `eche_source/` and auto-builds `eche/`).  
> If you want Bot-only (faster), select **"Portable App from Release"** in installer instead of GitHub.



## Install: Command Line (for devs)

### For Developers (Git + terminal - full repo)

Use this if you're contributing code. For normal use, use the one-liner above.

#### 0) Install Git and Python first (Windows / PowerShell)

```powershell
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
```

After install: close PowerShell, open new window:

```powershell
git --version
python --version
```

#### 1) Clone (full)

```powershell
git clone https://github.com/sevinOG/eche.git
cd eche
```

This gives you **everything**: bot + source + installer source (your Tier 3).

#### 2) Build portable (devs)

```powershell
cd eche_source
.\SETUP_AND_BUILD.bat
# -> creates ../eche/Eche.exe
```

#### 3) Run from source

```powershell
cd eche_source
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe eche_app.py
```

#### 4) Build installer

```powershell
cd eche_installer_source
.\build.bat
# -> dist/Eche-Installer.exe
```

---

## Packaging: why onedir

| Mode | What happens | Why |
| --- | --- | --- |
| One-file | Unpacks to temp every launch | Defender false positives |
| Onedir (current) | `Eche.exe` + `_internal/` | Normal app layout |

Spec: `eche_source/build_exe.spec`

---

## Repo layout

```
eche/
  README.md  VERSION
  prebuilt/Eche-Installer.exe   <- download this only, it fetches rest
  eche/                    <- portable after build/installer (not in git)
  eche_source/             <- app source
  eche_installer_source/   <- installer source
```

---

## Common commands

| Goal | Command |
| --- | --- |
| **Install for users (fastest)** | `iwr -Uri https://github.com/sevinOG/eche/releases/latest/download/Eche-Installer.exe -OutFile Eche-Installer.exe; .\Eche-Installer.exe` |
| **Install portable only** | In installer UI, select "Portable App from Release" |
| **Install full source** | In installer UI, select "Install from GitHub" (default, auto-builds) |
| **Clone full repo (dev)** | `git clone https://github.com/sevinOG/eche.git` |
| **Build from clone** | `.\eche_source\SETUP_AND_BUILD.bat` |
| **Build installer** | `.\eche_installer_source\build.bat` |

---

## Privacy

Tokens stay local except Discord, Groq, Unsplash. See PRIVACY.md.

## License

MIT

## What Changed in v1.3.1

- Installer now auto-builds Eche.exe (needs Python) - no manual batch step
- Added direct download link + terminal one-liner (installer-only download)
- Installer can now fetch Bot / Bot+Source / Full tiers from GitHub
- Shortcuts point directly to Eche.exe
