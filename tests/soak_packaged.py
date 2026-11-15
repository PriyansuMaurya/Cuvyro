"""Monitor a Windows packaged build for eight hours; never report a short pass."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('exe', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Run on Windows against the packaged Notch.exe')
    exe = args.exe.resolve(strict=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    # Cover the model and native libraries as well as the entry executable.
    manifest = {}
    for path in sorted(exe.parent.rglob('*')):
        if path.is_file() and not (path.name.startswith('notch-') and '.log' in path.name):
            with path.open('rb') as stream:
                manifest[str(path.relative_to(exe.parent))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = {'status': 'FAIL', 'required_seconds': 28800, 'files_sha256': manifest}
    process = None
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='notch-soak-') as work:
            env = dict(os.environ, APPDATA=work, NOTCH_SOAK_DIR=work)
            # A separate store avoids changing the user's script and preferences.
            process = subprocess.Popen([str(exe)], cwd=exe.parent, env=env)
            pulse = Path(work) / f'heartbeat-{process.pid}'
            while time.monotonic() - started < 28800:
                if process.poll() is not None:
                    raise RuntimeError(f'App exited early: {process.returncode}')
                elapsed = time.monotonic() - started
                if pulse.exists():
                    if time.time() - float(pulse.read_text()) > 60:
                        raise RuntimeError('GUI heartbeat stale for more than 60 seconds')
                elif elapsed > 120:
                    raise RuntimeError('No GUI heartbeat within 120 seconds')
                time.sleep(5)
            log_roots = [exe.parent, Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'NotchFree' / 'logs']
            found_log = False
            for root in log_roots:
                for log in root.glob(f'notch-{process.pid}.log*'):
                    found_log = True
                    content = log.read_text(encoding='utf-8', errors='replace')
                    if ' CRITICAL ' in content or ' ERROR ' in content:
                        raise RuntimeError(f'Exception/error evidence in {log}')
                fault = root / f'notch-fault-{process.pid}.log'
                if fault.exists() and fault.stat().st_size:
                    raise RuntimeError(f'Native fault evidence in {fault}')
            if not found_log:
                raise RuntimeError('Runtime diagnostic log missing')
            report['status'] = 'PASS'
            report['scope'] = 'Process survival and GUI responsiveness; manual functional review still required'
    except BaseException as exc:
        report['error'] = str(exc) or type(exc).__name__
    finally:
        report['elapsed_seconds'] = time.monotonic() - started
        if process is not None and process.poll() is None:
            # End the isolated test session. This is not a graceful-close test.
            process.terminate()
            process.wait(timeout=30)
        args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
