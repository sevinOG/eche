# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

sys.setrecursionlimit(5000)
block_cipher = None
# SPECPATH is injected by PyInstaller when it executes this file
try:
    _ROOT = Path(SPECPATH).resolve()
except NameError:
    _ROOT = Path(".").resolve()

# Pull in packages PyInstaller often misses for frozen bot builds
try:
    from PyInstaller.utils.hooks import collect_all, collect_submodules
except Exception:
    collect_all = None
    collect_submodules = None

_extra_datas = []
_extra_binaries = []
_extra_hidden = [
    "core.eche",
    "core.bot",
    "core.client",
    "core.paths",
    "core.secrets",
    "gui.main",
    "dateparser",
    "dateparser.conf",
    "dateparser.date",
    "dateparser.search",
    "dateparser.utils",
    "dateparser_data",
    "dateparser_scripts",
    "regex",
    "tzlocal",
    "pytz",
    "zoneinfo",
    # music / voice stack (must ship in flash-drive portable builds)
    "yt_dlp",
    "yt_dlp.utils",
    "yt_dlp.extractor",
    "yt_dlp.downloader",
    "yt_dlp.postprocessor",
    "aiohttp",
    "requests",
    "certifi",
    "charset_normalizer",
    "idna",
    "urllib3",
    "psutil",
    "dotenv",
]

if collect_all is not None:
    for pkg in (
        "dateparser",
        "regex",
        "tzlocal",
        "pytz",
        "yt_dlp",
        "aiohttp",
        "certifi",
    ):
        try:
            d, b, h = collect_all(pkg)
            _extra_datas += d
            _extra_binaries += b
            _extra_hidden += h
        except Exception:
            pass
if collect_submodules is not None:
    for pkg in ("dateparser", "yt_dlp"):
        try:
            _extra_hidden += collect_submodules(pkg)
        except Exception:
            pass

# Runtime data folders are gitignored and may be missing after a fresh clone.
# Create empty shells so PyInstaller does not abort, and only ship paths that exist.
_data_entries = []
for _src_name, _dest in (
    ("cogs", "cogs"),
    ("core", "core"),
    ("gui", "gui"),
    ("config", "config"),
    ("context", "context"),
    ("cookies", "cookies"),
    ("logs", "logs"),
    ("memories", "memories"),
    ("assets", "assets"),
):
    _p = _ROOT / _src_name
    if _src_name in ("context", "cookies", "logs", "memories"):
        _p.mkdir(parents=True, exist_ok=True)
        # ensure non-empty so tree-copy tools keep the folder
        _keep = _p / ".gitkeep"
        if not _keep.is_file():
            _keep.write_text("", encoding="utf-8")
    if _p.exists():
        _data_entries.append((str(_p), _dest))
_version = _ROOT / "VERSION"
if _version.is_file():
    _data_entries.append((str(_version), "."))

a = Analysis(
    ['eche_app.py'],
    pathex=[str(_ROOT)],
    binaries=_extra_binaries,
    datas=_data_entries + _extra_datas,
    hiddenimports=sorted(set(_extra_hidden)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['rthook_eche.py'],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onedir layout: slim bootloader EXE + _internal next to it
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Eche',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX causes browser/AV false positives — keep off
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Multi-size .ico for Explorer + taskbar
    icon=str((_ROOT / 'assets' / 'icon.ico').resolve()) if (_ROOT / 'assets' / 'icon.ico').is_file() else None,
    version=str(_ROOT / 'version_info.txt') if (_ROOT / 'version_info.txt').is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Eche'
)
