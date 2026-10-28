"""OS-released advisory locks for a session directory and cache writers."""

from contextlib import contextmanager
from pathlib import Path
import os
import time
from ..domain.errors import CASSoundError


class FileLock:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def acquire(self, timeout=0):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        start = time.monotonic()
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.file = handle
                return self
            except OSError:
                if time.monotonic() - start >= timeout:
                    handle.close()
                    raise CASSoundError(
                        "Directory is in use by another session or cache writer.", code="directory_busy"
                    )
                time.sleep(0.05)

    def release(self):
        if self.file:
            self.file.close()
            self.file = None


@contextmanager
def cache_write_lock(root):
    lock = FileLock(Path(root) / "cache-write.lock").acquire(timeout=600)
    try:
        yield
    finally:
        lock.release()
