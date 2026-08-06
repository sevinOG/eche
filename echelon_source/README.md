# Echelon source (application)

Full open-source tree for the **Echelon** Discord bot + desktop GUI.

## Build portable app

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
BUILD.bat
```

Produces a frozen onedir under `dist\Echelon\` and publishes to sibling `..\echelon\` when present.

## Dev run (no freeze)

```bat
.venv\Scripts\python.exe echelon_app.py
```

## Layout

| Path | Role |
|------|------|
| `core/` | Bot core, paths, secrets, client, summarizer |
| `cogs/` | Discord extensions |
| `gui/` | PyQt6 control panel |
| `assets/` | `icon.png` / `icon.ico` |
| `BUILD.bat` | One-tap freeze + portable publish |
| `requirements.txt` | Runtime + build deps |

## Security

Do not commit:

- `config/settings.json`
- `config/secrets.dpapi.json`
- `.env`
- `cookies/`, personal `logs/`

## Related

- Installer source: `../echelon_installer_source/`
- Public hub: [sevinOG/echelon_ecosystem](https://github.com/sevinOG/echelon_ecosystem)

Maintainer: **Grok (xAI)**
