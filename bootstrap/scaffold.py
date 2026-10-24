"""Deterministic local scaffold from an explicit pinned template tree."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from bootstrap.errors import (
    ERR_DRIFT_PLAN,
    ERR_GENERATED_EDIT,
    ERR_OUTPUT_ROOT,
    ERR_OVERWRITE,
    ERR_ROLLBACK_INCOMPLETE,
    ERR_SYMLINK,
    ERR_TEMPLATE_ROOT,
    InitializerError,
)
from bootstrap.hashing import sha256_digest
from bootstrap.paths import (
    assert_under_root,
    iter_template_files,
    refuse_user_overwrite,
    resolve_output_root,
    resolve_template_root,
    safe_join,
)
from bootstrap.safe_fs import (
    assert_no_symlink_ancestors,
    exclusive_create_file,
    fsync_dir,
    path_is_symlink,
)
from bootstrap.snapshot import REPO_ROOT, verify_template_snapshot_with_provenance

# Workflow sources are copied to a pending directory so GitHub never auto-enables them.
WORKFLOWS_PENDING = Path(".github") / "workflows.pending"

_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH


def verify_template_revision(
    template_root: Path,
    expected: str,
    *,
    git_repo: Path | None = None,
    require_git_content_match: bool = False,
) -> Tuple[str, str]:
    """Verify pin via git commit object (+ marker). Returns (content digest, provenance)."""
    return verify_template_snapshot_with_provenance(
        template_root,
        expected,
        git_repo=git_repo or REPO_ROOT,
        require_marker=True,
        require_git_content_match=require_git_content_match,
    )


def _dest_relative(src: Path, template_root: Path) -> str:
    rel = src.relative_to(template_root).as_posix()
    if rel.startswith(".github/workflows/"):
        suffix = rel[len(".github/workflows/") :]
        return f"{WORKFLOWS_PENDING.as_posix()}/{suffix}"
    return rel


def _file_digest(data: bytes) -> str:
    return sha256_digest(data)


def compute_scaffold_digest(
    *,
    template_revision: str,
    content_digest: str,
    files: Mapping[str, bytes],
) -> str:
    """Canonical scaffold digest over reviewed relative paths (sorted)."""
    digest = hashlib.sha256()
    digest.update(template_revision.encode("utf-8"))
    digest.update(content_digest.encode("utf-8"))
    for rel in sorted(files):
        data = files[rel]
        digest.update(rel.encode("utf-8"))
        digest.update(data)
    return sha256_digest(digest.digest())


def _assert_path_components_real(path: Path, *, stop_at: Path) -> None:
    cursor = path
    stop = stop_at
    while True:
        if path_is_symlink(cursor):
            raise InitializerError(ERR_SYMLINK)
        if cursor == stop:
            break
        if stop not in cursor.parents and cursor != stop:
            raise InitializerError(ERR_SYMLINK)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent


def verify_owned_output_tree(
    *,
    output_root: Path,
    owned_hashes: Mapping[str, str],
    template_revision: str,
    content_digest: str,
    expected_scaffold_digest: str,
) -> None:
    """Re-read generated files and require hashes + scaffold digest match review."""
    if not isinstance(expected_scaffold_digest, str) or not expected_scaffold_digest:
        raise InitializerError(ERR_DRIFT_PLAN)
    if not owned_hashes:
        raise InitializerError(ERR_GENERATED_EDIT)
    try:
        root = assert_no_symlink_ancestors(output_root)
    except InitializerError:
        raise
    if not root.is_dir():
        raise InitializerError(ERR_OUTPUT_ROOT)

    on_disk: Dict[str, bytes] = {}
    for rel, expected_hash in sorted(owned_hashes.items()):
        if not isinstance(rel, str) or not isinstance(expected_hash, str):
            raise InitializerError(ERR_GENERATED_EDIT)
        # Check the lexical leaf before resolve-based joins so symlink
        # replacements surface as ERR_SYMLINK rather than a containment miss.
        lexical_leaf = root.joinpath(*Path(rel).parts)
        if path_is_symlink(lexical_leaf):
            raise InitializerError(ERR_SYMLINK)
        dest = safe_join(root, rel)
        _assert_path_components_real(dest, stop_at=root)
        if path_is_symlink(dest):
            raise InitializerError(ERR_SYMLINK)
        if not dest.is_file():
            raise InitializerError(ERR_GENERATED_EDIT)
        try:
            data = dest.read_bytes()
        except OSError:
            raise InitializerError(ERR_GENERATED_EDIT) from None
        if _file_digest(data) != expected_hash:
            raise InitializerError(ERR_GENERATED_EDIT)
        on_disk[rel] = data

    actual = compute_scaffold_digest(
        template_revision=template_revision,
        content_digest=content_digest,
        files=on_disk,
    )
    if actual != expected_scaffold_digest:
        raise InitializerError(ERR_DRIFT_PLAN)


def _collect_intended_files(
    *,
    src_root: Path,
    config: Mapping[str, Any],
) -> Dict[str, bytes]:
    intended: Dict[str, bytes] = {}
    for src in sorted(iter_template_files(src_root), key=lambda p: p.as_posix()):
        rel = _dest_relative(src, src_root)
        try:
            data = src.read_bytes()
        except OSError:
            raise InitializerError(ERR_TEMPLATE_ROOT) from None
        intended[rel] = data

    app_config_rel = "app.config.json"
    intended[app_config_rel] = (
        json.dumps(dict(config), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    marker_rel = ".github/WORKFLOWS_DISABLED_UNTIL_REVIEW"
    intended[marker_rel] = (
        "Workflows from the pinned template were copied to "
        ".github/workflows.pending/ and are disabled until reviewed "
        "deployment integration (APP-006+). Do not auto-enable.\n"
    ).encode("utf-8")
    return intended


def _prevalidate_write(
    *,
    dst_root: Path,
    rel: str,
    data: bytes,
    owned: Set[str],
    hashes: Mapping[str, str],
) -> None:
    dest = safe_join(dst_root, rel)
    parent = dest.parent
    if parent.exists() or path_is_symlink(parent):
        _assert_path_components_real(parent, stop_at=dst_root)
    lexical_leaf = dst_root.joinpath(*Path(rel).parts)
    if path_is_symlink(lexical_leaf):
        raise InitializerError(ERR_SYMLINK)
    if dest.exists() or path_is_symlink(dest):
        if path_is_symlink(dest):
            raise InitializerError(ERR_SYMLINK)
        if rel in hashes:
            try:
                current = dest.read_bytes()
            except OSError:
                raise InitializerError(ERR_GENERATED_EDIT) from None
            if _file_digest(current) != hashes[rel]:
                raise InitializerError(ERR_GENERATED_EDIT)
        elif rel in owned:
            try:
                current = dest.read_bytes()
            except OSError:
                raise InitializerError(ERR_GENERATED_EDIT) from None
            if current != data:
                raise InitializerError(ERR_GENERATED_EDIT)
        else:
            refuse_user_overwrite(dest, owned_relative=owned, output_root=dst_root)


def _prevalidate_obsolete_removal(
    *,
    dst_root: Path,
    rel: str,
    hashes: Mapping[str, str],
) -> Optional[Path]:
    """Return dest to delete when unmodified owned output; None if already absent."""
    lexical_leaf = dst_root.joinpath(*Path(rel).parts)
    if path_is_symlink(lexical_leaf):
        raise InitializerError(ERR_SYMLINK)
    dest = safe_join(dst_root, rel)
    _assert_path_components_real(dest, stop_at=dst_root)
    if path_is_symlink(dest):
        raise InitializerError(ERR_SYMLINK)
    if not dest.exists():
        return None
    if not dest.is_file():
        raise InitializerError(ERR_GENERATED_EDIT)
    try:
        current = dest.read_bytes()
    except OSError:
        raise InitializerError(ERR_GENERATED_EDIT) from None
    expected = hashes.get(rel)
    if not isinstance(expected, str) or _file_digest(current) != expected:
        raise InitializerError(ERR_GENERATED_EDIT)
    return dest


def _plain_restore_bytes(dest: Path, previous: Optional[bytes]) -> bool:
    """Restore or remove without re-entering _atomic_write_bytes.

    Rollback must not depend on chmod/fsync that may have failed (or been
    fault-injected) on the forward path. Returns True only when the destination
    matches the pre-mutation intent.
    """
    try:
        if previous is None:
            if dest.exists() and not path_is_symlink(dest):
                dest.unlink()
            return not (dest.exists() and dest.is_file() and not path_is_symlink(dest))
        if path_is_symlink(dest):
            return False
        if not dest.parent.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(previous)
        return (
            dest.is_file()
            and not path_is_symlink(dest)
            and dest.read_bytes() == previous
        )
    except OSError:
        return False


def _cleanup_tmp(tmp_path: Path) -> None:
    try:
        if tmp_path.exists() and not path_is_symlink(tmp_path):
            tmp_path.unlink()
    except OSError:
        pass


def _atomic_write_bytes(dest: Path, data: bytes) -> None:
    if path_is_symlink(dest):
        raise InitializerError(ERR_SYMLINK)
    if path_is_symlink(dest.parent):
        raise InitializerError(ERR_SYMLINK)
    prior: Optional[bytes] = None
    had_prior = False
    if dest.exists() and not path_is_symlink(dest):
        try:
            prior = dest.read_bytes()
            had_prior = True
        except OSError:
            raise InitializerError(ERR_OUTPUT_ROOT) from None
    tmp_name = f".{dest.name}.{os.getpid()}.tmp"
    tmp_path = dest.parent / tmp_name
    if path_is_symlink(tmp_path) or tmp_path.exists():
        if path_is_symlink(tmp_path):
            raise InitializerError(ERR_SYMLINK)
        try:
            tmp_path.unlink()
        except OSError:
            raise InitializerError(ERR_OUTPUT_ROOT) from None
    fd: Optional[int] = None
    replaced = False
    try:
        fd = exclusive_create_file(tmp_path, mode=0o600)
        with os.fdopen(fd, "wb", closefd=True) as fh:
            fd = None
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if path_is_symlink(dest) or path_is_symlink(tmp_path):
            raise InitializerError(ERR_SYMLINK)
        os.replace(str(tmp_path), str(dest))
        replaced = True
        os.chmod(dest, _FILE_MODE)
        fsync_dir(dest.parent)
    except InitializerError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        _cleanup_tmp(tmp_path)
        if replaced and not _plain_restore_bytes(dest, prior if had_prior else None):
            raise InitializerError(ERR_ROLLBACK_INCOMPLETE) from None
        raise
    except OSError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        _cleanup_tmp(tmp_path)
        if replaced and not _plain_restore_bytes(dest, prior if had_prior else None):
            raise InitializerError(ERR_ROLLBACK_INCOMPLETE) from None
        raise InitializerError(ERR_OUTPUT_ROOT) from None


def _rollback_journal(journal: List[Tuple[str, Path, Optional[bytes]]]) -> None:
    incomplete = False
    for action, dest, previous in reversed(journal):
        if action == "write":
            if not _plain_restore_bytes(dest, previous):
                incomplete = True
        elif action == "delete":
            if previous is None:
                continue
            if not _plain_restore_bytes(dest, previous):
                incomplete = True
    if incomplete:
        raise InitializerError(ERR_ROLLBACK_INCOMPLETE)


def materialize_scaffold(
    *,
    template_root: Path,
    output_root: Path,
    config: Mapping[str, Any],
    template_revision: str,
    owned_relative: Set[str] | None = None,
    owned_hashes: Mapping[str, str] | None = None,
    git_repo: Path | None = None,
    require_git_content_match: bool = False,
) -> Tuple[int, str, Set[str], Dict[str, str], str, str]:
    """Copy a sanitized template into output_root.

    Returns
    (file_count, scaffold_digest, owned_paths, owned_hashes, content_digest, provenance).

    Validates the full destination set (including obsolete owned paths) before any
    mutation. Applies writes/removals with rollback so a mid-apply failure restores
    prior owned contents. Returned ownership covers only the new intended file set.
    """
    content_digest, provenance = verify_template_revision(
        template_root,
        template_revision,
        git_repo=git_repo,
        require_git_content_match=require_git_content_match,
    )
    src_root = resolve_template_root(template_root)
    dst_root = resolve_output_root(output_root)
    assert_no_symlink_ancestors(dst_root)
    prior_owned: Set[str] = set(owned_relative or ())
    prior_hashes: Dict[str, str] = dict(owned_hashes or {})
    if not prior_owned and prior_hashes:
        prior_owned = set(prior_hashes.keys())

    intended = _collect_intended_files(src_root=src_root, config=config)
    obsolete = sorted(set(prior_hashes) - set(intended))

    for rel, data in sorted(intended.items()):
        _prevalidate_write(
            dst_root=dst_root,
            rel=rel,
            data=data,
            owned=prior_owned,
            hashes=prior_hashes,
        )

    removals: List[Tuple[str, Path]] = []
    for rel in obsolete:
        dest = _prevalidate_obsolete_removal(
            dst_root=dst_root,
            rel=rel,
            hashes=prior_hashes,
        )
        if dest is not None:
            removals.append((rel, dest))

    live_workflows = dst_root / ".github" / "workflows"
    if live_workflows.exists():
        raise InitializerError(ERR_OVERWRITE)

    journal: List[Tuple[str, Path, Optional[bytes]]] = []
    try:
        for rel, data in sorted(intended.items()):
            dest = safe_join(dst_root, rel)
            if not dest.parent.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
            _assert_path_components_real(dest.parent, stop_at=dst_root)
            previous: Optional[bytes] = None
            if dest.exists() or path_is_symlink(dest):
                if path_is_symlink(dest):
                    raise InitializerError(ERR_SYMLINK)
                try:
                    previous = dest.read_bytes()
                except OSError:
                    raise InitializerError(ERR_GENERATED_EDIT) from None
            # Record intent before mutation so post-replace chmod/fsync failures
            # remain rollback-visible even if the helper cannot self-restore.
            journal.append(("write", dest, previous))
            _atomic_write_bytes(dest, data)

        for _rel, dest in removals:
            try:
                previous = dest.read_bytes()
            except OSError:
                raise InitializerError(ERR_GENERATED_EDIT) from None
            if path_is_symlink(dest):
                raise InitializerError(ERR_SYMLINK)
            journal.append(("delete", dest, previous))
            dest.unlink()
    except Exception as exc:
        try:
            _rollback_journal(journal)
        except InitializerError as rollback_exc:
            if rollback_exc.code == ERR_ROLLBACK_INCOMPLETE:
                raise rollback_exc from exc
            raise
        raise

    new_hashes = {rel: _file_digest(data) for rel, data in intended.items()}
    new_owned = set(new_hashes)
    scaffold_digest = compute_scaffold_digest(
        template_revision=template_revision,
        content_digest=content_digest,
        files=intended,
    )
    return (
        len(intended),
        scaffold_digest,
        new_owned,
        new_hashes,
        content_digest,
        provenance,
    )


def assert_under_output(path: Path, output_root: Path) -> Path:
    return assert_under_root(path, output_root)
