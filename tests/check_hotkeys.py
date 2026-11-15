"""System-wide Manual controls, exercised through Windows itself.

Checks chord parsing, real registration, OS-level delivery while another
window owns the foreground, conflict reporting, focus preservation, the
Voice-mode no-op, and release on close. This needs Windows and a real
display: RegisterHotKey is an operating-system service.

    .venv/Scripts/python.exe tests/check_hotkeys.py
"""

import ctypes
import os
import sys
import tempfile

from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

# The focus checks print the foreground window's title, and a title is whatever
# the application in front calls itself. Redirected output uses the console
# codepage, so a CJK title under cp1252 raised UnicodeEncodeError from inside
# the report and took the whole check down with it.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

failures = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


user32 = ctypes.windll.user32
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL, VK_SHIFT, VK_MENU, VK_SPACE = 0x11, 0x10, 0x12, 0x20
VK_LEFT, VK_RIGHT = 0x25, 0x27


def describe(hwnd):
    """Name a window so a foreground change says who took it."""
    title = ctypes.create_unicode_buffer(256)
    klass = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    user32.GetClassNameW(hwnd, klass, 256)
    return f"{hwnd} [{klass.value}] {title.value!r}"


def os_chord(*keys):
    """Press and release a chord the way a keyboard sends it."""
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
    for key in reversed(keys):
        user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)


