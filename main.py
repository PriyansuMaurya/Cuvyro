"""Compatibility launcher for a source checkout.

``start-cuvyro.bat``, the PyInstaller spec and the README have always used
``python main.py``. The application itself lives in :mod:`cuvyro.app`; this
module keeps that command working without duplicating the startup code.
"""

from cuvyro.app import main

if __name__ == "__main__":
    main()
