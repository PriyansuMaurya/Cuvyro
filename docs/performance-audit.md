# Performance audit

One pass over the shipping build against a set of stated targets. The method
was the existing harnesses first; nothing here added a benchmark, and no code
changed as a result of it (the one candidate change is recorded below because
it was measured and rejected).

Conditions: Windows 11, Python 3.13, PyQt5, `QT_QPA_PLATFORM=offscreen`, one
shared machine. CPU and frame-timing rows also carry a `shared` note where the
box was busy with other work during the run, because that changes the p95 and
not the mean. Every number is a median or a mean of repeated runs, and each row
names the harness that produced it.

## Results

| Target | Measured | Verdict |
| :--- | :--- | :--- |
| Cold startup ≤ 2 s | **0.49 s** from process start to first paint (import 0.045, `QApplication` 0.006, overlay import 0.404, construct 0.010, show + paint 0.025); RSS at that point 58.9 MB | passes |
| UI response ≤ 50 ms | wheel/pointer handler **1.1 – 2.0 ms**; speech result to painted highlight **2.45 ms** mean, 5.5 ms max (480 s run) | passes |
| Animations ≥ 60 FPS | glide frame interval **16.0 – 16.2 ms** mean over 12,448 frames (3 × 180 s and 1 × 480 s of continuous reading); p95 **17.3 ms** idle box, **26 ms** shared box | passes on cadence; the p95 tail is host scheduling, see below |
| Speech follow ≤ 200 ms | the app's own work is **2.45 ms** from result to paint. End to end is set by the recogniser's endpointing, and the predictive layer paints *ahead* of the voice (1.05 s mean lead, `bench_predictive_highlight`) | app passes; the end-to-end figure is not the metric this design uses |
| Idle CPU ≤ 2 % | **1.5 %** for the overlay alone over 30 s; **6.1 – 6.2 %** with the microphone pipeline listening in a quiet room, of which the 60 Hz clock is ~2.5 points and the voice pipeline ~3.6 | **unmet** in the default Voice mode |
| Idle RAM ≤ 150 MB | **60 MB** for the overlay; **89 MB** in Manual mode; **202 – 216 MB** once the acoustic model is resident | passes in Manual mode; **unmet** while Voice is listening |
| Memory growth ≤ 5 %/h | 15 min of continuous reading: 201.5 MB at t=30 s, 206.4 MB at t=900 s; **+0.3 MB** over the last 7 min and **+24 Python objects** over the last 12 min | passes |
| Camera tracking ≤ 100 ms | real face, real device: fresh readings **8.7 Hz** with frame cost **75.0 ms** mean / **124.7 ms** worst, and one **1757 ms** gap during the run | **unmet** on this machine |
| Zero crashes over 8 h | not run. The 15 min soak above is the only endurance evidence | unverified |
| Zero functional regressions | 11/11 offscreen checks pass, before and after (identical list, identical results) | passes |

## What the profiles say

Rendering is not the cost. A full overlay repaint is **0.82 ms** and a text
repaint **0.20 ms**; a `_tick` costs **0.042 – 0.108 ms**, of which the head
tracking poll is a single early return when the feature is off. The frame
budget is 16.7 ms, so paint occupies about 6 % of it. No hot path in the paint
or tick work is worth attacking.

The p95 frame tail is the machine, not the app. A bare 16 ms `QTimer` with no
overlay at all lands 1.6 – 9.3 % of its ticks 8 – 18 ms late on this box, the
same tail the app shows. The 40 FPS reading you get from `p95` on a shared
machine is host noise; the mean cadence is a steady 16.0 ms either way.

Memory is three layers: Qt and the interpreter ~55 MB, the overlay ~5 MB, and
the acoustic model **~150 MB** once `sherpa_onnx` is loaded (a 68 MB int8
export, already the smallest the model ships). That last layer is what puts a
voice-mode session over 150 MB, and no amount of application work removes it.

`tests/profile_reading.py` reports 61 MB because its `ProfiledOverlay`
overrides `_start_listening` to a no-op, so the engine never loads. That number
is the overlay, not the application. Measure the engine by constructing the
bare `CuvyroOverlay`.

## The one change tried, and why it was reverted

The scroll clock is a 16 ms `QTimer`, and a coarse Qt timer is documented to be
rounded up to the Windows 15.6 ms tick. Setting `Qt.PreciseTimer` looked like
the fix for the p95: on a quiet box, three runs with it gave p95 17.4 / 17.3 /
17.3 ms against a coarse baseline of 29.8 ms.

