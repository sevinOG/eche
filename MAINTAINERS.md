# Maintainers

| Role | Notes |
|------|--------|
| Project | **Eche** open-source Discord bot + desktop panel |
| Coordination | Community / maintainers via **GitHub Issues** |

This repository is maintained in the open. Contributions and review may involve automated assistants (including models branded “Grok” from xAI) **and** human maintainers. Treat that as tooling, not a personal support channel.

## Contact

- **Bugs and features:** [GitHub Issues](https://github.com/sevinOG/eche/issues) on this repository  
- **Do not** send Discord tokens, API keys, or private server IDs in issues or email  
- There is no guaranteed private support inbox tied to any model brand name

## Principles

1. **Portable first** — finished apps run from a folder (USB / Desktop) when you build or install them.
2. **Source available** — every binary path has a documented source tree and build script.
3. **No secrets in git** — tokens and server IDs stay on the user's machine only.
4. **Onedir freezes** — app is never one-file PyInstaller (temp unpack ≈ dropper signal). Slim `Eche.exe` + `_internal/`; installer wraps/copies the folder. No UPX.
5. **Installer recommended** for ready-to-run — the `eche/` folder in git is build output, not a pre-shipped binary tree.

## Packaging notes

| Artifact | Layout | Spec / script |
|----------|--------|----------------|
| Portable app | onedir | `eche_source/build_exe.spec`, `BUILD.bat` → `eche/` |
| Installer wizard | single EXE (one download; sign when you can) | `eche_installer_source/build.spec` |
| Optional later | Inno/NSIS wrapping onedir | does not replace onedir runtime |

Public install docs: root [README.md](README.md). Privacy: [PRIVACY.md](PRIVACY.md).
 