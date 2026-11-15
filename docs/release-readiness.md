# Release readiness

The packaged eight-hour soak for 1.6.2 passed; its candidate identity, the
monitor report and the check output are archived in `docs/release/1.6.2.md`.
The release is still blocked on signing and on the tester's own notes from that
run. Preparing a harness is not a completed soak; a passing report from one
candidate is, and only for that candidate's bytes.

## Diagnostics

Startup and Python exceptions go to `notch-<pid>.log` beside the executable.
Each file rotates at 2 MiB, retaining two previous parts. Native fault traces
go to `notch-fault-<pid>.log`. If the installation directory is not writable,
the fallback is `%LOCALAPPDATA%\NotchFree\logs`. Each launch writes its own
session files, so startup keeps the five newest sessions and deletes older
ones; the running process never deletes its own.
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
computer awake. A killed monitor or missing report is not a pass. The 1.6.2
run completed 28,801.4 seconds and passed.

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

The release audit reported 8–9.5 seconds for packaged model loading, and README
quotes that range as audit evidence rather than a fresh measurement. The 1.6.2
candidate reaches speech recognition 3.9–4.5 seconds after process start on
this machine, measured over three launches of the packaged exe; a cold first
launch after installation can be slower, which is why README keeps the wider
range. Re-measure if the machine, the model or the freeze changes.

## Signing and release

An OV/EV signing certificate and access to its supported signing provider are
still required. Do not put private keys or passwords into the repository.

`packaging/sign.bat` signs one artifact when `NOTCH_SIGN_CERT` points at a
`.pfx`; `NOTCH_SIGN_PASSWORD` is its password and `NOTCH_SIGN_TIMESTAMP`
overrides the default RFC 3161 server. `build-installer.bat` calls it twice, in
the order a release verifies: `dist\Notch\Notch.exe` before Inno Setup runs and
`dist\NotchSetup-<version>.exe` after. `NOTCH_SIGN_REQUIRED` turns the
unconfigured path into an error, which is what a release build sets; a
developer build without a certificate stays unsigned and says so.

A tag now carries both gates. Before anything is built, the workflow requires
`docs/release/<version>-soak-result.json` to report PASS, at least 28,800
seconds and a packaged-file manifest. Before anything is published, it restores
the certificate from `secrets.NOTCH_SIGN_CERT_PFX` (a base64 `.pfx`) with
`secrets.NOTCH_SIGN_CERT_PASSWORD`, deletes it afterwards, and fails unless
`Notch.exe` and the installer both verify as valid, attributable and
timestamped. A `workflow_dispatch` build is held to neither gate because it has
no version to check.

Verify both signatures and timestamps on a clean Windows machine as well: the
workflow sees the runner's copy, not what the installer writes.

Rebuilding after the soak creates different bytes; do not present an earlier
candidate's report as proof of the rebuilt artifact. Archive the signed
installer, its SHA-256, the matching packaged-file manifest, soak report and
check output; for 1.6.2 those last three are in `docs/release/` and the
installer stays in `dist/` and in the release attachment.
