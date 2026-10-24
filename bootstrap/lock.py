"""Exclusive per-app lock for durable apply/resume."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from bootstrap.errors import ERR_CONCURRENT, ERR_LOCK, ERR_LOCK_IO, ERR_SYMLINK, InitializerError
from bootstrap.safe_fs import (
    ensure_owned_directory,
    open_existing_nofollow_rw,
    open_nofollow,
    path_is_symlink,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore


class AppLock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fh: Optional[object] = None
        self._fd: Optional[int] = None

    def acquire(self, *, blocking: bool = False) -> None:
        if fcntl is None:
            raise InitializerError(ERR_LOCK_IO)
        if path_is_symlink(self.lock_path):
            raise InitializerError(ERR_SYMLINK)
        try:
            ensure_owned_directory(self.lock_path.parent)
            if self.lock_path.exists() or path_is_symlink(self.lock_path):
                if path_is_symlink(self.lock_path):
                    raise InitializerError(ERR_SYMLINK)
                fd = open_existing_nofollow_rw(self.lock_path)
            else:
                flags = os.O_RDWR | os.O_CREAT
                fd = open_nofollow(self.lock_path, flags=flags, mode=0o600)
                # Re-check after create in case of race to a symlink replace.
                if path_is_symlink(self.lock_path):
                    os.close(fd)
                    raise InitializerError(ERR_SYMLINK)
        except InitializerError:
            raise
        except OSError:
            raise InitializerError(ERR_LOCK_IO) from None
        fh = os.fdopen(fd, "r+", encoding="utf-8", closefd=True)
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
        self._fd = fh.fileno()
        try:
            if path_is_symlink(self.lock_path):
                raise InitializerError(ERR_SYMLINK)
            fh.seek(0)
            fh.truncate()
            fh.write(f"pid={os.getpid()}\n")
            fh.flush()
            os.fsync(fh.fileno())
        except InitializerError:
            self.release()
            raise
        except OSError:
            self.release()
            raise InitializerError(ERR_LOCK_IO) from None

    def release(self) -> None:
        fh = self._fh
        self._fh = None
        self._fd = None
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
