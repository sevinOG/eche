# START HERE (never used GitHub or AI?)

**Eche** (formerly **Echelon**) is an open-source Discord bot with a simple desktop window.

## Recommended: one download

**[⬇️ Eche-Installer.exe](https://github.com/sevinOG/eche/raw/main/prebuilt/Eche-Installer.exe)**

1. Save the file  
2. Run **Eche-Installer.exe**  
3. Keep **Install from GitHub** selected  
4. Choose a folder → Install  

If a browser blocks the file: unsigned open-source EXEs often get false positives. Use this official GitHub link only; Edge **Keep** / SmartScreen **Run anyway**. (Signing the installer is planned to improve this.)

## After install

1. Prefer **`Eche.exe`** if present → double-click it (keep `_internal/` next to it — onedir layout)  
2. If you only have **source** files → install free [Python](https://www.python.org/downloads/) (check **Add to PATH**), then double-click **`SETUP_AND_BUILD.bat`** once → open `dist\Eche\Eche.exe` or the published `eche\Eche.exe`  
3. **`RUN_ECHE.bat`** is optional (runs from Python without building)

Settings → Discord token → Run Bot.

A fresh clone of the repo does **not** include a ready `eche/Eche.exe` — use the installer above, or **build** from `eche_source` (below).

## Comfortable with Git / terminal?

```powershell
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
```

New PowerShell window — **build the app** (preferred), then launch:

```powershell
git clone https://github.com/sevinOG/eche.git
cd eche\eche_source
.\SETUP_AND_BUILD.bat
cd ..\eche
.\Eche.exe
```

(Developers who want to edit code without freezing: see [README.md](README.md) “Run from source”.)  
Privacy: [PRIVACY.md](PRIVACY.md).

## Four forks

| Name | Meaning |
|------|---------|
| **eche** | Portable app folder **after** you build (`Eche.exe` + `_internal/`) |
| **eche_source** | App recipe / code |
| **eche_installer** / **prebuilt** | Ready installer (recommended) |
| **eche_installer_source** | Installer recipe |

## Contact

Bugs & ideas: [GitHub Issues](https://github.com/sevinOG/eche/issues) — never paste tokens. See [MAINTAINERS.md](MAINTAINERS.md).
