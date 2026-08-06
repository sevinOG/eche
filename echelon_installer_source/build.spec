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

_icon_ico = 'assets/icon.ico' if Path('assets/icon.ico').is_file() else None

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Brand art for title bar / taskbar (icon.png primary)
        ('assets', 'assets'),
    ],
    hiddenimports=[
        'PyQt6.sip',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'win32com',
        'win32com.client',
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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI app, no console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows embeds .ico; Qt UI loads assets/icon.png at runtime
    icon=_icon_ico,
)

# Optional collate? single exe is enough
