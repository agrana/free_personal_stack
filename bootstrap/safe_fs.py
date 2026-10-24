"""Symlink-resistant filesystem helpers for state, lock, and temp paths.

Boundary (best-effort POSIX, not a hostile concurrent sandbox):
- Lexical ancestor components are checked with lstat/is_symlink before create.
- Final opens prefer O_NOFOLLOW / O_EXCL where the OS provides them.
- This does not close all TOCTOU races against a privileged concurrent attacker,
  hostile mount namespaces, or non-POSIX platforms without O_NOFOLLOW.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional

from bootstrap.errors import ERR_LOCK_IO, ERR_PATH_ESCAPE, ERR_SYMLINK, InitializerError

_NOFOLLOW_OPEN = getattr(os, "O_NOFOLLOW", 0)


def path_is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        raise InitializerError(ERR_PATH_ESCAPE) from None


def assert_no_symlink(path: Path) -> Path:
    if path_is_symlink(path):
        raise InitializerError(ERR_SYMLINK)
    return path


def lexical_absolute(path: Path) -> Path:
    """Absolute path without resolving symlinks (unlike Path.resolve)."""
    try:
        expanded = path.expanduser()
    except OSError:
        raise InitializerError(ERR_PATH_ESCAPE) from None
    try:
        return Path(os.path.abspath(str(expanded)))
    except OSError:
        raise InitializerError(ERR_PATH_ESCAPE) from None


def assert_no_symlink_ancestors(path: Path) -> Path:
    """Refuse any symlink in the lexical ancestor chain (including the leaf)."""
    candidate = lexical_absolute(path)
    cursor = Path(candidate.anchor)
    if cursor.exists() or path_is_symlink(cursor):
        assert_no_symlink(cursor)
    for part in candidate.parts[1:]:
        cursor = cursor / part
        if path_is_symlink(cursor):
            raise InitializerError(ERR_SYMLINK)
        if not cursor.exists():
            break
    return candidate


def assert_lexical_under_root(path: Path, root: Path) -> Path:
    """Containment check that still refuses symlink leaves on the lexical walk.

    Resolved paths are used only for the containment predicate after each
    existing lexical component under root has been checked as a non-symlink.
    Symlinks are never accepted as proof that a path was checked.
    """
    candidate = assert_no_symlink_ancestors(path)
    root_lex = assert_no_symlink_ancestors(root)
    try:
        candidate.relative_to(root_lex)
    except ValueError:
        raise InitializerError(ERR_PATH_ESCAPE) from None
    try:
        resolved = candidate.resolve(strict=False)
        root_resolved = root_lex.resolve(strict=False)
    except OSError:
        raise InitializerError(ERR_PATH_ESCAPE) from None
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise InitializerError(ERR_PATH_ESCAPE) from None
    return candidate


def ensure_owned_directory(path: Path, *, root: Optional[Path] = None) -> Path:
    """Create a real directory after validating every lexical ancestor.

    Every existing path component from the filesystem root down is checked with
    is_symlink before any mkdir. Intermediate symlinks (including nested
    ancestors where a deeper real directory already exists behind a link) are
    rejected — this does not resolve the symlink away and then claim success.
    """
    candidate = lexical_absolute(path)
    cursor = Path(candidate.anchor)
    if path_is_symlink(cursor):
        raise InitializerError(ERR_SYMLINK)

    missing_from: Optional[int] = None
    components = candidate.parts[1:]
    for index, part in enumerate(components):
        cursor = cursor / part
        if path_is_symlink(cursor):
            raise InitializerError(ERR_SYMLINK)
        if not cursor.exists():
            missing_from = index
            break
        if not cursor.is_dir():
            raise InitializerError(ERR_PATH_ESCAPE)

    if missing_from is None:
        if path_is_symlink(candidate):
            raise InitializerError(ERR_SYMLINK)
        if not candidate.is_dir():
            raise InitializerError(ERR_PATH_ESCAPE)
    else:
        cursor = Path(candidate.anchor)
        for index, part in enumerate(components):
            cursor = cursor / part
            if index < missing_from:
                continue
            if path_is_symlink(cursor):
                raise InitializerError(ERR_SYMLINK)
            if cursor.exists():
                if path_is_symlink(cursor):
                    raise InitializerError(ERR_SYMLINK)
                if not cursor.is_dir():
                    raise InitializerError(ERR_PATH_ESCAPE)
                continue
            try:
                cursor.mkdir(mode=0o700, exist_ok=False)
            except FileExistsError:
                if path_is_symlink(cursor):
                    raise InitializerError(ERR_SYMLINK)
                if not cursor.is_dir():
                    raise InitializerError(ERR_PATH_ESCAPE)
            except OSError:
                raise InitializerError(ERR_PATH_ESCAPE) from None
            if path_is_symlink(cursor):
                raise InitializerError(ERR_SYMLINK)

    if root is not None:
        assert_lexical_under_root(candidate, root)
    return candidate


def open_nofollow(
    path: Path,
    *,
    flags: int,
    mode: int = 0o600,
) -> int:
    """Open a path without following the final symlink component."""
    if path_is_symlink(path):
        raise InitializerError(ERR_SYMLINK)
    open_flags = flags | _NOFOLLOW_OPEN
    try:
        return os.open(str(path), open_flags, mode)
    except OSError as exc:
        # Race: path became a symlink between check and open.
        if path_is_symlink(path):
            raise InitializerError(ERR_SYMLINK) from None
        if getattr(exc, "errno", None) in {getattr(os, "ELOOP", 40)}:
            raise InitializerError(ERR_SYMLINK) from None
        raise InitializerError(ERR_LOCK_IO) from None


def exclusive_create_file(path: Path, *, mode: int = 0o600) -> int:
    """Create a new regular file exclusively without following symlinks."""
    if path.exists() or path_is_symlink(path):
        raise InitializerError(ERR_SYMLINK if path_is_symlink(path) else ERR_PATH_ESCAPE)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_TRUNC
    return open_nofollow(path, flags=flags, mode=mode)


def open_existing_nofollow_rw(path: Path) -> int:
    if path_is_symlink(path):
        raise InitializerError(ERR_SYMLINK)
    if not path.exists():
        raise InitializerError(ERR_LOCK_IO)
    try:
        st = os.lstat(path)
    except OSError:
        raise InitializerError(ERR_LOCK_IO) from None
    if stat.S_ISLNK(st.st_mode):
        raise InitializerError(ERR_SYMLINK)
    if not stat.S_ISREG(st.st_mode):
        raise InitializerError(ERR_LOCK_IO)
    return open_nofollow(path, flags=os.O_RDWR)


def fsync_dir(path: Path) -> None:
    try:
        dir_fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
