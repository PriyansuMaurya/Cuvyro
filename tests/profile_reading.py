"""Profile the reader: frame stability, response latency, CPU and memory.

The harness builds the real overlay offscreen, loads a long script and drives
the entry point the audio worker's queued signal calls (``_on_speech_text``)
at the cadence of the recogniser. It reports:

  * how long the main thread spends inside each speech result,
  * how long the highlight takes to reach the screen after one arrives,
  * how evenly the scroll advances while a line is gliding,
  * how long a wheel cuvyro takes to move the page,
  * and what an idle and a reading second cost in CPU and working set.

It touches no saved state (temporary store) and never opens the microphone.

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/profile_reading.py
    ... --seconds 12 --wpm 180
    ... --json out.json

For an A/B against the build before the scroll-clock and undo-stack fixes:

    ... --interval 17 --undo

The other knobs (``--precise-timer``, ``--interval``, ``--no-effects``) isolate
single suspects.
"""

import argparse
import ctypes
import json
import os
import statistics
import sys
import tempfile
import time

from PyQt5.QtCore import QPoint, QPointF, Qt, QTimer
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "demo-script.txt")
SCRIPT_REPEATS = 8


# ── Working set ──────────────────────────────────────────────────────
class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def working_set_mb() -> float:
    counters = _PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    try:
        function = ctypes.windll.kernel32.K32GetProcessMemoryInfo
        function.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
        process = ctypes.windll.kernel32.GetCurrentProcess()
        if function(process, ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize / (1024.0 * 1024.0)
    except Exception:
        pass
    return float("nan")


def pct(values, share):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


def mean(values):
    return statistics.fmean(values) if values else 0.0


def load_script(win, text):
    """Load a script the way the file picker does, minus the microphone."""
    win._text_edit.setPlainText(text)
    win._spaced_key = None
    win._text_edit.setAlignment(Qt.AlignCenter)
    win._script_text = text
    win._matching.load_script(text)
    win._highlight_key = None
    win._focus_span = None
    win._last_match_time = None
    win._last_match_pos = -1
    win._line_spans = []
    win._current_line = 0
    win._confirmed_word = -1
    win._partial_word = -1
    win._apply_highlight(-1)
    win._refresh_lines()
    win._text_edit.verticalScrollBar().setValue(0)
    win._scroll_to_reading_line(0)


class Profile:
    def __init__(self):
        self.reset()

    def reset(self):
        self.overlay_paint = []
        self.text_paint = []
        self.text_stamps = []
        self.tick = []
        self.tick_stamps = []
        self.tick_marks = []
        self.highlight = []
        self.refresh = []
        self.speech = []
        self.latency = []
        self.pending_at = None
        self.typeset = 0
        self.steps = 0
        self.text_paints = 0


def build_overlay(profile, detach_effects=False):
    from cuvyro.ui import controls
    from cuvyro.ui import overlay as ui_overlay
    from cuvyro.storage.store import Store

    class ProfiledText(controls.ScriptTextEdit):
        def paintEvent(self, event):
            started = time.perf_counter()
            try:
                super().paintEvent(event)
            finally:
                now = time.perf_counter()
                profile.text_paint.append(now - started)
                profile.text_stamps.append(now)
                profile.text_paints += 1
                if profile.pending_at is not None:
                    profile.latency.append(now - profile.pending_at)
                    profile.pending_at = None

    ui_overlay.ScriptTextEdit = ProfiledText

    class ProfiledOverlay(ui_overlay.CuvyroOverlay):
        def _start_listening(self):
            """The harness calls the recognition slot directly."""

        def paintEvent(self, event):
            started = time.perf_counter()
            try:
                super().paintEvent(event)
            finally:
                profile.overlay_paint.append(time.perf_counter() - started)

        def _tick(self):
            started = time.perf_counter()
            try:
                return super()._tick()
            finally:
                now = time.perf_counter()
                profile.tick.append(now - started)
                profile.tick_stamps.append(now)
                profile.tick_marks.append(
                    (now, self._text_edit.verticalScrollBar().value(),
                     self._scroll_moving))

        def _apply_highlight(self, position):
            started = time.perf_counter()
            try:
                return super()._apply_highlight(position)
            finally:
                profile.highlight.append(time.perf_counter() - started)

        def _refresh_lines(self):
            started = time.perf_counter()
            try:
                return super()._refresh_lines()
            finally:
                profile.refresh.append(time.perf_counter() - started)

        def _apply_line_spacing(self):
            changed = super()._apply_line_spacing()
            if changed:
                profile.typeset += 1
            return changed

        def _step_reading_line(self, force=False):
            moved = super()._step_reading_line(force)
            if moved:
                profile.steps += 1
            return moved

        def _on_speech_text(self, text, is_final):
            started = time.perf_counter()
            profile.pending_at = started
            try:
                return super()._on_speech_text(text, is_final)
            finally:
                profile.speech.append(time.perf_counter() - started)

    win = ProfiledOverlay()
    win._store = Store(tempfile.mkdtemp(prefix="cuvyro-profile-"))
    if detach_effects:
        win._text_edit.setGraphicsEffect(None)
    return win


def wheel(widget, notches):
    local = QPointF(widget.rect().center())
    global_pos = QPointF(widget.mapToGlobal(widget.rect().center()))
    event = QWheelEvent(local, global_pos, QPoint(0, 0),
                        QPoint(0, 120 * notches), Qt.NoButton,
                        Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=12.0)
    parser.add_argument("--wpm", type=float, default=180.0)
    parser.add_argument("--no-effects", action="store_true")
    parser.add_argument("--precise-timer", action="store_true")
    parser.add_argument("--interval", type=int, help="override the scroll clock")
    parser.add_argument("--undo", action="store_true",
                        help="keep the document's undo stack on (old behaviour)")
    parser.add_argument("--no-undo", action="store_true")
    parser.add_argument("--json")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    profile = Profile()
    win = build_overlay(profile, detach_effects=args.no_effects)
    if args.interval:
        win._scroll_timer.setInterval(args.interval)
    if args.precise_timer:
        win._scroll_timer.setTimerType(Qt.PreciseTimer)
    if args.undo:
        win._text_edit.document().setUndoRedoEnabled(True)
    if args.no_undo:
        win._text_edit.document().setUndoRedoEnabled(False)
    win.show()
    app.processEvents()

    text = (open(FIXTURE, encoding="utf-8").read() + "\n") * SCRIPT_REPEATS
    load_script(win, text)
    for _ in range(8):
        app.processEvents()
        time.sleep(0.015)
    win._stop_listening_sync()

    words = text.split()
    state = {"index": 0, "accum": 0.0}
    idle = {}
    session = {}
    wheel_result = {}

    def feed():
        if state["index"] >= len(words):
            return
        state["accum"] += args.wpm / 60.0 / 10.0
        step = int(state["accum"])
        if step:
            state["accum"] -= step
            state["index"] = min(len(words), state["index"] + step)
            heard = words[max(0, state["index"] - 6):state["index"]]
            win._on_speech_text(" ".join(heard), state["index"] % 12 == 0)

    def probe_wheel():
        viewport = win._text_edit.viewport()
        before = win._text_edit.verticalScrollBar().value()
        profile.pending_at = time.perf_counter()
        started = time.perf_counter()
        wheel(viewport, -3)
        handler = time.perf_counter() - started
        after = win._text_edit.verticalScrollBar().value()
        wheel_result["handler_ms"] = round(1000 * handler, 2)
        wheel_result["moved_px"] = after - before
        QTimer.singleShot(120, finish)

    def begin_reading():
        idle["ticks"] = len(profile.tick)
        idle["overlay_paints"] = len(profile.overlay_paint)
        idle["text_paints"] = profile.text_paints
        idle["cpu_seconds"] = time.process_time() - cpu_start
        idle["tick_ms_p95"] = 1000 * pct(
            [b - a for a, b in zip(profile.tick_stamps[:-1],
                                   profile.tick_stamps[1:])], 0.95)
        session["undo_before"] = win._text_edit.document().availableUndoSteps()
        session["memory_before_mb"] = working_set_mb()
        session["cpu_before"] = time.process_time()
        session["wall_before"] = time.perf_counter()
        profile.reset()
        QTimer.singleShot(int(args.seconds * 1000), lambda: feed_timer.stop())
        QTimer.singleShot(int(args.seconds * 1000) + 400, probe_wheel)
        feed_timer.start(100)

    def finish():
        # Split the tick stream into glides; a glide is a run of ticks where
        # the scroll spring was in flight.
        glides = []
        moving_before = False
        for at, value, moving in profile.tick_marks:
            if moving and not moving_before:
                glides.append({"from": at, "ticks": [], "values": []})
            if moving and glides:
                glides[-1]["ticks"].append(at)
                glides[-1]["values"].append(value)
            moving_before = moving

        tick_intervals = []
        stall_longest = 0
        px_per_frame = []
        for glide in glides:
            ticks = glide["ticks"]
            tick_intervals.extend(b - a for a, b in zip(ticks[:-1], ticks[1:]))
            values = glide["values"]
            px_per_frame.extend(b - a for a, b in zip(values[:-1], values[1:]))
            stall = 0
            for before, after in zip(values[:-1], values[1:]):
                stall = stall + 1 if after == before else 0
                stall_longest = max(stall_longest, stall)

        wall = time.perf_counter() - session["wall_before"]
        cpu = time.process_time() - session["cpu_before"]

        session["undo_after"] = win._text_edit.document().availableUndoSteps()
        session["memory_after_mb"] = working_set_mb()

        report = {
            "effects": not args.no_effects,
            "precise_timer": args.precise_timer,
            "undo_enabled": win._text_edit.document().isUndoRedoEnabled(),
            "scroll_interval_ms": win._scroll_timer.interval(),
            "idle_1_5s": {
                "ticks": idle.get("ticks", 0),
                "overlay_paints": idle.get("overlay_paints", 0),
                "text_paints": idle.get("text_paints", 0),
                "cpu_seconds": round(idle.get("cpu_seconds", 0.0), 2),
            },
            "speech": {
                "results": len(profile.speech),
                "handler_ms_mean": round(1000 * mean(profile.speech), 3),
                "handler_ms_p95": round(1000 * pct(profile.speech, 0.95), 3),
                "handler_ms_max": round(1000 * max(profile.speech, default=0.0), 3),
                "response_ms_mean": round(1000 * mean(profile.latency), 2),
                "response_ms_p95": round(1000 * pct(profile.latency, 0.95), 2),
                "response_ms_max": round(1000 * max(profile.latency, default=0.0), 2),
            },
            "rendering": {
                "overlay_paints": len(profile.overlay_paint),
                "overlay_ms_mean": round(1000 * mean(profile.overlay_paint), 3),
                "text_paints": profile.text_paints,
                "text_ms_mean": round(1000 * mean(profile.text_paint), 3),
                "text_ms_p95": round(1000 * pct(profile.text_paint, 0.95), 3),
                "text_ms_max": round(1000 * max(profile.text_paint, default=0.0), 3),
                "typeset_passes": profile.typeset,
                "refresh_lines": len(profile.refresh),
            },
            "scroll": {
                "line_steps": profile.steps,
                "glides": len(glides),
                "frames": len(tick_intervals),
                "frame_ms_mean": round(1000 * mean(tick_intervals), 2),
                "frame_ms_p95": round(1000 * pct(tick_intervals, 0.95), 2),
                "frame_ms_max": round(1000 * max(tick_intervals, default=0.0), 2),
                "frame_jitter_ms": round(1000 * statistics.pstdev(tick_intervals), 2)
                if len(tick_intervals) > 1 else 0.0,
                "px_per_frame_mean": round(mean(px_per_frame), 2),
                "px_per_frame_max": round(max(px_per_frame, default=0.0), 1),
                "px_over_4": sum(1 for d in px_per_frame if d >= 4),
                "longest_stall_frames": stall_longest,
            },
            "input": wheel_result,
            "session": {
                "undo_before": session.get("undo_before"),
                "undo_after": session.get("undo_after"),
                "memory_before_mb": round(session.get("memory_before_mb", 0.0), 1),
                "memory_after_mb": round(session.get("memory_after_mb", 0.0), 1),
            },
            "cost": {
                "seconds": round(wall, 2),
                "ticks": len(profile.tick),
                "tick_ms_mean": round(1000 * mean(profile.tick), 3),
                "tick_ms_p95": round(1000 * pct(profile.tick, 0.95), 3),
                "cpu_seconds": round(cpu, 2),
                "cpu_share": round(cpu / wall, 3) if wall else 0.0,
                "working_set_mb": round(working_set_mb(), 1),
            },
        }
        print(json.dumps(report, indent=2))
        if args.json:
            with open(args.json, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2)
        win.close()
        app.quit()

    feed_timer = QTimer()
    feed_timer.timeout.connect(feed)

    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    QTimer.singleShot(1500, begin_reading)
    app.exec_()


if __name__ == "__main__":
    main()
