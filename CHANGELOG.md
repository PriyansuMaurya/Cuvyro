# Changelog

Notch is versioned with [semantic versioning](https://semver.org/): a minor
release adds behaviour, a patch fixes it, and a major release may break a
saved state or a workflow. Each release is a `vX.Y.Z` tag on `main`; this file
starts at 1.6.0, and earlier releases are described by their tags and commit
messages.

## Unreleased

Pre-launch hardening found by an independent read of the shipping paths.

### Added

- Windowed builds now leave support evidence. `notch.diagnostics` writes a
  rotating `notch-<pid>.log` beside the executable, falling back to
  `%LOCALAPPDATA%\NotchFree\logs`, records unhandled exceptions from the
  main thread and from worker threads, and keeps native fault traces in
  `notch-fault-<pid>.log`. A windowed build has no console, so without this
  a launch-day crash left nothing to read.
- `tests/soak_packaged.py` is the eight-hour endurance monitor: it hashes the
  packaged tree, runs the exe against an isolated state folder, and fails on
  an early exit, a stale GUI heartbeat, any logged error, or native fault
  evidence. `docs/release-readiness.md` records what a pass proves and what
  signing still blocks.
- `tests/check_diagnostics.py` proves exception capture in fresh windowed-
  style processes, and the release workflow now runs it before the installer
  step.

### Fixed

- A release build now ships the engine it is measured with. The workflow
  fetched only the Vosk fallback, which `build-installer.bat` accepts, so a
  tagged installer would have run the recogniser the benchmark replaced (4.3x
  realtime, RMS gate instead of Silero) while every documented figure came from
  sherpa-onnx. The workflow fetches `model/sherpa`, `silero_vad.onnx` and the
  face landmarker, asserts them, and verifies the frozen tree carries them.
- A tag can no longer publish an untested build. The release workflow runs the
  offscreen checks before the installer step and fails on any non-zero check.
- Two copies can no longer run at once. They shared one state file, one camera
  and one set of global chords, and the last save won. `notch.platform.
  single_instance` takes a session mutex; the window explains the situation and
  exits, and the installer uses the same name as its `AppMutex`.
- The reader's own words are no longer written to the log at partial cadence,
  twice per result. Transcript tracing is opt-in with `NOTCH_TRACE_TRANSCRIPT=1`.
- The microphone ribbon drains on a time constant instead of a fixed fraction
  per frame, so an unplugged or muted mic clears in the same wall-clock time on
  a busy host as on an idle one.

### Changed

- `tests/check_design.py` wakes the control rail before measuring a control's
  shape, because the three-second idle fade could otherwise be measured as a
  broken (half-transparent) circle on a slow host.
- `tests/check_robustness.py` checks the cross-process mutex and the silent
  default for transcript logging; `tests/check_waveform.py` checks that one
  late frame drains as far as a frame budget would.
- README gives the packaged model load as 8-9.5 seconds instead of the 4.2
  seconds measured from a source run, and describes head tracking as
  experimental and off by default for new installations.
- `tests/check_robustness.py` reports SKIP instead of a failure for the
  session-mutex checks when another reader already holds it, so a
  developer's open window or a running soak no longer turns the suite red
  while the guard is doing its job.

## 1.6.1 — 2026-11-14

A patch release for the installer pipeline; no application code changed. The
installer builder, the Inno script, the frozen exe's version resource and the
README's build command all name 1.6.1.

### Fixed

- The release workflow now fetches `model/face_landmarker.task`. Its only model
  step unpacked the Vosk speech model, and `model/` is not tracked by git, so a
  fresh runner had the fallback recogniser and nothing for head tracking. The
  build script has required the landmarker since head tracking moved to the
  MediaPipe detector; the v1.6.0 tag build stopped on that check before
  PyInstaller ran, so the tag published no installer and no release. The step
  also rejects a file under 1 MB, which is the size a saved error page would be.

## 1.6.0 — 2026-11-14

Ten commits landed after 1.5.0 without a release of their own, plus a new
tooltip layer. The installer builder, the Inno script, the frozen exe's
version resource and the README's build command all name 1.6.0.

### Added

- Anticipatory shading. A local predictor paints the next word or phrase from
  the speaker's learned rate, the pause state and a five-weight timing model,
  so the card no longer waits for the recogniser to finish a block before it
  shows where the voice is going. The shading is bounded by the rendered line
  and never moves the reading line, the anchor or the scrollbar.
  `tests/check_predictive_speech.py --live` records a real reading into
  `.sessions/`, and `--replay` decodes that recording again through the same
  worker, which turns one human reading into a repeatable real-audio check.
- One tooltip layer for every surface (`notch/ui/tooltips.py`). The panel is
  painted in the menu's material, with the same hairline and bright top edge,
  rounded to 8 DIP on three soft shadow rings, and it is excluded from screen
  capture like the rest of the app. Hovering a control for 650 ms opens it;
  reaching one with Tab opens it after 350 ms, which is the first time a hint
  was available without a pointer. It is placed beside the control, or beside
  the menu at the row's height, clamped to the work area, and it leaves on
  pointer-out, press, wheel, any key, focus loss, app deactivation, the close
  of its window, and after nine seconds regardless.
- A recorded demo of the whole app. `tests/record_demo.py` drives the panel,
  setup, help, the pill and both themes through the app's own methods at 2x
  and encodes a GIF from the widget tree, so the README no longer needs a
  desktop recording to show a surface.
- `CHANGELOG.md`.

### Changed

- The application is a responsibility-based package: `notch/ui`, `notch/audio`,
  `notch/tracking`, `notch/platform` and `notch/storage`, with shared model and
  install paths in `notch/paths.py`. `main.py` remains a thin launcher for
  `start-notch.bat` and the PyInstaller spec.
- The repository sits at the workspace root with a single `.gitignore`, so the
  app, its model benchmarks and the Electron rebuild share one history and one
  ignore policy.
- Head tracking follows the head rather than a fixed offset: capture is
  640x480, the detector is a MediaPipe landmarker that reads 478 points
  including both irises, the head anchor comes from the outer eye corners, and
  movement is 0.45 of the head's travel inside the upper half's 5x2 grid.
  DirectShow is opened before MediaFoundation, and a camera that has delivered
  one frame is retried with a capped backoff instead of being abandoned.
- Manual mode's controls are system-wide chords registered through Windows:
  Ctrl+Shift+Space starts or pauses and Ctrl+Alt+Left/Right change the pace.
  They are off until the menu's Global keys row or Setup switches them on, a
  chord owned by another application is reported (error 1409) instead of
  failing silently, and every chord is released when the mode changes or the
  reader closes. Window-scoped Space and arrows remain the fallback.
- Sticky-note placement is a bounded anchored lattice with a deterministic
  fallback, so the same desktop always produces the same corner and the fourth
  note no longer covers the first.
- Tooltip wording describes an outcome instead of a gesture ("New note",
  "Mute microphone", "Advance as you speak"), stays inside 72 characters, and
  keeps a shortcut in parentheses at the end. `tests/check_design.py` enforces
  the rules on every declared string in `overlay.py`, `settings.py`,
  `notes.py` and `tooltips.py`.
- The README is a guide to this checkout: setup and model files, the modes and
  keys that exist, where state is stored, how reading and tracking work, the
  checks, and the remaining limits. The product-page claims that the code no
  longer matched are gone.

### Fixed

- Manual mode kept scrolling with another application in front. The card could
  not take the keyboard at launch or win it back after its own menu, Help or
  Setup had closed, so window-scoped shortcuts never existed when they were
  needed.
- Head tracking no longer goes inert: the old 320x240 frame left a face at
  reading distance under the detector's measured 56 px floor, calibration
  could stall for ever or capture a lean as the neutral pose, the profile
  cascade ran on every frame instead of only on a miss, and the worker gave up
  on a camera that had already produced a frame. A reading pass now also logs
  once when no face is in view, so "switched on" and "switched on but inert"
  stop looking identical.
- Tooltips painted an unpainted slab over the light card, because Qt built them
  from the transparent control rail's palette, and the application stylesheet
  that followed could only recolour a frame Qt had drawn. They are painted by
  the app now, so both themes, both surfaces and the keyboard path agree.
- `tests/check_design.py` covers the tooltip layer against rendered pixels:
  opacity, theme, contrast, corner radius, shadow, type, placement, dismissal,
  keyboard arrival and wording.

### Removed

- The application-wide `QToolTip` stylesheet, superseded by the painted layer.
- `rea-work/app-js/package.json`, the last tracked file of the superseded
  Electron tree.
