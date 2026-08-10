# Eche - Discord Bot Control Panel

Open-source Discord bot + desktop control panel.


---

QUICKSTART:

### 0) Install Git and Python

Download from:
- https://git-scm.com/downloads
- https://www.python.org/downloads (check **Add python.exe to PATH**)

Or with winget (CMD also works, not just PowerShell):
```
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e
```
Close CMD, open new CMD.

### 1) Clone ONLY installer (~10MB, not whole repo)

**CMD:**
```cmd
mkdir eche-setup
cd eche-setup
git clone --filter=blob:none --no-checkout https://github.com/sevinOG/eche.git .
git sparse-checkout set --no-cone prebuilt
git checkout main
prebuilt\Eche-Installer.exe
```

**Git Bash:**
```bash
git clone --filter=blob:none --no-checkout https://github.com/sevinOG/eche.git eche-setup
cd eche-setup
git sparse-checkout set --no-cone prebuilt
git checkout main
./prebuilt/Eche-Installer.exe
```

### What happens:

```
You cloned:  prebuilt/Eche-Installer.exe (installer only, 10MB)
       ↓
Installer fetches:  eche_source/ (bot source from GitHub)
       ↓
Installer auto-builds:  eche/Eche.exe (portable app)
```

### Other tiers (if you want source)

**Bot + Source:**
```cmd
git sparse-checkout set --no-cone prebuilt eche_source
git checkout main
```

**Full (Bot + Source + Installer Source):**
```cmd
git sparse-checkout set --no-cone prebuilt eche_source eche_installer_source
git checkout main
```

---

## For Developers (Full Clone)

Only if you're editing code.

```cmd
git clone https://github.com/sevinOG/eche.git
cd eche
cd eche_source
SETUP_AND_BUILD.bat
cd ..\eche
Eche.exe
```

Run from source without building:
```cmd
cd eche_source
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe eche_app.py
```

Build installer:
```cmd
cd eche_installer_source
build.bat
```

---

## Why onedir, not one-file

| Mode | Issue |
|---|---|
| One-file | Unpacks to temp every launch, looks like dropper to Defender |
| Onedir (we use) | `Eche.exe` + `_internal/` - normal app layout |

Spec: `eche_source/build_exe.spec`, UPX off.

---

## Repo Layout

```
eche/
  prebuilt/Eche-Installer.exe  <- download this, it fetches rest
  eche/                       <- built app after installer/BUILD.bat (not in git)
  eche_source/                <- bot source
  eche_installer_source/      <- installer source
```

---

## Common Commands (CMD)

| Goal | Command |
|---|---|
| Clone installer only | `git clone --filter=blob:none --no-checkout https://github.com/sevinOG/eche.git . && git sparse-checkout set --no-cone prebuilt && git checkout main` |
| Run installer | `prebuilt\Eche-Installer.exe` |
| Full clone | `git clone https://github.com/sevinOG/eche.git` |
| Build portable | `eche_source\SETUP_AND_BUILD.bat` |
| Build installer | `eche_installer_source\build.bat` |

<details>
<summary>PowerShell version (if you must)</summary>

```powershell
mkdir eche-setup; cd eche-setup
git clone --filter=blob:none --no-checkout https://github.com/sevinOG/eche.git .
git sparse-checkout set --no-cone prebuilt
git checkout main
.\prebuilt\Eche-Installer.exe
```

Or one-liner download:
```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/sevinOG/eche/main/prebuilt/Eche-Installer.exe" -OutFile "Eche-Installer.exe"
.\Eche-Installer.exe
```

</details>

---

## Privacy

Tokens stay local except Discord, Groq, Unsplash. See PRIVACY.md.

## Changelog v1.3.1

- Installer now auto-builds Eche.exe (2-3 min first time, needs Python)
- New terminal flow: clone installer only (10MB sparse checkout), installer fetches bot
- Removed manual SETUP_AND_BUILD.bat step for new users
- Shortcuts point directly to Eche.exe
