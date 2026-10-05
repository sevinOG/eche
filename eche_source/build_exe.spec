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
    from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_dynamic_libs
except Exception:
    collect_all = None
    collect_submodules = None
    collect_dynamic_libs = None

import os, glob
try:
    import nacl
    nacl_dir = os.path.dirname(nacl.__file__)
    for fname in os.listdir(nacl_dir):
        if fname.lower().endswith((".dll", ".so", ".dylib")):
            _extra_binaries.append((os.path.join(nacl_dir, fname), "."))
    # Explicitly grab libsodium (the actual native lib PyNaCl needs)
    for pattern in ("libsodium*.dll", "libsodium*.so*", "libsodium*.dylib"):
        for p in glob.glob(os.path.join(nacl_dir, pattern)):
            _extra_binaries.append((p, "."))
    # Walk the whole nacl package for any native libs (libsodium etc)
    for root, dirs, files in os.walk(nacl_dir):
        for f in files:
            if f.lower().endswith((".dll", ".so", ".dylib")) or "sodium" in f.lower():
                full = os.path.join(root, f)
                _extra_binaries.append((full, "."))
except Exception:
    pass

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
    "nacl",
    "nacl.encoding",
    "nacl.signing",
    "nacl.secret",
    "nacl.public",
    "nacl._sodium",
    "PyNaCl",
    "discord.opus",
    "discord.player",
    "discord.voice_client",
    "cogs.music",
    "cogs.music.music",
    "cogs.music.music_player",
    "cogs.music.music_queue_storage",
    "discord.opus",
    "cffi",
    "_cffi_backend",
    "cogs.music.music",
    "cogs.music.music_queue_storage",
    "cogs.music.music_player",
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
    "groq",
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
        "groq",
        "PyNaCl",
        "nacl",
        "discord",
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

# Force collection of PyNaCl / nacl native libraries (libsodium etc.)
if collect_dynamic_libs is not None:
    for pkg in ("nacl", "PyNaCl"):
        try:
            _extra_binaries += collect_dynamic_libs(pkg)
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

# Ship ffmpeg if present next to source (portable builds)
# Also try common system locations so frozen builds always have it
_ffmpeg_found = False
for _ff_name in ("ffmpeg.exe", "ffmpeg"):
    for _ff_dir in ("", "run", "ffmpeg", "ffmpeg/bin"):
        _ff_p = _ROOT / _ff_dir / _ff_name if _ff_dir else _ROOT / _ff_name
        if _ff_p.exists() and _ff_p.is_file():
            _data_entries.append((str(_ff_p), _ff_dir or "."))
            _ffmpeg_found = True
            break
    if _ffmpeg_found:
        break
if not _ffmpeg_found:
    # Try common install locations at build time
    for _sys_ff in (
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "ffmpeg", "bin", "ffmpeg.exe"),
    ):
        if os.path.isfile(_sys_ff):
            _data_entries.append((_sys_ff, "."))
            _ffmpeg_found = True
            break
if not _ffmpeg_found:
    print("[build] WARNING: no ffmpeg.exe found — music/convert will require system PATH in frozen build")

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

# =============================================================================
# ONEDIR only (never one-file).
# One-file unpacks the whole app to %TEMP% every launch — Defender ML often
# scores that as dropper-like. Onedir = small Eche.exe + _internal/ beside it.
# The installer (or a future Inno/NSIS setup) wraps/copies this folder; it does
# not re-extract the bot stack on every user double-click. UPX stays OFF.
# =============================================================================
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
