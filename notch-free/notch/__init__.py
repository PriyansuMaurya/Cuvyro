"""Notch, a local offline teleprompter.

The package is deliberately split by responsibility: ``ui`` owns the Qt
surfaces, ``audio`` the microphone and recognition pipeline, ``tracking`` the
camera and motion policy, ``platform`` the Windows integration, and
``storage`` the on-disk state. Importing ``notch`` itself must stay cheap; the
Qt application is created by :func:`notch.app.main` or ``python -m notch``.
"""
