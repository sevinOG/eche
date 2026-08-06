# START HERE (never used GitHub or AI?)

## From zero (3 steps)

| Step | What |
|------|------|
| **1** | Install Git + Python |
| **2** | Get the installer and run it |
| **3** | (Optional) Clone app source for full dev access |

Full copy-paste PowerShell (exact package ids):  
**[eche_installer_source/README.md](eche_installer_source/README.md)** · summary in **[README.md](README.md)**

### Step 1 — tools

```powershell
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
```

Close PowerShell, open a **new** window.

### Step 2 — installer

```powershell
$uri = "https://github.com/sevinOG/eche/raw/main/prebuilt/Eche-Installer.exe"
$out = Join-Path $env:USERPROFILE "Downloads\Eche-Installer.exe"
Invoke-WebRequest -Uri $uri -OutFile $out -UseBasicParsing
Start-Process $out
```

Wizard: **Install from GitHub** → folder → Install.

### Step 3 — optional app source

```powershell
git clone https://github.com/sevinOG/eche.git
cd eche\eche_source
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe eche_app.py
```

---

## One download only (no terminal)

**[⬇️ Eche-Installer.exe](https://github.com/sevinOG/eche/raw/main/prebuilt/Eche-Installer.exe)**

1. Save the file  
2. Run **Eche-Installer.exe**  
3. Keep **Install from GitHub** selected  
4. Choose a folder → Install  

If a browser blocks the file: open-source EXEs without a paid signature often get false positives. Use this official GitHub link only; Edge **Keep** / SmartScreen **Run anyway**.

## After install

- **`Eche.exe`** present → double-click it (keep `_internal/` next to it — onedir layout)  
- Only source files → **`RUN_ECHE.bat`**, or **SETUP_AND_BUILD.bat** after Python is installed  

Settings → Discord token → Run Bot.

## Four forks

| Name | Meaning |
|------|---------|
| **eche** | Finished app folder (`Eche.exe` + `_internal/`) |
| **eche_source** | App recipe / code |
| **eche_installer** | Finished installer |
| **eche_installer_source** | Installer recipe |

**Maintainer:** Grok (xAI)
