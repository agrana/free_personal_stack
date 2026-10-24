"""Repository snapshot pin verification for APP-005.

TEMPLATE_REVISION markers are never sufficient alone. The controlling git
repository must contain the pinned commit object, and materializable template
contents are hashed into a content snapshot digest bound into plans/manifests.

Dirty-content policy:
- Declared revision never authorizes arbitrary on-disk bytes.
- When a materializable relative path exists in the pinned commit, on-disk bytes
  must match that blob or verification fails with template.content_dirty.
- Extra materializable paths absent from the pin are rejected when any path was
  matched from the pin (mixed provenance).
- Offline fixture trees with zero paths present in the pin are allowed only as
  content-digest-bound sources (provenance offline_bound); set
  require_git_content_match=True to refuse that mode.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from bootstrap.errors import (
    ERR_TEMPLATE_DIRTY,
    ERR_TEMPLATE_MARKER_ONLY,
    ERR_TEMPLATE_REV,
    ERR_TEMPLATE_ROOT,
    ERR_TEMPLATE_SNAPSHOT,
    InitializerError,
)
from bootstrap.hashing import sha256_digest
from bootstrap.paths import iter_template_files, resolve_template_root

REPO_ROOT = Path(__file__).resolve().parents[1]

PROVENANCE_GIT = "git_tree_verified"
PROVENANCE_OFFLINE = "offline_bound"


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


def _git_blob_bytes(repo: Path, revision: str, rel_posix: str) -> Optional[bytes]:
    try:
        probed = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{revision}:{rel_posix}"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise InitializerError(ERR_TEMPLATE_SNAPSHOT) from None
    if probed.returncode != 0:
        return None
    try:
        shown = subprocess.run(
            ["git", "-C", str(repo), "show", f"{revision}:{rel_posix}"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise InitializerError(ERR_TEMPLATE_SNAPSHOT) from None
    if shown.returncode != 0:
        return None
    return shown.stdout


def assert_content_matches_revision(
    template_root: Path,
    revision: str,
    *,
    git_repo: Path,
    require_git_content_match: bool = False,
) -> str:
    """Compare materializable bytes to pinned blobs; return provenance kind."""
    root = resolve_template_root(template_root)
    repo = git_repo
    tracked_hits = 0
    untracked = 0
    for src in iter_template_files(root):
        rel = src.relative_to(root).as_posix()
        if rel == "TEMPLATE_REVISION":
            continue
        try:
            data = src.read_bytes()
        except OSError:
            raise InitializerError(ERR_TEMPLATE_ROOT) from None
        blob = _git_blob_bytes(repo, revision, rel)
        if blob is None:
            untracked += 1
            continue
        tracked_hits += 1
        if blob != data:
            raise InitializerError(ERR_TEMPLATE_DIRTY)
    if tracked_hits > 0 and untracked > 0:
        raise InitializerError(ERR_TEMPLATE_DIRTY)
    if tracked_hits == 0:
        if require_git_content_match:
            raise InitializerError(ERR_TEMPLATE_DIRTY)
        return PROVENANCE_OFFLINE
    return PROVENANCE_GIT


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


def build_content_manifest(template_root: Path) -> dict:
    """Immutable selected-source manifest of materializable relative paths."""
    root = resolve_template_root(template_root)
    entries = []
    for src in sorted(iter_template_files(root), key=lambda p: p.as_posix()):
        rel = src.relative_to(root).as_posix()
        try:
            data = src.read_bytes()
        except OSError:
            raise InitializerError(ERR_TEMPLATE_ROOT) from None
        entries.append({"path": rel, "sha256": sha256_digest(data)})
    return {
        "files": entries,
        "contentSnapshotDigest": content_snapshot_digest(root),
    }


def verify_template_snapshot(
    template_root: Path,
    expected_revision: str,
    *,
    git_repo: Optional[Path] = None,
    require_marker: bool = True,
    require_git_content_match: bool = False,
) -> str:
    """Verify pin via git commit object + optional marker; return content digest.

    Marker agreement is checked when present, but a matching marker without a
    verified git commit object is always rejected.
    """
    digest, _provenance = verify_template_snapshot_with_provenance(
        template_root,
        expected_revision,
        git_repo=git_repo,
        require_marker=require_marker,
        require_git_content_match=require_git_content_match,
    )
    return digest


def verify_template_snapshot_with_provenance(
    template_root: Path,
    expected_revision: str,
    *,
    git_repo: Optional[Path] = None,
    require_marker: bool = True,
    require_git_content_match: bool = False,
) -> Tuple[str, str]:
    repo = git_repo or REPO_ROOT
    rev = assert_git_commit_object(repo, expected_revision)
    root = resolve_template_root(template_root)
    marker = _read_revision_marker(root)
    if require_marker and marker is None:
        raise InitializerError(ERR_TEMPLATE_ROOT)
    if marker is not None and marker != rev:
        raise InitializerError(ERR_TEMPLATE_REV)
    provenance = assert_content_matches_revision(
        root,
        rev,
        git_repo=repo,
        require_git_content_match=require_git_content_match,
    )
    return content_snapshot_digest(root), provenance


def reject_marker_only_claim(*, git_verified: bool) -> None:
    if not git_verified:
        raise InitializerError(ERR_TEMPLATE_MARKER_ONLY)
