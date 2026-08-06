# Echelon Ecosystem

Open-source **Discord bot + desktop control panel** — built so a complete beginner can start from a blank Windows PC.

**Maintainer:** [Grok](https://x.ai) (xAI)

---

## New here? Start with ONE download

### [Download Echelon Installer (Windows)](https://github.com/sevinOG/echelon_ecosystem/raw/main/prebuilt/Echelon-Installer.exe)

1. Click the link above (one file).  
2. Run `Echelon-Installer.exe`.  
3. Keep **Install from GitHub** selected.  
4. Choose a folder → Install.  

Full plain-English walkthrough: **[START_HERE.md](START_HERE.md)**

You do **not** need to know Git, AI, or the command line to use the installer.

---

## What you get

| Product | Where | For whom |
|---------|--------|----------|
| **Installer (portable)** | [`prebuilt/Echelon-Installer.exe`](prebuilt/Echelon-Installer.exe) | **Everyone new** |
| **App source** | [`echelon_source/`](echelon_source/) | Learning & customizing |
| **Installer source** | [`echelon_installer_source/`](echelon_installer_source/) | People who rebuild the wizard |
| **Portable app** | Built by installer / `SETUP_AND_BUILD.bat` / Releases | Day-to-day use |

There is no separate mystery package called “ecosystem” at runtime.  
That name is this **download hub**. The app code uses normal folders: `core/`, `cogs/`, `gui/`.

---

## How the installer works (default path)

```
You download Installer.exe
        ↓
Installer fetches echelon_source from this GitHub repo
        ↓
Installer runs the same path as “install from source”
        ↓
Your folder has the project (+ Echelon.exe if a Release portable app exists)
        ↓
Double-click Echelon.exe  OR  SETUP_AND_BUILD.bat (once, needs free Python)
```

Local / recovery tools are under **Advanced** in the installer (hidden by default).

---

## Features (once running)

- Modular Discord bot (economy, games, music, memory, hire cogs, …)
- Desktop GUI for settings, cogs, context, providers
- Secure local secrets (Windows DPAPI) — never committed
- Cloud AI (default: Groq) or local Ollama via settings

---

## Advanced: build yourself

```bat
git clone https://github.com/sevinOG/echelon_ecosystem.git
cd echelon_ecosystem\echelon_source
SETUP_AND_BUILD.bat
```

Or step-by-step: `python -m venv .venv` → `pip install -r requirements.txt` → `BUILD.bat`.

Installer rebuild:

```bat
cd echelon_ecosystem\echelon_installer_source
build.bat
```

---

## Safety & privacy

- This repo does **not** contain Discord tokens, API keys, or your server IDs.  
- Never paste secrets into GitHub Issues.  
- If you fork, keep `config/settings.json` and `config/secrets.dpapi.json` **out of git**.  
- Windows may show SmartScreen on first run of open-source EXEs → *More info → Run anyway*.

---

## License

MIT — see [LICENSE](LICENSE).

## Maintainers

See [MAINTAINERS.md](MAINTAINERS.md). Primary maintainer: **Grok (xAI)**.
