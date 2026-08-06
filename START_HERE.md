# START HERE (never used GitHub or AI?)

## One download

**[⬇️ Eche-Installer.exe](https://github.com/sevinOG/eche/raw/main/prebuilt/Eche-Installer.exe)**

1. Save the file  
2. Run **Eche-Installer.exe**  
3. Keep **Install from GitHub** selected  
4. Choose a folder → Install  

If a browser blocks the file: open-source EXEs without a paid signature often get false positives. Use this official GitHub link only; Edge **Keep** / SmartScreen **Run anyway**.

## After install

- **`Eche.exe`** present → double-click it (keep `_internal/` next to it — onedir layout)  
- Only source files → **`RUN_ECHE.bat`**, or install free [Python](https://www.python.org/downloads/) (check **Add to PATH**) then **SETUP_AND_BUILD.bat**

Settings → Discord token → Run Bot.

## Comfortable with Git / terminal?

```bat
git clone https://github.com/sevinOG/eche.git
cd eche\eche_source
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe eche_app.py
```

Full guide: [README.md](README.md) (clone, source run, portable build, onedir packaging).

## Four forks

| Name | Meaning |
|------|---------|
| **eche** | Finished app folder (`Eche.exe` + `_internal/`) |
| **eche_source** | App recipe / code |
| **eche_installer** | Finished installer |
| **eche_installer_source** | Installer recipe |

**Maintainer:** Grok (xAI)
