"""Check exception capture in fresh processes without importing Qt."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as temp:
        env = dict(os.environ, PYTHONPATH=str(ROOT), TEST_LOG_DIR=temp)
        setup = '''
import os, sys
import cuvyro.paths
cuvyro.paths.app_dir = lambda: os.environ['TEST_LOG_DIR']
from cuvyro.diagnostics import install
sys.stdout = sys.stderr = None
install()
'''
        result = subprocess.run([sys.executable, '-c', setup +
            "print('windowed stdout works', flush=True)\nraise RuntimeError('test-main-failure')"], env=env)
        assert result.returncode != 0
        logs = '\n'.join(p.read_text() for p in Path(temp).glob('cuvyro-*.log'))
        assert 'windowed stdout works' in logs and 'test-main-failure' in logs
        subprocess.run([sys.executable, '-c', setup + '''
import threading
def fail():
    raise ValueError('test-thread-failure')
t = threading.Thread(target=fail)
t.start()
t.join()
'''], env=env, check=True)
        logs = '\n'.join(p.read_text() for p in Path(temp).glob('cuvyro-*.log'))
        assert 'Unhandled thread exception' in logs and 'test-thread-failure' in logs
        # An invalid preferred directory must fall back to local app data.
        bad = Path(temp) / 'not-a-directory'
        bad.write_text('fixture')
        env.update(TEST_LOG_DIR=str(bad), LOCALAPPDATA=temp)
        subprocess.run([sys.executable, '-c', setup + "print('fallback works')"], env=env, check=True)
        assert list((Path(temp) / 'Cuvyro' / 'logs').glob('cuvyro-*.log'))

        # A launch per day must not leave one log per day for ever. The newest
        # sessions are the support evidence; older ones go at startup, and the
        # running process never deletes its own files.
        retention = Path(temp) / 'retention'
        retention.mkdir()
        now = time.time()
        for index in range(8):
            pid = 90000 + index
            stamp = now - (8 - index) * 60
            for name in (f'cuvyro-{pid}.log', f'cuvyro-fault-{pid}.log'):
                path = retention / name
                path.write_text('old session', encoding='utf-8')
                os.utime(path, (stamp, stamp))
        env.update(TEST_LOG_DIR=str(retention), LOCALAPPDATA=temp)
        subprocess.run([sys.executable, '-c', setup + "print('retention works')"],
                       env=env, check=True)

        def pids(pattern):
            found = set()
            for path in retention.glob(pattern):
                stem = path.name.replace('cuvyro-fault-', '').replace('cuvyro-', '')
                found.add(int(stem.split('.')[0]))
            return found

        kept_logs = pids('cuvyro-*.log')
        kept_faults = pids('cuvyro-fault-*.log')
        assert len(kept_logs) == 6, f'expected 5 kept sessions plus this one: {kept_logs}'
        assert {90003, 90004, 90005, 90006, 90007} <= kept_logs, kept_logs
        assert not ({90000, 90001, 90002} & kept_logs), kept_logs
        assert not ({90000, 90001, 90002} & kept_faults), kept_faults
        assert (retention / f'cuvyro-{max(kept_logs)}.log').exists()
    print('PASS: windowed output, main/thread exceptions, unwritable-path fallback '
          'and bounded session retention')


if __name__ == '__main__':
    main()
