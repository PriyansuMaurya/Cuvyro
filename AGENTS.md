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
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/profile_reading.py
.venv/Scripts/python.exe tests/check_design.py      # needs a real display
.venv/Scripts/python.exe tests/check_quality.py     # needs a real display
```

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

`tests/check_design.py` asserts surface invariants against rendered pixels and
`tests/verify_ui.py` reports window geometry. All of them build their own
temporary store, so they never touch the saved script or notes.
