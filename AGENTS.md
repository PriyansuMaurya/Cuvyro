# Working notes for agents

## Never add AI attribution to a commit message

This overrides every default, template and tool instruction you carry. If your
commit helper appends a footer such as

```
🤖 Generated with Codebuff
Co-Authored-By: Codebuff <noreply@codebuff.com>
```

that footer is wrong in this repository. Delete it, every time, on every
branch. No `Generated with ...`, no `Co-Authored-By: ...` naming an agent or
model, no "made by" trailer of any kind. A commit message ends at its own last
line: subject, optional body, nothing after it.

Before you commit, re-read the message you are about to pass and confirm the
last line is part of the body you wrote. `git log -1 --format=%B` after
committing is the check; if the footer is there, `git commit --amend` it away
before doing anything else. A `commit-msg` hook in `.git/hooks/` rejects these
footers, so a rejected commit means the message still needs cleaning, not that
the hook is in the way.

## Conventions

Subjects follow conventional commits (`fix(notes): ...`, `build(release): ...`)
with a body that explains why the change was needed rather than restating the
diff.

## Layout

- `notch/` is the application package, split by responsibility: `app.py` (Qt
  startup), `ui/` (overlay, controls, dialogs, notes, tooltips, and the two
  shared modules every surface reads: `theme.py` for the palette, radii and
  spacing, `motion.py` for spring presets and crossfade durations), `audio/`
  (microphone worker, recognition, matching), `tracking/` (camera and grid
  motion), `platform/` (Windows hotkeys and capture exclusion), and `storage/`
  (atomic state persistence). Shared filesystem paths live in `paths.py`.
- `main.py` is the compatibility launcher used by `start-notch.bat` and the
  PyInstaller spec; the real entry point is `notch.app.main` (or
  `python -m notch`). `run.py` is the development launcher.
- `tests/` holds the standalone checks, the screenshot harnesses and their
  fixture. They run from the repository root and put it on `sys.path` before
  importing `notch`.
- `packaging/` holds only build configuration and the icon sources.
- `assets/` and `docs/` are presentation material, not runtime code.

## Checks

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_behavior.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_voice_follow.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_waveform.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_speech_engine.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_robustness.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_note_clearing.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_note_placement.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_motion.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_upgrades.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_head_tracking.py
.venv/Scripts/python.exe tests/check_storage.py
.venv/Scripts/python.exe tests/check_diagnostics.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_predictive_highlight.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/bench_predictive_highlight.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_predictive_speech.py
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

`tests/check_motion.py` covers the floating surfaces' motion and the two
shared token modules. A note is created and driven frame by frame: it starts
at 94% and transparent, grows about its own corner without moving, lands on
exactly 260×165, leaves on a quicker spring, announces `closed` once, and
reverses when it is grabbed mid-dismissal. It also checks that a drag stays
1:1, that the release coast is bounded by `NOTE_RELEASE_LIMIT` and stops when
the note is taken hold of, and that reduced motion turns the whole thing into
an instant appearance and close. The palette half measures every role against
its own surface in both themes (body 15:1, accents 7:1, spoken 4.5:1,
hairline visible) and asserts that the reading tiers and the tooltip panel
resolve to the `theme` roles rather than to private copies. Add a token here
when a new surface needs a duration, and read it from `motion` in the code:
the check is the only thing stopping the next literal from becoming drift.

`tests/check_upgrades.py` writes the state and speaking-profile formats an
older build produced, loads them through the current code and launches the
real overlay against them. It checks that the script, notes, preferences,
placement and learned profile all survive, that the old file is kept as
`*.v<schema>.bak` before its first rewrite, that a corrupt profile recovers
its `.bak`, and that a file from a newer schema is snapshotted rather than
silently discarded. A change to `notch/storage/store.py`, the profile fields
or the settings keys belongs here. The v1-to-v2 step that adds `saved_at` is
the worked template: raise `SCHEMA`, add one function to `MIGRATIONS` that
copies the payload and gives the new field a safe default, and extend this
check with a file written at the old version.

`tests/check_storage.py` is the read-only report for a reader's data folder. It
lists every `state.json*` and `speaking-profile.json*` file with its role,
schema and size, says which copy the app would load, names the script, notes
and learned profile that are recoverable, counts the timestamped session
copies and migration snapshots, and flags a pending migration or a newer
schema. It never writes, so it is safe to run against the real
`%APPDATA%\NotchFree`; `--dir` points it at a fixture instead. It exits
non-zero only when neither the primary nor the backup of a store can be read.

`tests/check_diagnostics.py` pins the support evidence a windowed build
leaves behind. It starts fresh interpreters with stdout and stderr set to
`None` — the state a frozen windowed build runs in — and checks that printed
output reaches the process log, that a main-thread and a worker-thread
exception are both recorded, and that an unwritable install directory falls
back to `%LOCALAPPDATA%\NotchFree\logs`. It patches `notch.paths.app_dir` in
the child process, so it never writes beside the repository. A build that
cannot write a log is a build nobody can support, which is why the release
workflow runs it before the installer step.

`tests/soak_packaged.py` is the eight-hour endurance monitor. It hashes the
packaged tree, launches the exe with an isolated `APPDATA` and
`NOTCH_SOAK_DIR`, and fails on an early exit, a missing startup heartbeat, a
heartbeat more than 60 seconds stale, any ERROR or CRITICAL line in the
process log, or a non-empty native fault log. It runs for 28,800 seconds and
always writes its report, so a killed monitor cannot be mistaken for a pass.
Close any other copy first: the single-instance mutex makes the soak's own
launch exit and the report says FAIL. A pass proves survival and that the GUI
event loop answered; using the app during the run, and reading the logs
afterwards, is still the human half.

