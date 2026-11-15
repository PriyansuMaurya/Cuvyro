"""The signing helper's contract, exercised with a stand-in for signtool.

packaging/sign.bat is called by build-installer.bat before Inno Setup and again
after it. The only thing between a release build and an unsigned artifact is
this script refusing to exit 0, so its branches are pinned here rather than
trusted. A stub named through NOTCH_SIGNTOOL replaces the real signtool, which
means no certificate is needed and nothing contacts a timestamp server.

    .venv/Scripts/python.exe tests/check_signing.py

Needs Windows: the helper is a batch file and the workflow calls it there.
"""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SIGN = ROOT / "packaging" / "sign.bat"

failures = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


STUB = """@echo off
echo %*>>"{log}"
if /i "%~1"=="sign" if defined STUB_FAIL_SIGN exit /b 1
if /i "%~1"=="verify" if defined STUB_FAIL_VERIFY exit /b 1
exit /b 0
"""


def main():
    check("the signing helper exists", SIGN.exists(), str(SIGN))

    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        target = temp / "artifact.exe"
        target.write_bytes(b"not a real executable")
        cert = temp / "certificate.pfx"
        cert.write_bytes(b"placeholder")
        calls = temp / "calls.log"
        stub = temp / "signtool.bat"
        stub.write_text(STUB.format(log=calls), encoding="utf-8")

        def run(**overrides):
            env = {k: v for k, v in os.environ.items() if not k.startswith("NOTCH_SIGN")}
            env.pop("STUB_FAIL_SIGN", None)
            env.pop("STUB_FAIL_VERIFY", None)
            env.update({k: str(v) for k, v in overrides.items()})
            if calls.exists():
                calls.unlink()
            result = subprocess.run(["cmd", "/c", str(SIGN)] + (
                [str(target)] if "no_target" not in overrides else []),
                capture_output=True, text=True, env=env)
            return result.returncode, result.stdout + result.stderr

        rc, out = run(no_target=True)
        check("a missing argument is refused", rc == 1 and "no file given" in out,
              f"rc={rc}")

        rc, out = run(NOTCH_SIGN_CERT=cert, NOTCH_SIGNTOOL=stub, no_target=True)
        check("a missing argument is refused even when signing is configured",
              rc == 1 and "no file given" in out, f"rc={rc}")

        rc, out = run()
        check("an unconfigured local build is left unsigned", rc == 0,
              f"rc={rc} {out.strip()}")

        rc, out = run(NOTCH_SIGN_REQUIRED=1)
        check("a release build refuses to skip signing",
              rc == 1 and "NOTCH_SIGN_REQUIRED is set but NOTCH_SIGN_CERT is not" in out,
              f"rc={rc}")

        rc, out = run(NOTCH_SIGN_CERT=temp / "absent.pfx")
        check("a missing certificate is an error, not a skip",
              rc == 1 and "certificate not found" in out, f"rc={rc}")

        rc, out = run(NOTCH_SIGN_CERT=cert, NOTCH_SIGNTOOL=stub, NOTCH_SIGN_PASSWORD="pw")
        logged = calls.read_text(encoding="utf-8") if calls.exists() else ""
        first = logged.splitlines()[0] if logged else ""
        check("a configured build signs", rc == 0 and "sign: ok" in out, f"rc={rc} {out.strip()}")
        check("the signature is verified after signing",
              first.startswith("sign /fd sha256") and "verify /pa" in logged,
              logged.replace("\n", " | "))
        check("the certificate and timestamp server reach signtool",
              "certificate.pfx" in logged and "/tr" in logged, logged.replace("\n", " | "))

        rc, out = run(NOTCH_SIGN_CERT=cert, NOTCH_SIGNTOOL=stub, STUB_FAIL_SIGN=1)
        check("a failed signature fails the build",
              rc == 1 and "signing failed" in out, f"rc={rc}")

        rc, out = run(NOTCH_SIGN_CERT=cert, NOTCH_SIGNTOOL=stub, STUB_FAIL_VERIFY=1)
        check("a signature that does not verify fails the build",
              rc == 1 and "does not verify" in out, f"rc={rc}")

    if failures:
        print(f"\n{len(failures)} signing check(s) failed")
        return 1
    print("\nAll signing checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
