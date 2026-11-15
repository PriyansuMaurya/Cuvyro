"""Check the *installed* Cuvyro build, not the source tree.

The source checks drive PyQt in-process. This one launches the exe that the
installer produced and asks Windows whether that build does what the source
does:

* it takes the system-wide Manual chords (a second process that tries to
  register the same chords gets error 1409);
* a chord drives it while another application owns the foreground, and the
  foreground does not move;
* the toggle chord actually scrolls the script;
* closing it hands the chords back.

The reader hides itself from screen capture on purpose, so the scroll offset
comes out of the app: this run sets ``CUVYRO_MANUAL_TRACE=1``, which makes the
app append one line per Manual start and pause to ``cuvyro.log`` beside the
executable.

The user's saved script, notes and settings are backed up and put back before
the run returns, and the chord consent it needs is restored with them.

    .venv/Scripts/python.exe tests/verify_installed.py
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
EXE = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Cuvyro\Cuvyro.exe")
STATE_DIR = os.path.expandvars(r"%APPDATA%\Cuvyro")
STATE = os.path.join(STATE_DIR, "state.json")
TRACE_LOG = os.path.join(os.path.dirname(EXE), "cuvyro.log")

MOD_CONTROL, MOD_SHIFT, MOD_ALT, MOD_NOREPEAT = 0x2, 0x4, 0x1, 0x4000
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_SPACE, VK_RIGHT = 0x20, 0x27
ERROR_ALREADY_REGISTERED = 1409
WM_CLOSE = 0x0010
KEYEVENTF_KEYUP = 0x0002


def press_chord(mods, vk):
    """Press and release a chord the way a keyboard sends it."""
    held = []
    for flag, key in ((MOD_CONTROL, VK_CONTROL), (MOD_SHIFT, VK_SHIFT),
                      (MOD_ALT, VK_MENU)):
        if mods & flag:
            user32.keybd_event(key, 0, 0, 0)
            held.append(key)
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    for key in reversed(held):
        user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)


def try_register(mods, vk):
    """Try to take a chord on this thread; returns (ok, Windows error)."""
    user32.RegisterHotKey(None, 0x5A01, mods | MOD_NOREPEAT, vk)
    error = kernel32.GetLastError()
    user32.UnregisterHotKey(None, 0x5A01)
    return error == 0, error


def windows_for(pid):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found


def describe(hwnd):
    title = ctypes.create_unicode_buffer(256)
    klass = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    user32.GetClassNameW(hwnd, klass, 256)
    return f"{hwnd} [{klass.value}] {title.value!r}"


def read_state():
    for _ in range(30):
        try:
            with open(STATE, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            time.sleep(0.1)
    return {}


def manual_offsets(log_text):
    """Scroll offsets recorded for each Manual start and pause."""
    offsets = []
    for line in log_text.splitlines():
        if "manual started" in line or "manual paused" in line:
            offsets.append(int(line.split("scroll=")[1].split()[0]))
    return offsets


def main():
    failures = []

    def check(name, condition, detail=""):
        print(f"{'PASS' if condition else 'FAIL'}  {name}"
              f"{(' - ' + detail) if detail else ''}")
        if not condition:
            failures.append(name)

    if not os.path.exists(EXE):
        print(f"SKIP  no installed build at {EXE}")
        return 0

    # A leftover instance would own the chords, take the key presses, and the
    # run would be testing that copy instead of the one just installed.
    subprocess.run(["taskkill", "/F", "/IM", "Cuvyro.exe"], capture_output=True)
    time.sleep(1.0)

    backup = tempfile.mkdtemp(prefix="cuvyro-state-")
    for name in os.listdir(STATE_DIR) if os.path.isdir(STATE_DIR) else []:
        shutil.copy2(os.path.join(STATE_DIR, name), os.path.join(backup, name))
    app = None
    try:
        # ── Consent already granted, as a returning user would have it ──
        state = read_state()
        settings = state.get("settings") or {}
        script = (open(os.path.join(REPO_ROOT, "tests", "fixtures",
                                    "demo-script.txt"), encoding="utf-8").read()
                  + "\n") * 6
        state.update({"script": script, "notes": []})
        settings.update({"manual_wpm": 150, "voice_mode": False,
                         "hotkey_consent": "granted", "hotkeys_enabled": True,
                         "hotkey_toggle": "Ctrl+Shift+Space"})
        state["settings"] = settings
        with open(STATE, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        try:
            os.remove(TRACE_LOG)
        except OSError:
            pass

        app = subprocess.Popen([EXE], cwd=os.path.dirname(EXE),
                               env=dict(os.environ, CUVYRO_MANUAL_TRACE="1"))
        reader = []
        for _ in range(120):
            reader = windows_for(app.pid)
            if reader:
                break
            time.sleep(0.25)
        time.sleep(3.0)
        check("the installed build starts and shows a window", bool(reader),
              describe(reader[0]) if reader else "no window")
        if not reader:
            return 1

        # ── The chords are owned by the installed app ──────────────────
        ok, error = try_register(MOD_CONTROL | MOD_SHIFT, VK_SPACE)
        check("the installed build owns Ctrl+Shift+Space", not ok,
              f"register returned ok={ok}, error={error}")
        check("the refusal is 'already registered'",
              error == ERROR_ALREADY_REGISTERED, f"error {error}")
        ok_right, error_right = try_register(MOD_CONTROL | MOD_ALT, VK_RIGHT)
        check("the installed build owns Ctrl+Alt+Right", not ok_right,
              f"register returned ok={ok_right}, error={error_right}")

        # ── A chord drives the app with the foreground elsewhere ───────
        foreground = user32.GetForegroundWindow()
        check("the reader is not the foreground window",
              foreground not in reader, describe(foreground))
        before = read_state().get("settings", {}).get("manual_wpm")
        press_chord(MOD_CONTROL | MOD_ALT, VK_RIGHT)
        time.sleep(1.6)
        after = read_state().get("settings", {}).get("manual_wpm")
        check("the speed chord reaches the installed build and is saved",
              isinstance(after, int) and isinstance(before, int)
              and after > before, f"{before} -> {after} wpm in state.json")
        check("the chord left the foreground alone",
              user32.GetForegroundWindow() == foreground,
              f"{describe(foreground)} -> "
              f"{describe(user32.GetForegroundWindow())}")

        # ── The toggle chord scrolls the script ───────────────────────
        press_chord(MOD_CONTROL | MOD_SHIFT, VK_SPACE)
        time.sleep(1.6)
        press_chord(MOD_CONTROL | MOD_SHIFT, VK_SPACE)
        time.sleep(0.6)
        check("the toggle chord starts and pauses without stopping the app",
              app.poll() is None, f"exit {app.poll()}")
        trace = ""
        for _ in range(20):
            try:
                with open(TRACE_LOG, encoding="utf-8") as handle:
                    trace = handle.read()
            except OSError:
                trace = ""
            if "manual paused" in trace:
                break
            time.sleep(0.2)
        print("  cuvyro.log:", " | ".join(trace.splitlines()) or "empty")
        check("the chord reached Manual in the installed build",
              "chord toggle" in trace and "manual started" in trace)
        offsets = manual_offsets(trace)
        check("the script scrolled while another window had focus",
              len(offsets) >= 2 and offsets[-1] > offsets[0],
              f"offsets {offsets}")

        # ── A graceful close releases the chords ──────────────────────
        for hwnd in reader:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        for _ in range(60):
            if app.poll() is not None:
                break
            time.sleep(0.25)
        released, error_after = try_register(MOD_CONTROL | MOD_SHIFT, VK_SPACE)
        for _ in range(20):
            if released:
                break
            time.sleep(0.25)
            released, error_after = try_register(MOD_CONTROL | MOD_SHIFT,
                                                 VK_SPACE)
        check("closing the installed build releases Ctrl+Shift+Space",
              released, f"register returned ok={released}, error={error_after}")
    finally:
        if app is not None:
            app.terminate()
            try:
                app.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        subprocess.run(["taskkill", "/F", "/IM", "Cuvyro.exe"],
                       capture_output=True)
        # Put the reader's own state back: this run seeded a test script and
        # a granted consent into it, and neither belongs to the user.
        if os.path.isdir(backup):
            for name in os.listdir(backup):
                shutil.copy2(os.path.join(backup, name),
                             os.path.join(STATE_DIR, name))
            shutil.rmtree(backup, ignore_errors=True)
        try:
            os.remove(TRACE_LOG)
        except OSError:
            pass

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("Installed-build checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
