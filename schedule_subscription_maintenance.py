"""Run every five minutes using the site's Python; prevent overlapping workers."""
from pathlib import Path
from datetime import datetime, timezone
import errno
import os
import sys
import subprocess


def acquire_lock(path):
    """Return a held descriptor, or None if another worker holds the lock.

    Keep Windows local development working as well as the Linux VPS. Locks
    belong to the OS and are released on close or process exit.
    """
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'0')
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return None
        raise


def main():
    root = Path(__file__).resolve().parent
    reports = Path(os.getenv('CREDIT_REPORT_DIR', str(root / 'instance' / 'credit_reports')))
    reports.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = acquire_lock(reports / '.maintenance.lock')
    if lock_fd is None:
        print('Previous maintenance worker is still running; skipped overlapping invocation.', file=sys.stderr)
        return 0
    try:
        name = 'maintenance_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json'
        result = subprocess.run([sys.executable, '-m', 'flask', '--app', 'run', 'credits', 'maintain',
                                 '--output', str((reports/name).resolve())], cwd=root)
        return result.returncode
    finally:
        os.close(lock_fd)


if __name__ == '__main__':
    raise SystemExit(main())
