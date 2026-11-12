# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Notch (Windows onedir build).

The app looks for the speech model at <exe dir>/model when frozen
(see ui_overlay.py), so the build script copies model/ next to Notch.exe
instead of bundling it inside _internal.

sherpa-onnx is the engine the app prefers. Its wheel carries the sherpa and
onnxruntime DLLs inside ``sherpa_onnx/lib``, which PyInstaller's own hooks know
nothing about, so the package is collected whole and keeps that layout next to
``_sherpa_onnx.pyd``.
"""

import os

from PyInstaller.utils.hooks import collect_all

REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas, binaries, hiddenimports = [], [], []
# cv2 and mediapipe are collected whole: each carries native binaries and data
# files PyInstaller's own hooks do not know about, and head tracking needs both.
# The landmark model itself is a file in model/, copied next to the exe.
for pkg in ("sherpa_onnx", "vosk", "sounddevice", "thefuzz", "rapidfuzz", "cv2",
            "mediapipe"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Imported from inside AudioWorker.run, so name it here rather than rely on the
# analyser walking a function-local import.
hiddenimports.append("speech_engine")

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
    version=os.path.join(SPECPATH, "version_info.txt"),
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
