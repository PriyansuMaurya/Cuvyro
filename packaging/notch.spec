# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Notch (Windows onedir build).

The app looks for the speech model at <exe dir>/model when frozen
(see ui_overlay.py), so the build script copies model/ next to Notch.exe
instead of bundling it inside _internal.
"""

import os

from PyInstaller.utils.hooks import collect_all

REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas, binaries, hiddenimports = [], [], []
for pkg in ("vosk", "sounddevice", "thefuzz", "rapidfuzz"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# PyQt5 is resolved by PyInstaller's own hooks; only silence noisy optional modules.
excludes = [
    "tkinter",
    "matplotlib",
    "pandas",
    "scipy",
    "IPython",
    "pytest",
    "PyQt5.QtWebEngineWidgets",
    "PyQt5.QtQuick",
    "PyQt5.QtQml",
]

a = Analysis(
    [os.path.join(REPO_ROOT, "main.py")],
    pathex=[REPO_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Notch",
    icon=os.path.join(SPECPATH, "notch.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # windowed: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Notch",
)
