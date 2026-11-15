# Release readiness

The release remains blocked until signing and the packaged eight-hour soak
have been completed. Preparing a harness is not a completed soak.

## Diagnostics

Startup and Python exceptions go to `notch-<pid>.log` beside the executable.
Each file rotates at 2 MiB, retaining two previous parts. Native fault traces
go to `notch-fault-<pid>.log`. If the installation directory is not writable,
the fallback is `%LOCALAPPDATA%\NotchFree\logs`. Logs from previous processes
are retained for support; remove old sessions after collecting the evidence.
If both directories are unwritable, logging cannot be guaranteed.

Windowed builds route stdout and stderr to the process log. Transcript tracing
remains opt-in; do not enable it in a distributable support configuration.
Exceptions can contain paths or other application details; review logs before
sharing them. Native termination before Python starts cannot be caught here.

## Packaged soak

Close any other copy of Notch first. On the Windows test machine, run:

```
python tests/soak_packaged.py dist/Notch/Notch.exe --report soak-result.json
```

The monitor uses an isolated state folder and runs for 28,800 seconds. It
fails on an early exit, absent startup heartbeat or a GUI heartbeat more than
60 seconds old. The report includes hashes of the packaged files. Keep the
computer awake. A killed monitor or missing report is not a pass.

This measures process survival and GUI responsiveness. During the run, exercise
Voice and Manual modes, microphone loss/recovery, notes and editor changes,
theme changes and global shortcuts. Keep head tracking off for the baseline;
test its opt-in path separately. Record those observations and inspect all
exception/fault logs before accepting the build. The monitor's PASS alone is
not evidence that audio matching or every feature worked for eight hours.

## Head tracking and timing

Head tracking stays experimental and default-off for new installations.
Existing explicit opt-ins are preserved. Do not promote it in headline claims
until region calibration has passed real-camera validation.

The release audit reported 8–9.5 seconds for packaged model loading. README
now uses that range; it is supplied audit evidence, not a new measurement
from this change. Repeat the measurement on the release candidate.

## Signing and release

An OV/EV signing certificate and access to its supported signing provider are
still required. Do not put private keys or passwords into the repository.
Configure signing for the certificate provider on the Windows build machine,
sign `Notch.exe` before compiling the installer, then sign the installer.
Verify both signatures and timestamps on a clean Windows machine.

The existing workflow checks behaviour and bundled engines, but does not yet
enforce certificate verification or soak evidence. Do not create the release
tag until those gates are integrated and the exact candidate passes. Rebuilding
after the soak creates different bytes; do not present an earlier candidate's
report as proof of the rebuilt artifact. Archive the signed installer, its
SHA-256, the matching packaged-file manifest, soak report and check output.
