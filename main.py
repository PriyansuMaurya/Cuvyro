"""Compatibility launcher for a source checkout.

``start-notch.bat``, the PyInstaller spec and the README have always used
``python main.py``. The application itself lives in :mod:`notch.app`; this
module keeps that command working without duplicating the startup code.
"""

from notch.app import main

if __name__ == "__main__":
    main()
