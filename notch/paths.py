"""Filesystem locations shared by the app and its checks.

The speech and face models are deliberately not bundled by PyInstaller. A
frozen build copies ``model/`` next to ``Notch.exe``; a source checkout keeps
it at the repository root. Both the audio worker and the head tracker need
that rule, so it lives here instead of being re-derived in each module.
"""

import os
import sys


def app_dir() -> str:
    """The directory the app treats as its install root."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # notch/paths.py -> notch -> repository root.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def model_dir() -> str:
    """The folder holding the local speech and face models."""
    return os.path.join(app_dir(), "model")
