"""Repository snapshot pin verification for APP-005.

TEMPLATE_REVISION markers are never sufficient alone. The controlling git
repository must contain the pinned commit object, and materializable template
contents are hashed into a content snapshot digest bound into plans/manifests.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Optional

from bootstrap.errors import (
    ERR_TEMPLATE_MARKER_ONLY,
    ERR_TEMPLATE_REV,
    ERR_TEMPLATE_ROOT,
    ERR_TEMPLATE_SNAPSHOT,
    InitializerError,
)
from bootstrap.hashing import sha256_digest
from bootstrap.paths import iter_template_files, resolve_template_root

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_revision_marker(template_root: Path) -> Optional[str]:
    marker = template_root / "TEMPLATE_REVISION"
    if not marker.is_file() or marker.is_symlink():
        return None
    try:
        text = marker.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None
    if len(text) == 40 and all(c in "0123456789abcdef" for c in text):
        return text
    return None


def assert_git_commit_object(repo_root: Path, revision: str) -> str:
    """Fail closed unless revision is a real commit object in repo_root."""
    rev = (revision or "").strip().lower()
    if len(rev) != 40 or any(c not in "0123456789abcdef" for c in rev):
        raise InitializerError(ERR_TEMPLATE_REV)
    try:
        repo = repo_root.resolve(strict=True)
    except OSError:
        raise InitializerError(ERR_TEMPLATE_SNAPSHOT) from None
    git_dir = repo / ".git"
    if not git_dir.exists():
        # Linked worktree: .git may be a file.
        if not git_dir.is_file() and not git_dir.is_dir():
            raise InitializerError(ERR_TEMPLATE_SNAPSHOT)
    try:
        probed = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-t", rev],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise InitializerError(ERR_TEMPLATE_SNAPSHOT) from None
    if probed.returncode != 0 or probed.stdout.strip() != "commit":
        raise InitializerError(ERR_TEMPLATE_SNAPSHOT)
    try:
        resolved = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", f"{rev}^{{commit}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise InitializerError(ERR_TEMPLATE_SNAPSHOT) from None
    if resolved.returncode != 0:
        raise InitializerError(ERR_TEMPLATE_SNAPSHOT)
    got = resolved.stdout.strip().lower()
    if got != rev:
        raise InitializerError(ERR_TEMPLATE_REV)
    return rev


def content_snapshot_digest(template_root: Path) -> str:
    """Digest of materializable template bytes (exclusions already applied)."""
    root = resolve_template_root(template_root)
    digest = hashlib.sha256()
    for src in sorted(iter_template_files(root), key=lambda p: p.as_posix()):
        rel = src.relative_to(root).as_posix()
        try:
            data = src.read_bytes()
        except OSError:
            raise InitializerError(ERR_TEMPLATE_ROOT) from None
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return sha256_digest(digest.digest())


def verify_template_snapshot(
    template_root: Path,
    expected_revision: str,
    *,
    git_repo: Optional[Path] = None,
    require_marker: bool = True,
) -> str:
    """Verify pin via git commit object + optional marker; return content digest.

    Marker agreement is checked when present, but a matching marker without a
    verified git commit object is always rejected.
    """
    repo = git_repo or REPO_ROOT
    # Git commit object is mandatory — marker agreement is never enough alone.
    rev = assert_git_commit_object(repo, expected_revision)
    root = resolve_template_root(template_root)
    marker = _read_revision_marker(root)
    if require_marker and marker is None:
        raise InitializerError(ERR_TEMPLATE_ROOT)
    if marker is not None and marker != rev:
        raise InitializerError(ERR_TEMPLATE_REV)
    return content_snapshot_digest(root)


def reject_marker_only_claim(*, git_verified: bool) -> None:
    if not git_verified:
        raise InitializerError(ERR_TEMPLATE_MARKER_ONLY)
