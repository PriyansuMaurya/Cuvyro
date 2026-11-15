"""Check exception capture in fresh processes without importing Qt."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as temp:
        env = dict(os.environ, PYTHONPATH=str(ROOT), TEST_LOG_DIR=temp)
        setup = '''
import os, sys
import notch.paths
notch.paths.app_dir = lambda: os.environ['TEST_LOG_DIR']
from notch.diagnostics import install
sys.stdout = sys.stderr = None
install()
'''
        result = subprocess.run([sys.executable, '-c', setup +
            "print('windowed stdout works', flush=True)\nraise RuntimeError('test-main-failure')"], env=env)
        assert result.returncode != 0
        logs = '\n'.join(p.read_text() for p in Path(temp).glob('notch-*.log'))
        assert 'windowed stdout works' in logs and 'test-main-failure' in logs
        subprocess.run([sys.executable, '-c', setup + '''
import threading
def fail():
    raise ValueError('test-thread-failure')
t = threading.Thread(target=fail)
t.start()
t.join()
'''], env=env, check=True)
        logs = '\n'.join(p.read_text() for p in Path(temp).glob('notch-*.log'))
        assert 'Unhandled thread exception' in logs and 'test-thread-failure' in logs
        # An invalid preferred directory must fall back to local app data.
        bad = Path(temp) / 'not-a-directory'
        bad.write_text('fixture')
        env.update(TEST_LOG_DIR=str(bad), LOCALAPPDATA=temp)
        subprocess.run([sys.executable, '-c', setup + "print('fallback works')"], env=env, check=True)
        assert list((Path(temp) / 'NotchFree' / 'logs').glob('notch-*.log'))
    print('PASS: windowed output, main/thread exceptions and unwritable-path fallback')


if __name__ == '__main__':
    main()
