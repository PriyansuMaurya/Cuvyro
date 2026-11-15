"""System-wide manual controls that do not need the reader focused.

Manual playback used to depend entirely on the reader owning the keyboard.
That is fine while Cuvyro is focused, but the moment to use Manual is usually
a presentation or a recording, where another application owns the keyboard.
Windows' ``RegisterHotKey`` lets Cuvyro listen for three chords from anywhere
without taking focus away from the application the user is working in.

Registration is explicit and reversible. The caller asks the user once, the
chords live in the settings file, and every registration failure is reported
back so window-scoped Space and the arrow keys remain the fallback. Nothing
here reads, stores or forwards the keys other than the three configured
chords.
"""

import ctypes
import ctypes.wintypes
import sys
from typing import Dict, Optional, Tuple

from PyQt5.QtCore import QAbstractNativeEventFilter, QObject, pyqtSignal

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_VK = {
    "Space": 0x20,
    "Left": 0x25,
    "Up": 0x26,
    "Right": 0x27,
    "Down": 0x28,
    "PageUp": 0x21,
    "PageDown": 0x22,
    "Home": 0x24,
    "End": 0x23,
    # Function keys give the toggle chord somewhere to go when the user's
    # usual combination is already taken.
    **{f"F{n}": 0x6F + n for n in range(1, 13)},
}
_MODS = {
    "Ctrl": MOD_CONTROL,
    "Control": MOD_CONTROL,
    "Alt": MOD_ALT,
    "Shift": MOD_SHIFT,
    "Win": MOD_WIN,
}

ACTIONS = ("toggle", "slower", "faster")
# Two things decide these chords, both measured against Windows itself:
#
#   * The toggle cannot hold Alt. Alt is the Windows menu key, and
#     Ctrl+Alt+Space is read as Alt+Space by whatever owns the keyboard, so
#     it flashes the window menu / task switcher over the app the user is
#     presenting in. Ctrl+Shift+Space delivers and leaves that app alone.
#   * The speed chords cannot be Shift plus a navigation key. RegisterHotKey
#     accepts Ctrl+Shift+Left, but WM_HOTKEY never arrives for it (Windows
#     treats Shift+navigation as selection input), so the chord would look
#     registered and do nothing. Ctrl+Alt+arrows do arrive, and holding Alt
#     with an arrow never opens a menu.
DEFAULT_CHORDS = {
    "toggle": "Ctrl+Shift+Space",
    "slower": "Ctrl+Alt+Left",
    "faster": "Ctrl+Alt+Right",
}
# The two speed chords are fixed so the help text can state them; only the
# play/pause chord is configurable, which is the one users actually press.
SPEED_CHORDS = {
    "slower": DEFAULT_CHORDS["slower"],
    "faster": DEFAULT_CHORDS["faster"],
}
TOGGLE_CHOICES = ("Ctrl+Shift+Space", "Ctrl+Shift+F9", "Ctrl+Shift+F10")

# One id per action. Values are arbitrary but must be unique within the app.
_BASE_ID = 0x4E01


def available() -> bool:
    """Whether this platform can register a system-wide chord."""
    return sys.platform == "win32"


def parse_chord(chord: str) -> Optional[Tuple[int, int]]:
    """Split ``"Ctrl+Shift+Space"`` into RegisterHotKey modifiers and a VK.

    A bare key is rejected on purpose: a system-wide Space would swallow the
    space bar in every application on the machine. Returns None for anything
    that is not a known modifier/key combination.
    """
    if not isinstance(chord, str) or not chord.strip():
        return None
    parts = [part.strip() for part in chord.split("+")]
    key = parts[-1]
    if key not in _VK:
        return None
    mods = 0
    for part in parts[:-1]:
        if part not in _MODS:
            return None
        mods |= _MODS[part]
    if not mods:
        return None
    return mods | MOD_NOREPEAT, _VK[key]


class _MessageFilter(QAbstractNativeEventFilter):
    """Turn WM_HOTKEY messages from the reader's window into signals."""

    def __init__(self, owner):
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) != b"windows_generic_MSG":
            return False, 0
        msg = ctypes.wintypes.MSG.from_address(int(message))
        if msg.message != WM_HOTKEY:
            return False, 0
        action = self._owner._by_id.get(int(msg.wParam))
        if action is None:
            return False, 0
        self._owner.triggered.emit(action)
        return True, 0


class GlobalHotkeys(QObject):
    """Register, deliver and release Manual mode's system-wide chords.

    The chords are registered against the reader's window, so the WM_HOTKEY
    messages arrive through Qt's native event filter on the GUI thread. A
    registration that another application already owns is reported rather
    than retried silently; the caller keeps its window-scoped controls.
    """

    triggered = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._by_id: Dict[int, str] = {}
        self._registered: Dict[str, int] = {}
        self.errors: Dict[str, int] = {}
        self._hwnd = 0
        self._filter = None
        self._filter_installed = False

    def register(self, chords: Dict[str, str], hwnd: int) -> Dict[str, bool]:
        """Register every action in ``chords``; returns per-action success.

        Any previous registration is released first so a changed chord can be
        applied without leaving the old chord listening. Errors carry the
        Windows error code (1409 means another application owns the chord).
        """
        self.unregister_all()
        results = {action: False for action in ACTIONS}
        self.errors = {}
        if not available() or not hwnd:
            return results
        self._install_filter()
        self._hwnd = int(hwnd)
        for index, action in enumerate(ACTIONS):
            chord = chords.get(action)
            parsed = parse_chord(chord) if chord else None
            if parsed is None:
                continue
            mods, vk = parsed
            hotkey_id = _BASE_ID + index
            if ctypes.windll.user32.RegisterHotKey(
                    self._hwnd, hotkey_id, mods, vk):
                self._by_id[hotkey_id] = action
                self._registered[action] = hotkey_id
                results[action] = True
            else:
                self.errors[action] = int(ctypes.windll.kernel32.GetLastError())
        return results

    def unregister_all(self):
        """Release every chord. Safe to call when nothing is registered."""
        if self._hwnd:
            for hotkey_id in list(self._registered.values()):
                try:
                    ctypes.windll.user32.UnregisterHotKey(self._hwnd, hotkey_id)
                except Exception:
                    pass
        self._registered.clear()
        self._by_id.clear()
        self.errors = {}

    def is_registered(self, action: str) -> bool:
        return action in self._registered

    def shutdown(self):
        """Unregister and remove the native filter. Called on application exit."""
        self.unregister_all()
        if self._filter is not None and self._filter_installed:
            try:
                from PyQt5.QtWidgets import QApplication
                app = QApplication.instance()
                if app is not None:
                    app.removeNativeEventFilter(self._filter)
            except Exception:
                pass
        self._filter_installed = False

    def _install_filter(self):
        if self._filter_installed:
            return
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return
        self._filter = _MessageFilter(self)
        app.installNativeEventFilter(self._filter)
        self._filter_installed = True
