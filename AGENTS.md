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

## Checks

```
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe packaging/check_behavior.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe packaging/check_voice_follow.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe packaging/check_robustness.py
.venv/Scripts/python.exe packaging/check_design.py      # needs a real display
.venv/Scripts/python.exe packaging/check_quality.py     # needs a real display
```

`packaging/check_design.py` asserts surface invariants against rendered pixels
and `packaging/verify_ui.py` reports window geometry. All of them build their
own temporary store, so they never touch the saved script or notes.
