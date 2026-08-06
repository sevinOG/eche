# Echelon Ecosystem

**Open-source Discord bot platform** with a desktop control panel (PyQt6) and a purple-themed installer.

This repository is the **public distribution hub**. It ships **source** (and an optional prebuilt installer).  
Runtime secrets (Discord tokens, API keys, server IDs) are **never** stored here.

**Maintainer:** [Grok](https://x.ai) (xAI)

---

## The four products (forks)

Echelon is intentionally split so “run”, “edit”, “install”, and “edit the installer” never get mixed up:

| Product | In this repo? | What it is |
|---------|---------------|------------|
| **Echelon app** (portable) | Built locally / Releases | Finished `Echelon.exe` — flash-drive ready |
| **Echelon source** | ✅ `echelon_source/` | Full app source + `BUILD.bat` |
| **Echelon installer** (portable) | ✅ `prebuilt/` (when present) | Finished `Echelon-Installer.exe` |
| **Echelon installer source** | ✅ `echelon_installer_source/` | Installer wizard source + `build.bat` |

```
echelon_ecosystem/                 ← you are here (GitHub)
├── echelon_source/                ← build the APP from source
│   ├── BUILD.bat                  ← freeze → ../echelon portable layout
│   ├── core/  cogs/  gui/
│   └── requirements.txt
├── echelon_installer_source/      ← build the INSTALLER from source
│   ├── build.bat
│   └── src/
├── prebuilt/                      ← optional Echelon-Installer.exe
├── README.md
├── MAINTAINERS.md
└── LICENSE
```

Local developers often keep four sibling folders after building:

```
echelon/                     # portable APP (output of BUILD.bat)
echelon_source/              # this repo's echelon_source/
echelon_installer/           # portable INSTALLER (output of build.bat)
echelon_installer_source/    # this repo's echelon_installer_source/
```

There is **no** separate runtime package named `echelon_ecosystem` anymore —  
imports are plain `core.*` / `cogs.*` / `gui.*` inside the app source.

---

## Quick start (users)

### A) Easiest — prebuilt installer

1. Download **`prebuilt/Echelon-Installer.exe`** (or a GitHub Release asset).
2. Run it.
3. Choose **Install from GitHub** (default) to fetch **Echelon source** into a folder you pick.
4. From that folder, create a venv and run `BUILD.bat` to produce the portable app  
   (or use Advanced options if you already have a built `Echelon.exe`).

### B) Clone source yourself

```bat
git clone https://github.com/sevinOG/echelon_ecosystem.git
cd echelon_ecosystem\echelon_source
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
BUILD.bat
```

The portable app is published next to the source as `..\echelon\` (when that folder exists)  
or under `dist\Echelon\` inside the source tree.

### C) Build the installer from source

```bat
cd echelon_ecosystem\echelon_installer_source
:: Prefer using the same Python venv as the app source if available
build.bat
```

---

## What Echelon does

- Modular Discord bot (economy, games, music, memory, hire cogs, …)
- Desktop GUI for settings, cogs, context, providers
- Secure local secrets (Windows DPAPI) — never committed
- Cloud AI (default: Groq) or local Ollama via settings

---

## Privacy

This repo is scrubbed of:

- Email addresses and personal contact info  
- Discord tokens / API keys  
- Home server IDs and other account identifiers  
- Local paths under a single developer machine  

If you fork, keep `config/settings.json` and `config/secrets.dpapi.json` **out of git**.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Maintainer note

Maintained with care by **Grok (xAI)** for open learning and portable bot infrastructure.  
Contributions welcome via Issues and Pull Requests.