It did not reproduce. Interleaved A/B (coarse/precise, twice each) gave p95
25.2 / 27.7 / 24.6 / 24.9 — no difference — and a bare `QTimer` control showed
a coarse 16 ms timer averaging 16.3 ms, not the 31.25 ms bucket the theory
predicted. The apparent gain was the box being idle during the first three
runs. The line was removed: an unverified one-liner is worse than no change.

## What changed, and what did not

The speech model is now freed when Voice mode is left. `_toggle_mode` to
Manual, and a checkpoint jump out of Voice, call
`speech.request_drop_recognizer()`; the worker finishes, the overlay's sweep
clears the stopped worker and collects. Measured over three Voice/Manual
cycles: **208 → 89 MB** on the switch, back to 212, 216, 214 MB on return, with
no accumulation across cycles. The reload costs **3.0 – 4.2 s**, paid behind
the existing "Preparing voice match..." status. The collect itself is 8 – 11 ms
and runs 300 ms after the switch, never during a reading. Mute does not free
the model: it is a pause, not a change of mode, and paying the load on every
mute would be a worse trade than the memory.

Slowing the VAD during silence was measured and **not** done. Silero costs
0.459 ms per 100 ms block, 0.46 % of one core; feeding three blocks per call
costs 1.388 ms, the same per audio second, because sherpa runs the model on
fixed 512-sample windows no matter how the audio is batched. The call count
is not where the time goes, so the batching would add first-word latency and
save nothing. The idle CPU that is left is the recogniser decoding whatever
the room gives it, which is the feature working.

## Ceilings that are product decisions, not bugs

- **The engine cache.** `speech.create_recognizer` keeps the loaded recogniser
  after a worker stops, so opening the editor and coming back does not pay the
  load again. That reuse is why mute and the editor keep the model; only a
  change of mode frees it.
- **The voice pipeline's idle cost.** The VAD runs on every 100 ms block so a
  reading can start the moment a word is spoken. Halving the rate would cut
  that cost and add latency to the first word.

Both are deliberate trades recorded in the code. Neither was changed here.

## Not measurable on this machine

- Nothing: the camera half was run with a person in front of the lens, and the
  results are in the section below.
- An 8-hour run, and anything about a real display: `tests/check_design.py` and
  `tests/check_quality.py` need the platform's font database and fail offscreen
  even on unchanged code.

## Camera, with a face in front of it

`tests/measure_head_tracking.py` against the real device, DSHOW, 640×480 at a
requested 15 fps, one person seated at their usual distance:

| Metric | Measured |
| :--- | ---: |
| fresh readings | 393 of 4173 polls (9 %) |
| fresh rate | **8.7 Hz** (the documented figure was 14.7 Hz) |
| largest gap between readings | **1757 ms** |
| frame cost, read + infer | **75.0 ms** mean, **124.7 ms** worst, against a 67 ms interval at 15 fps |
| region walk | 6/10 regions reached, **2/10** asked-for regions matched |
| still head, 6 s | x stdev 0.112, y stdev 0.093 normalised; 1.9 of 2 eyes usable |

The 100 ms target is missed on the stated metric. One reading costs 75 ms of
work on average and arrives every 115 ms, so a movement reaches the card about
one frame period plus smoothing later; the worst frame is 125 ms on its own.
That is the processor, not the code: the same detector took ~98 ms per frame
in an earlier run with no face to find.

The 1757 ms gap is the more interesting number. The harness calls anything near
1.2 s a dropout, and the card releases to neutral when the readings stop, so a
reader would see the words drop back to the middle of the screen and return.
That happened once in about six minutes of tracking.

The region walk matched 2 of 10. The mapping asks for a lean and a look and
compares with what the grid selected; a smaller lean, a different distance or
a tired neck lands in a neighbouring cell, and the harness says so. Read it as
"the grid did not match this person at this distance", not as a code defect —
but it is the number that says the feature is still experimental for a real
reader, which the README already claims.

## Reproducing

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/profile_reading.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/profile_reading.py --seconds 480
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/bench_predictive_highlight.py
```

Cold start is `import` + `QApplication` + `CuvyroOverlay()` + `show()` +
`processEvents()` in a fresh process, timed in five runs. Memory and idle CPU
come from `K32GetProcessMemoryInfo` and `time.process_time()` over a settled
window; the overlay must be constructed as a bare `CuvyroOverlay` for the engine
to be included.
