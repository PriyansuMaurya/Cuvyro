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

Two of its checks exist because the feature failed in the field without any
test failing. `check_detection_range` holds the frame size to the detector's
measured floor: the frontal cascade needs a face box around 56 px, so a reader
80 cm from the lens needs roughly 93 px of head, which 320 px of frame never
gave them — the detector returned nothing and the card never moved.
`check_sparse_camera` includes a camera that delivers one reading and then
stops: calibration used to be advanced only from incoming samples, so that
state left the tracker calibrating for ever.
It prints a VERDICT line. It read NOT MEANINGFUL while the words were capped at
14 DIP of travel; carrying the whole card instead (150 DIP) lifts the modelled
removal of head-induced eye rotation to ~38%, past the 25% bar, so the verdict
now reads MEANINGFUL. It still ships off by default: a profile cascade, run
only when the frontal pass misses and on a half-size copy, keeps a turned head
alive for about a second, but a face that leaves the frame is still a lost face
and the feature has not been watched on a second machine. Calibration starts
its window at the first face sample and takes a median, because a camera can
report a face only every second or so; it also resolves on a deadline in
`poll`, so a stalled window cannot leave the feature inert. The window is
rolling and only closes on a still head: a lean that fell inside it used to
become the neutral, after which a 45 mm lean moved the card the full 150 DIP
in the wrong direction. That is covered by `check_sparse_camera`.
`tests/measure_head_tracking.py` is the real-device half — sample rate, detector cost, stillness and drift — and SKIPs
when there is no camera or no face.

`tests/check_design.py` asserts surface invariants against rendered pixels and
`tests/verify_ui.py` reports window geometry. All of them build their own
temporary store, so they never touch the saved script or notes.