def main():
    # The first-run guide is modal. A machine that has never run Notch (a
    # runner, a new laptop) would block on it instead of starting, so the
    # check states the machine it describes. check_note_clearing, the
    # robustness check and the upgrade check do the same.
    from PyQt5.QtCore import QSettings
    QSettings("Notch", "Notch").setValue("guide_seen", True)
    app = QApplication(sys.argv)
    from notch.platform import hotkeys as hk
    from notch.ui import overlay as ui_overlay
    from notch.storage.store import Store

    if not hk.available():
        print("SKIP  global hotkeys are Windows-only")
        return 0

    # ── Chord parsing ────────────────────────────────────────────────
    parsed = hk.parse_chord("Ctrl+Shift+Space")
    check("a chord parses to modifiers and a key",
          parsed is not None and parsed[0] & hk.MOD_CONTROL
          and parsed[0] & hk.MOD_SHIFT and parsed[1] == 0x20)
    check("a bare key is refused for a system-wide shortcut",
          hk.parse_chord("Space") is None)
    check("an unknown key is refused", hk.parse_chord("Ctrl+Shift+Banana") is None)
    check("an unknown modifier is refused",
          hk.parse_chord("Hyper+Space") is None)
    # Alt is the Windows menu key: an Alt+Space chord is read as the window
    # menu by whatever owns the keyboard, so the toggle cannot hold it.
    check("the toggle chord does not hold Alt",
          "Alt" not in hk.DEFAULT_CHORDS["toggle"],
          hk.DEFAULT_CHORDS["toggle"])
    check("no offered toggle chord holds Alt",
          not any("Alt" in chord for chord in hk.TOGGLE_CHOICES),
          str(hk.TOGGLE_CHOICES))

    # RegisterHotKey accepts Shift+navigation chords and then never delivers
    # them, which would leave a speed chord that looks live and does nothing.
    def shift_plus_navigation(chord):
        parts = chord.split("+")
        return "Shift" in parts[:-1] and parts[-1] in (
            "Left", "Right", "Up", "Down", "PageUp", "PageDown",
            "Home", "End")

    check("no default chord is Shift plus a navigation key",
          not any(shift_plus_navigation(chord)
                  for chord in hk.DEFAULT_CHORDS.values()),
          str(hk.DEFAULT_CHORDS))

    win = ui_overlay.NotchOverlay()
    win._store = Store(tempfile.mkdtemp(prefix="notch-hotkeys-"))
    win._voice_mode = False
    win._hotkey_consent = "granted"
    win._hotkeys_enabled = True
    win.show()
    QTest.qWait(500)

    long_text = (open(os.path.join(REPO_ROOT, "tests", "fixtures",
                                   "demo-script.txt"), encoding="utf-8").read()
                 + "\n") * 6
    win._text_edit.setPlainText(long_text)
    win._script_text = long_text
    win._matching.load_script(long_text)
    QTest.qWait(150)
    bar = win._text_edit.verticalScrollBar()

    listening = win._apply_global_hotkeys()
    status = dict(win._hotkey_status)
    if not listening:
        print("SKIP  the toggle chord is already owned on this machine "
              f"({win._hotkeys.errors})")
        win.close()
        return 0
    check("the play/pause chord registers", status.get("toggle") is True)
    check("the speed chords register",
          status.get("slower") is True and status.get("faster") is True,
          str(status))

    # Put the foreground anywhere but the reader: window-scoped Space would
    # not reach it now, which is exactly the reported failure. This process
    # cannot take the foreground from another one on purpose, so a helper
    # window is raised and, if the shell still owns the front, that shell
    # window is the outside application the chord has to work under.
    other = QWidget()
    other.setWindowTitle("Foreground holder")
    other.setGeometry(20, 20, 220, 120)
    other.show()
    other.raise_()
    other.activateWindow()
    QTest.qWait(300)
    foreground = user32.GetForegroundWindow()
    print(f"  foreground: {describe(foreground)}")
    check("the reader does not own the foreground when the chord is pressed",
          foreground != int(win.winId()))

    bar.setValue(0)
    win._manual_running = False
    before_fg = user32.GetForegroundWindow()
    os_chord(VK_CONTROL, VK_SHIFT, VK_SPACE)
    QTest.qWait(600)
    check("a system-wide chord starts Manual without reader focus",
          win._manual_running, f"running={win._manual_running}")
    check("the script moves while another window is focused",
          bar.value() > 0, f"scroll={bar.value()}")
    after_fg = user32.GetForegroundWindow()
    # The reader must not grab the foreground. Anything else that moved is
    # the environment's own doing (a shell window activating late).
    check("starting Manual never activates the reader",
          after_fg != int(win.winId()),
          f"{describe(before_fg)} -> {describe(after_fg)}")

    os_chord(VK_CONTROL, VK_SHIFT, VK_SPACE)
    QTest.qWait(300)
    check("the same chord pauses Manual", not win._manual_running)

    speed_before = win._manual_wpm
    os_chord(VK_CONTROL, VK_MENU, VK_RIGHT)
    QTest.qWait(250)
    check("the faster chord works system-wide", win._manual_wpm > speed_before,
          f"{speed_before} -> {win._manual_wpm}")
    os_chord(VK_CONTROL, VK_MENU, VK_LEFT)
    QTest.qWait(250)
    check("the slower chord works system-wide",
          win._manual_wpm == speed_before, f"{speed_before} -> {win._manual_wpm}")

    # ── Conflicts are reported, not hidden ──────────────────────────
    rival = hk.GlobalHotkeys(win)
    rival.register({"toggle": "Ctrl+Shift+Space"}, int(other.winId()))
    check("a second registration of the same chord is reported as taken",
          rival.errors.get("toggle") == hk.ERROR_HOTKEY_ALREADY_REGISTERED,
          str(rival.errors))
    rival.shutdown()

    # ── Voice mode releases the chords and ignores them ─────────────
    win._voice_mode = True
    win._apply_global_hotkeys()
    check("Voice mode releases the system-wide chords",
          not win._hotkeys.is_registered("toggle"))
    voice_speed = win._voice_wpm
    os_chord(VK_CONTROL, VK_MENU, VK_RIGHT)
    QTest.qWait(250)
    check("Voice mode is unaffected by the chords",
          win._voice_wpm == voice_speed and not win._manual_running,
          f"voice_wpm {voice_speed} -> {win._voice_wpm}")

    # ── Cleanup on close ────────────────────────────────────────────
    win._voice_mode = False
    win._apply_global_hotkeys()
    hwnd = int(win.winId())
    win.close()
    QTest.qWait(150)
    probe_id = 0x7A01
    mods = hk.MOD_CONTROL | hk.MOD_SHIFT | hk.MOD_NOREPEAT
    released = bool(user32.RegisterHotKey(hwnd, probe_id, mods, VK_SPACE))
    if released:
        user32.UnregisterHotKey(hwnd, probe_id)
    check("closing the reader releases the chords", released)

    other.close()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All global-hotkey checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