`tests/check_speech_engine.py` decodes real audio through the installed model
and reports SKIP when `model/` has none. `tests/bench_speech_engines.py` is the
benchmark that chose the engine; it synthesises its fixtures with the local
SAPI voices and needs no network.

`tests/check_predictive_highlight.py` scores the anticipatory layer: adaptive
rate, pause freeze, line bound, anti-flicker, profile persistence, theme
contrast and update cost. `tests/bench_predictive_highlight.py` runs a known
speaker timeline through the predictor and reports coverage, lead, precision,
flicker, pause growth and per-update/paint latency; it is the number the
tuning constants in `notch/audio/predict.py` are chosen against.
`tests/check_predictive_speech.py` decodes three SAPI sentences with the real
model in wall-clock time and drives the real overlay. `--live` uses the
production microphone worker for a real reading and records it, and
`--replay <session>` decodes that recording again through the same recogniser,
which is how one human reading becomes a repeatable real-audio benchmark.
Sessions land in `.sessions/` (ignored by git). The design and measured
results are in `docs/predictive-highlight.md`.

`tests/profile_reading.py` drives the real overlay offscreen at recogniser
cadence and reports frame cadence, result-to-paint latency, CPU and working
set. `--interval 17 --undo` reproduces the build before the scroll-clock and
undo-stack fixes, so the two profiles can be compared directly.

`tests/calibrate_voice.py` scores a speaker against the corpus and drives the
overlay with the recordings (`--no-mic --reader` re-scores takes already in
`%TEMP%\notch-voice`). Recordings stay local WAVs; nothing is uploaded.

`tests/check_head_tracking.py` drives `HeadMotion` with explicit timestamps at
the cadence the worker achieves (14.7 Hz — a 66 ms camera frame plus the
inference, not the 15 fps it asks the camera for). It measures all ten regions
of the 5x2 upper-half grid, the continuity of every transition, the upper-half
and screen bounds, calibration, dead zones, hysteresis, jitter, fusion of head
/ iris / face direction, release, and disable. It then exercises the reader
itself (default off, absolute region placement, on-screen clamping, immediate
disable, camera-failure path) and drives the real worker against a simulated
device for dropout recovery, reopen backoff, restart and no-face behaviour.
`check_seed` pins the switch-on path: the spring is seeded from the card's
parked position, so enabling eases to the neutral region instead of
teleporting there. The recovery check holds the simulated device down for
thirteen seconds as well as three, because the field report was tracking
stopping after 10-15 s and the old worker gave up at twelve.

Several of its checks exist because the feature failed in the field without
any test failing. `check_detection_range` holds the frame size to the
detector's measured floor: the old frontal cascade needed a face box around
56 px, so a reader 80 cm from the lens needs roughly 93 px of head, which
320 px of frame never gave them — the detector returned nothing and the card
never moved. That is why the frame is 640×480 and why the detector is now a
MediaPipe landmarker, which reads a face far smaller than that.
`check_calibration` includes a camera that delivers one reading and then
stops: calibration used to be advanced only from incoming samples, so that
state left the tracker calibrating for ever; a polling deadline now resolves
it. It also holds the window to a still head, because a lean captured as
neutral turned every later lean into the wrong-direction travel.
`check_landmarks` pins the sign of every axis — head position, iris, yaw and
pitch — from a synthetic 478-point face, because a wrong sign looks like the
words running away, not like a bug. `check_outliers` drops one wild reading
and follows two that agree. `check_continuity` walks the card across all ten
cells and asserts that no painted frame carries a snap; `check_fusion` holds
that the eyes refine the position inside a cell but do not get a vote on
crossing a boundary, which is the shape that hunts when the card moving toward
a glance changes the glance. `check_jitter` includes a head hovering on a
boundary, which is what the hysteresis is for.

The camera worker's recovery policy is deliberate. It gives up only when the
device has *never* opened, within twelve seconds; after a first frame it keeps
retrying with a capped backoff, because turning the feature off after twelve
seconds of a transient problem made tracking appear to stop after 10-15 s. It
remembers the backend that worked (DirectShow first, MediaFoundation as the
fallback; measured on this machine: MSMF's constructor took 5.5 s and the
first reading arrived at 11.8 s, DSHOW about one second), so a reopen does not
probe all three and flash the camera light. A watchdog thread releases a
capture whose read has stopped answering, which is the only way to get an
OpenCV read out of a blocking wait. A `stop()` that times out leaves `_thread`
set on purpose — `running` stays true so a second source cannot open a second
handle on a device the first has not released — and only one source may hold
the camera at a time, which is what stops the light flickering. The reader
reuses that source on re-enable and stops it on close.

`tests/measure_head_tracking.py` is the real-device half — fresh-reading rate,
the largest gap between readings, frame cost, stillness, and a guided walk
across all ten regions followed by a continuous phase. It needs a person in
front of the lens; it reports the regions it actually selected and does not
pretend a missed pose is a pass. It SKIPs when there is no camera or no face.

`tests/check_design.py` asserts surface invariants against rendered pixels and
`tests/verify_ui.py` reports window geometry. All of them build their own
temporary store, so they never touch the saved script or notes.
