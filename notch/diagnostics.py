"""Local startup, exception and native-fault diagnostics for windowed builds."""
import atexit
import faulthandler
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import threading
import time

_logger = None
_fault_file = None

# Every launch writes its own notch-<pid>.log, so a reader started daily
# accumulates one session per launch for ever unless the old ones go. The
# support evidence is the newest few; anything older is noise in the folder.
SESSION_LOG = re.compile(r'^notch-(\d+)\.log(?:\.\d+)?$')
FAULT_LOG = re.compile(r'^notch-fault-(\d+)\.log$')
KEEP_SESSIONS = 5


def _prune_sessions(directory):
    """Delete all but the newest KEEP_SESSIONS sessions, never this one."""
    current = os.getpid()
    sessions = {}
    for path in directory.iterdir():
        match = SESSION_LOG.match(path.name) or FAULT_LOG.match(path.name)
        if not match:
            continue
        pid = int(match.group(1))
        if pid == current:
            continue
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        sessions.setdefault(pid, []).append((stamp, path))
    newest = sorted(sessions.items(), key=lambda item: max(s for s, _ in item[1]),
                    reverse=True)[:KEEP_SESSIONS]
    keep = {pid for pid, _ in newest}
    for pid, files in sessions.items():
        if pid in keep:
            continue
        for _, path in files:
            try:
                path.unlink()
            except OSError:
                # A still-running process holds its own file open on
                # Windows; leaving one behind is better than failing.
                pass


class LogStream:
    encoding = "utf-8"

    def __init__(self, logger, level):
        self.logger, self.level = logger, level

    def write(self, text):
        if text.strip():
            self.logger.log(self.level, text.rstrip())
        return len(text)

    def flush(self):
        for handler in self.logger.handlers:
            handler.flush()

    def isatty(self):
        return False


def install():
    """Install before Qt imports; prefer files beside the executable."""
    global _logger, _fault_file
    if _logger is not None:
        return _logger
    from notch.paths import app_dir
    candidates = [Path(app_dir()), Path(os.environ.get('LOCALAPPDATA') or
                  Path.home() / '.local' / 'state') / 'NotchFree' / 'logs']
    handler = None
    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(directory / f'notch-{os.getpid()}.log',
                                          maxBytes=2 * 1024 * 1024,
                                          backupCount=2, encoding='utf-8')
            break
        except OSError:
            continue
    logger = logging.getLogger('notch.runtime')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler or logging.NullHandler())
    if handler:
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        try:
            _fault_file = open(directory / f'notch-fault-{os.getpid()}.log', 'a')
            faulthandler.enable(file=_fault_file, all_threads=True)
        except (OSError, RuntimeError):
            logger.warning('Native fault logging unavailable')
        _prune_sessions(directory)
    _logger = logger
    original_hook = sys.excepthook

    def exception_hook(kind, value, tb):
        logger.critical('Unhandled exception', exc_info=(kind, value, tb))
        original_hook(kind, value, tb)

    sys.excepthook = exception_hook
    old_thread_hook = threading.excepthook

    def thread_hook(args):
        logger.critical('Unhandled thread exception',
                        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        old_thread_hook(args)

    threading.excepthook = thread_hook
    # A windowed PyInstaller build has no stdout/stderr. Keep source-console
    # behaviour intact, including explicitly opted-in transcript traces.
    if sys.stdout is None:
        sys.stdout = LogStream(logger, logging.INFO)
    if sys.stderr is None:
        sys.stderr = LogStream(logger, logging.ERROR)
    logger.info('Process started pid=%s frozen=%s python=%s', os.getpid(),
                bool(getattr(sys, 'frozen', False)), sys.version.split()[0])
    atexit.register(lambda: logger.info('Python interpreter exiting'))
    return logger


def heartbeat():
    """Opt-in GUI event-loop evidence, with no script or audio content."""
    directory = os.environ.get('NOTCH_SOAK_DIR')
    if directory:
        try:
            path = Path(directory) / f'heartbeat-{os.getpid()}'
            temp = path.with_suffix('.tmp')
            temp.write_text(str(time.time()), encoding='ascii')
            temp.replace(path)
        except OSError:
            if _logger:
                _logger.exception('Cannot write soak heartbeat')
