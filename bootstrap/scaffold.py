"""Deterministic local scaffold from an explicit pinned template tree."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, Mapping, Set, Tuple

from bootstrap.errors import (
    ERR_DRIFT_PLAN,
    ERR_GENERATED_EDIT,
    ERR_OUTPUT_ROOT,
    ERR_OVERWRITE,
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
from bootstrap.safe_fs import assert_no_symlink_ancestors, path_is_symlink
from bootstrap.snapshot import REPO_ROOT, verify_template_snapshot_with_provenance

# Workflow sources are copied to a pending directory so GitHub never auto-enables them.
WORKFLOWS_PENDING = Path(".github") / "workflows.pending"


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
    owned: Set[str] = set(owned_relative or ())
    hashes: Dict[str, str] = dict(owned_hashes or {})
    if not owned and hashes:
        owned = set(hashes.keys())

    written: Dict[str, bytes] = {}

    def _write_owned(rel: str, data: bytes) -> None:
        dest = safe_join(dst_root, rel)
        if not dest.parent.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
        _assert_path_components_real(dest.parent, stop_at=dst_root)

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

        dest.write_bytes(data)
        mode = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH
        os.chmod(dest, mode)
        owned.add(rel)
        hashes[rel] = _file_digest(data)
        written[rel] = data

    for src in sorted(iter_template_files(src_root), key=lambda p: p.as_posix()):
        rel = _dest_relative(src, src_root)
        try:
            data = src.read_bytes()
        except OSError:
            raise InitializerError(ERR_TEMPLATE_ROOT) from None
        _write_owned(rel, data)

    app_config_rel = "app.config.json"
    payload = (json.dumps(dict(config), indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_owned(app_config_rel, payload)

    marker_rel = ".github/WORKFLOWS_DISABLED_UNTIL_REVIEW"
    marker_body = (
        "Workflows from the pinned template were copied to "
        ".github/workflows.pending/ and are disabled until reviewed "
        "deployment integration (APP-006+). Do not auto-enable.\n"
    ).encode("utf-8")
    marker_parent = safe_join(dst_root, ".github")
    marker_parent.mkdir(parents=True, exist_ok=True)
    _write_owned(marker_rel, marker_body)

    live_workflows = dst_root / ".github" / "workflows"
    if live_workflows.exists():
        raise InitializerError(ERR_OVERWRITE)

    scaffold_digest = compute_scaffold_digest(
        template_revision=template_revision,
        content_digest=content_digest,
        files=written,
    )
    return (
        len(written),
        scaffold_digest,
        owned,
        hashes,
        content_digest,
        provenance,
    )


def assert_under_output(path: Path, output_root: Path) -> Path:
    return assert_under_root(path, output_root)
