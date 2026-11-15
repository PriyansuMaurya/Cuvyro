"""One reader per desktop.

Notch keeps a single state file, a single set of registered global chords and
a single camera. None of those are shared safely between two processes: a
second launch would register the same chords (Windows refuses them with error
1409), open the same camera, and save over the first window's script with
whatever it happened to be showing. Quitting the second window then looks like
the reader lost the user's edits.

A named mutex is the cheapest honest guard. It lives in the per-session
``Local`` namespace, so two Windows accounts can each run their own reader, and
it is released by the kernel when the process exits, including a crash.

The installer uses the same name as its ``AppMutex`` so an upgrade cannot
replace files underneath a running copy.
"""

import ctypes
import sys

APP_MUTEX = "Local\\NotchReaderSingleton"
_ERROR_ALREADY_EXISTS = 183

_handle = None


def _kernel32():
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                      ctypes.c_wchar_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    return kernel32


def acquire() -> bool:
    """Take the reader's mutex. False means another copy already owns it.

    A failure to call the OS at all is treated as success: refusing to start
    because a mutex call raised would be a worse failure than allowing a
    second window on a platform this code was not written for.
    """
    global _handle
    if sys.platform != "win32" or _handle is not None:
        return True
    try:
        kernel32 = _kernel32()
        handle = kernel32.CreateMutexW(None, False, APP_MUTEX)
        if not handle:
            return True
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _handle = handle
        return True
    except Exception:
        return True


def release() -> None:
    """Close the mutex handle. The kernel also does this at process exit."""
    global _handle
    if _handle is None:
        return
    try:
        _kernel32().CloseHandle(_handle)
    except Exception:
        pass
    _handle = None
