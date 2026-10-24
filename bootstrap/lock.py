"""Exclusive per-app lock for durable apply/resume."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from bootstrap.errors import ERR_CONCURRENT, ERR_LOCK, ERR_LOCK_IO, InitializerError

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore


class AppLock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fh: Optional[object] = None

    def acquire(self, *, blocking: bool = False) -> None:
        if fcntl is None:
            raise InitializerError(ERR_LOCK_IO)
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self.lock_path, "a+", encoding="utf-8")
        except OSError:
            raise InitializerError(ERR_LOCK_IO) from None
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(fh.fileno(), flags)
        except BlockingIOError:
            fh.close()
            raise InitializerError(ERR_CONCURRENT) from None
        except OSError:
            fh.close()
            raise InitializerError(ERR_LOCK) from None
        self._fh = fh
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(f"pid={os.getpid()}\n")
            fh.flush()
        except OSError:
            self.release()
            raise InitializerError(ERR_LOCK_IO) from None

    def release(self) -> None:
        fh = self._fh
        self._fh = None
        if fh is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass


@contextmanager
def exclusive_app_lock(lock_path: Path, *, blocking: bool = False) -> Iterator[AppLock]:
    lock = AppLock(lock_path)
    lock.acquire(blocking=blocking)
    try:
        yield lock
    finally:
        lock.release()


def wait_briefly_for_tests(seconds: float = 0.05) -> None:
    time.sleep(seconds)
