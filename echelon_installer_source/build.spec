# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Echelon Installer
Builds to dist/Echelon-Installer.exe

Usage:
  pyinstaller build.spec

Output:
  dist/Echelon-Installer.exe
  dist/Uninstall.exe (same binary, launched with --uninstall flag, but installer copies itself as Uninstall.exe)
"""

block_cipher = None

from pathlib import Path

_icon_ico = str(Path('assets/icon.ico').resolve()) if Path('assets/icon.ico').is_file() else None
_version = 'version_info.txt' if Path('version_info.txt').is_file() else None

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Brand art for title bar / taskbar (icon.png + icon.ico)
        ('assets', 'assets'),
    ],
    hiddenimports=[
        'PyQt6.sip',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        # optional shortcut helpers — omit hard fail if missing
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
    name='Echelon-Installer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX packers trigger many browser/AV false positives — keep OFF
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI app, no console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows Explorer / taskbar icon (must be multi-size .ico)
    icon=_icon_ico,
    version=_version,
)

# Optional collate? single exe is enough
