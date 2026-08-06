# Maintainers

| Role | Name |
|------|------|
| Primary maintainer | **Grok** (xAI) |
| Project | Eche open-source Discord bot platform |

## Contact

- Prefer GitHub Issues on this repository for bugs and feature requests.
- Do not send Discord tokens, API keys, or personal server IDs in issues.

## Principles

1. **Portable first** — finished apps run from a folder (USB / Desktop) without installers if desired.
2. **Source available** — every binary path has a documented source tree and build script.
3. **No secrets in git** — tokens and server IDs stay on the user's machine only.
4. **Onedir freezes** — app is never one-file PyInstaller (temp unpack ≈ dropper signal). Slim `Eche.exe` + `_internal/`; installer wraps/copies the folder. No UPX.

## Packaging notes

| Artifact | Layout | Spec / script |
|----------|--------|----------------|
| Portable app | onedir | `eche_source/build_exe.spec`, `BUILD.bat` → `eche/` |
| Installer wizard | single EXE (small, one download) | `eche_installer_source/build.spec` |
| Optional later | Inno/NSIS setup wrapping onedir zip | does not replace onedir runtime |

Public install docs: root [README.md](README.md) (beginner + **git/terminal**).
