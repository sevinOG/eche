# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

sys.setrecursionlimit(5000)
block_cipher = None

# Pull in packages PyInstaller often misses for frozen bot builds
try:
    from PyInstaller.utils.hooks import collect_all, collect_submodules
except Exception:
    collect_all = None
    collect_submodules = None

_extra_datas = []
_extra_binaries = []
_extra_hidden = [
    "core.echelon",
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

a = Analysis(
    ['echelon_app.py'],
    pathex=[],
    binaries=_extra_binaries,
    datas=[
        ('cogs', 'cogs'),
        ('core', 'core'),
        ('gui', 'gui'),
        ('config', 'config'),
        ('context', 'context'),
        ('cookies', 'cookies'),
        ('logs', 'logs'),
        ('memories', 'memories'),
        ('assets', 'assets'),
        ('VERSION', '.'),
    ] + _extra_datas,
    hiddenimports=sorted(set(_extra_hidden)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['rthook_echelon.py'],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Echelon',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows embeds .ico in the exe; source brand art is assets/icon.png
    icon=str(Path('assets/icon.ico')) if Path('assets/icon.ico').is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='Echelon'
)
