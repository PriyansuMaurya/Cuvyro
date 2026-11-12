# Working notes for agents

## Commit messages

No AI attribution in commit messages. Do not add `Generated with Codebuff`,
`Co-Authored-By: Codebuff`, or any similar footer or trailer, in this
repository or any branch of it. A commit message ends at its own last line:
subject, optional body, nothing after it.

## Conventions

Subjects follow conventional commits (`fix(notes): ...`, `build(release): ...`)
with a body that explains why the change was needed rather than restating the
diff.

## Layout

App modules stay at the repository root. `tests/` holds the standalone checks,
the screenshot harnesses and their fixture; `packaging/` holds only build
configuration and the icon sources.

## Checks

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_behavior.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_voice_follow.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_waveform.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_speech_engine.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_robustness.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_note_clearing.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_note_placement.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_head_tracking.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/profile_reading.py
.venv/Scripts/python.exe tests/check_design.py      # needs a real display
.venv/Scripts/python.exe tests/check_quality.py     # needs a real display
.venv/Scripts/python.exe tests/check_hotkeys.py     # needs Windows and a real display
```

`tests/check_hotkeys.py` registers the real system-wide chords and presses them
with OS-level keyboard events while another application owns the foreground, so
it needs Windows: `RegisterHotKey` is an operating-system service. It checks
delivery, focus preservation, conflict reporting (error 1409), the Voice-mode
no-op, and release on close.

`tests/verify_installed.py` does the same against the exe the installer
produced (rebuild with `build-installer.bat`, install it, then run the check).
It backs up and restores the reader's saved state around the run. Because the
reader excludes itself from screen capture, the scroll offset comes from
`NOTCH_MANUAL_TRACE=1`, which makes the app append one line per Manual start
and pause to `notch.log` beside the executable; nothing is written without it.

`tests/check_speech_engine.py` decodes real audio through the installed model
and reports SKIP when `model/` has none. `tests/bench_speech_engines.py` is the
benchmark that chose the engine; it synthesises its fixtures with the local
SAPI voices and needs no network.

`tests/profile_reading.py` drives the real overlay offscreen at recogniser
cadence and reports frame cadence, result-to-paint latency, CPU and working
set. `--interval 17 --undo` reproduces the build before the scroll-clock and
undo-stack fixes, so the two profiles can be compared directly.

`tests/calibrate_voice.py` scores a speaker against the corpus and drives the
overlay with the recordings (`--no-mic --reader` re-scores takes already in
`%TEMP%\notch-voice`). Recordings stay local WAVs; nothing is uploaded.

`tests/check_head_tracking.py` drives `HeadMotion` with explicit timestamps at
the cadence the worker achieves (10 Hz — a 66 ms camera frame plus the search,
not the 15 fps it asks the camera for) and measures latency, jitter, settling,
the displacement limits and the modelled eye-rotation change, then exercises
the reader itself (default off, card travel, on-screen clamping, immediate
disable, camera-failure path).

Several of its checks exist because the feature failed in the field without
any test failing. `check_detection_range` holds the frame size to the
detector's measured floor: the old frontal cascade needed a face box around
56 px, so a reader 80 cm from the lens needs roughly 93 px of head, which
320 px of frame never gave them — the detector returned nothing and the card
never moved. That is why the frame is 640×480 and why the detector is now a
MediaPipe landmarker, which reads a face far smaller than that.
`check_sparse_camera` includes a camera that delivers one reading and then
stops: calibration used to be advanced only from incoming samples, so that
state left the tracker calibrating for ever. `check_landmarks` pins the sign
of every axis from a synthetic 478-point face (a wrong sign looks like the
words running away, not like a bug), `check_gaze` holds gaze to its share of
the travel and asserts that line-scanning saccades move nothing, and
`check_gaze_feedback` models the loop where moving the card changes the gaze,
because that is the shape that hunts from side to side if the gain is too
high. `check_outliers` drops one wild reading and follows two that agree.
It prints a VERDICT line. Full compensation (gain 1.0) removes ~38% of the
modelled head-induced eye rotation and was measured, against a real reader
swaying between comfortable leans, to pin the card against its stop for a
fifth of a live session. At the shipped 0.45 gain it removes ~14% and the
card stays off its stop; the verdict says MEANINGFUL at that gain and
deliberately not maximal. The feature still ships off by default: it has now
been watched on one machine, with a landmarker that loses a face that leaves
the frame, and has not been watched on a second.
Calibration starts its window at the first face sample and takes a median,
because a camera can report a face only every second or so; it also resolves
on a deadline in `poll`, so a stalled window cannot leave the feature inert.
The window is rolling and only closes on a still head: a lean that fell inside
it used to become the neutral, after which a 45 mm lean moved the card the
full 150 DIP in the wrong direction. That is covered by `check_sparse_camera`.

The worker's camera order is measured, not theoretical: DirectShow first,
MediaFoundation as the fallback. On the machine this was fixed for, MSMF's
constructor took 5.5 s and the first reading arrived at 11.8 s; DSHOW produced
one in about a second, and both are shared on Windows 10+ (a second process
opened and read the same device alongside the worker). A `stop()` that times
out leaves `_thread` set on purpose — `running` stays true so a second source
cannot open a second handle on a device the first has not released — and only
one source may hold the camera at a time, which is what stops the camera light
flickering.

`tests/measure_head_tracking.py` is the real-device half — fresh-reading rate,
frame cost, stillness, lean travel and gaze separation — and SKIPs when there
is no camera or no face. It walks the subject through timed phases, so it needs
a person in front of the lens.

`tests/check_design.py` asserts surface invariants against rendered pixels and
`tests/verify_ui.py` reports window geometry. All of them build their own
temporary store, so they never touch the saved script or notes.
