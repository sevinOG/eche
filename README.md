# Eche Ecosystem

Open-source **Discord bot + desktop control panel** — beginner-friendly, portable, and free.

> **Rebrand note:** This product is **Eche** (formerly “Echelon”). The old name collides with known malware labels and caused download blockers. Executables are now `Eche-Installer.exe` / `Eche.exe`.

**Maintainer:** [Grok](https://x.ai) (xAI)

---

## New here? ONE download

### [Download Eche-Installer.exe (Windows)](https://github.com/sevinOG/echelon_ecosystem/raw/main/prebuilt/Eche-Installer.exe)

1. Click the link → Save the file  
2. Run **`Eche-Installer.exe`**  
3. Keep **Install from GitHub** selected  
4. Choose a folder → Install  

Plain-English guide: **[START_HERE.md](START_HERE.md)**

You do **not** need Git, AI knowledge, or the command line to use the installer.

If a browser says “virus detected”: that is a common **false positive** for new unsigned open-source EXEs. Prefer this official GitHub link only; use Edge “Keep” / SmartScreen **More info → Run anyway** if needed.

---

## What you get

| Product | Where | Who |
|---------|--------|-----|
| **Installer** | [`prebuilt/Eche-Installer.exe`](prebuilt/Eche-Installer.exe) | **Everyone new** |
| **App source** | [`eche_source/`](eche_source/) | Learning & customizing |
| **Installer source** | [`eche_installer_source/`](eche_installer_source/) | Advanced rebuilds |
| **Portable app** | Built via installer / `SETUP_AND_BUILD.bat` | Daily use (`Eche.exe`) |

---

## How install works (default)

```
Download Eche-Installer.exe
        ↓
Fetches eche_source from this GitHub repo
        ↓
Installs that tree (same as “install from source”)
        ↓
Open START_HERE.txt → Eche.exe if present, else SETUP_AND_BUILD.bat
```

Advanced local EXE / recovery options are collapsed in the installer.

---

## Advanced: build yourself

```bat
git clone https://github.com/sevinOG/echelon_ecosystem.git
cd echelon_ecosystem\eche_source
SETUP_AND_BUILD.bat
```

Installer rebuild:

```bat
cd echelon_ecosystem\eche_installer_source
build.bat
```

---

## Safety & privacy

- No Discord tokens, API keys, or server IDs in this repo  
- Never paste secrets into Issues  
- Builds ship **without UPX**; product name is **Eche** (not Echelon) to avoid malware-name collisions  

---

## License

MIT — see [LICENSE](LICENSE).

## Maintainers

See [MAINTAINERS.md](MAINTAINERS.md). Primary: **Grok (xAI)**.
