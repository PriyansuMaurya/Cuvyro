# Release readiness

The packaged eight-hour soak for 1.6.2 passed; its candidate identity, the
monitor report and the check output are archived in `docs/release/1.6.2.md`.
The release is still blocked on signing and on the tester's own notes from that
run. Preparing a harness is not a completed soak; a passing report from one
candidate is, and only for that candidate's bytes.

## Diagnostics

Startup and Python exceptions go to `cuvyro-<pid>.log` beside the executable.
Each file rotates at 2 MiB, retaining two previous parts. Native fault traces
go to `cuvyro-fault-<pid>.log`. If the installation directory is not writable,
the fallback is `%LOCALAPPDATA%\Cuvyro\logs`. Each launch writes its own
session files, so startup keeps the five newest sessions and deletes older
ones; the running process never deletes its own.
If both directories are unwritable, logging cannot be guaranteed.

Windowed builds route stdout and stderr to the process log. Transcript tracing
remains opt-in; do not enable it in a distributable support configuration.
Exceptions can contain paths or other application details; review logs before
sharing them. Native termination before Python starts cannot be caught here.

## Packaged soak

Close any other copy of Cuvyro first. On the Windows test machine, run:

```
python tests/soak_packaged.py dist/Cuvyro/Cuvyro.exe --report soak-result.json
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

## GitHub releases and the Microsoft Store

The two release paths reuse the Windows build, the offscreen checks, and the
packaged-engine checks. Publish a stable GitHub release from a reviewed
`vMAJOR.MINOR.PATCH` tag. The workflow packages `MAJOR.MINOR.PATCH.0` for Store
product `9MX46V9WW83Q`. A tag push can also create the GitHub release after
signing the EXE. The Store job follows GitHub publication in that same run,
because a release created with `GITHUB_TOKEN` does not trigger another workflow.

Both paths require the archived eight-hour result for the tag version at
`docs/release/<version>-soak-result.json`. Build and soak the release candidate
before tagging. The gate checks the archived result and its file manifest.
Keep the signed candidate and its hashes if you need byte-for-byte evidence.
The release workflow builds from the tag. Its MSIX hash covers that build's
validated package from artifact upload through Store submission.

To verify the Windows build without publishing, run **Build Windows installer**
from a branch and select **msix**. Set the version explicitly for the candidate.
The downloadable **Cuvyro-Store-MSIX-x64** Actions artifact contains the MSIX.
The workflow checks its manifest and engine files. Install and test the
certified build from the Store on Windows as well.

### Store setup

Link a Microsoft Entra tenant to Partner Center. Add a dedicated Entra
application with the Manager role required by Microsoft's publishing workflow.
Set these GitHub Actions secrets.

| Secret | Value |
| --- | --- |
| `STORE_TENANT_ID` | Entra tenant ID |
| `STORE_CLIENT_ID` | Entra application (client) ID |
| `STORE_CLIENT_SECRET` | Entra client secret value |
| `STORE_SELLER_ID` | Partner Center seller ID |

[Microsoft's GitHub Actions setup](https://learn.microsoft.com/en-us/windows/apps/publish/msstore-dev-cli/github-actions)

To verify the credential setup, manually run **Build Windows installer** and
select **check-store-access**. It reads Cuvyro's live submission and shows its
x64 version in the run log.

The workflow submits the MSIX for certification. Microsoft signs the Store
package and publishes it after approval. The Store job skips versions already
live or in review and stops if another submission is pending. After resolving a
pending draft or failed submission in Partner Center, rerun the failed Store
job while the build artifact is available. Check Partner Center for the
certification result.

### Standalone GitHub installer signing

Configure a publicly trusted Authenticode certificate with a signing provider.
On a dedicated Windows signing runner, install the Windows SDK, Inno Setup 6
and the provider's software. Make the provider certificate and its signing key
accessible through the runner service user's `CurrentUser\My` certificate
store. Configure the provider to sign without an interactive prompt. Set the
GitHub Actions variables `CUVYRO_SIGN_RUNNER` to the dedicated runner label and
`CUVYRO_SIGN_THUMBPRINT` to the certificate thumbprint.

`build-installer.bat` signs the frozen app before compiling the installer, then
signs the installer. The release job verifies both signatures and RFC 3161
timestamps before attaching the EXE. Local development builds can run without
signing configuration. Existing legacy PFX configurations using
`NOTCH_SIGN_CERT_PFX` and `NOTCH_SIGN_CERT_PASSWORD` still work where the
certificate provider supports that format.
