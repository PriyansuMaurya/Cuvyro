"""Keep Cuvyro's windows out of screen capture.

Every surface the app shows should carry the same policy: the reader, the
notes, the menu, and the dialogs. A privacy promise that only covers some of
the windows is not a privacy promise, because the one window that leaks is
the one somebody screen-shares.

``WDA_EXCLUDEFROMCAPTURE`` (Windows 10 2004+) removes the window from screen
captures entirely. Older builds only have ``WDA_MONITOR``, which blanks the
window instead of hiding it, so it is used as a fallback rather than a
choice.
"""

import ctypes
import sys

WDA_NONE = 0x0
WDA_MONITOR = 0x1
WDA_EXCLUDEFROMCAPTURE = 0x11


def exclude_from_capture(window, enable: bool = True) -> bool:
    """Apply capture exclusion to a window. Returns True on success."""
    if sys.platform != "win32":
        return False
    try:
        handle = int(window.winId())
        user32 = ctypes.windll.user32
        if not enable:
            return bool(user32.SetWindowDisplayAffinity(handle, WDA_NONE))
        if user32.SetWindowDisplayAffinity(handle, WDA_EXCLUDEFROMCAPTURE):
            return True
        return bool(user32.SetWindowDisplayAffinity(handle, WDA_MONITOR))
    except Exception:
        return False


def capture_affinity(window):
    """Read back the window's current affinity, or None when unavailable."""
    if sys.platform != "win32":
        return None
    try:
        out = ctypes.c_ulong(0)
        user32 = ctypes.windll.user32
        if not user32.GetWindowDisplayAffinity(int(window.winId()), ctypes.byref(out)):
            return None
        return out.value
    except Exception:
        return None
