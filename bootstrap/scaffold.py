"""Deterministic local scaffold from an explicit pinned template tree."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping, Set, Tuple

from bootstrap.errors import (
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
from bootstrap.snapshot import REPO_ROOT, verify_template_snapshot

# Workflow sources are copied to a pending directory so GitHub never auto-enables them.
WORKFLOWS_SRC = Path(".github") / "workflows"
WORKFLOWS_PENDING = Path(".github") / "workflows.pending"


def verify_template_revision(
    template_root: Path,
    expected: str,
    *,
    git_repo: Path | None = None,
) -> str:
    """Verify pin via git commit object (+ marker). Returns content snapshot digest."""
    return verify_template_snapshot(
        template_root,
        expected,
        git_repo=git_repo or REPO_ROOT,
        require_marker=True,
    )


def _dest_relative(src: Path, template_root: Path) -> str:
    rel = src.relative_to(template_root).as_posix()
    if rel.startswith(".github/workflows/"):
        suffix = rel[len(".github/workflows/") :]
        return f"{WORKFLOWS_PENDING.as_posix()}/{suffix}"
    return rel


def materialize_scaffold(
    *,
    template_root: Path,
    output_root: Path,
    config: Mapping[str, Any],
    template_revision: str,
    owned_relative: Set[str] | None = None,
    git_repo: Path | None = None,
) -> Tuple[int, str, Set[str]]:
    """Copy a sanitized template into output_root. Returns (file_count, digest, owned)."""
    content_digest = verify_template_revision(
        template_root, template_revision, git_repo=git_repo
    )
    src_root = resolve_template_root(template_root)
    dst_root = resolve_output_root(output_root)
    owned: Set[str] = set(owned_relative or ())

    digest = hashlib.sha256()
    digest.update(template_revision.encode("utf-8"))
    digest.update(content_digest.encode("utf-8"))
    file_count = 0

    for src in sorted(iter_template_files(src_root), key=lambda p: p.as_posix()):
        rel = _dest_relative(src, src_root)
        dest = safe_join(dst_root, rel)
        if not dest.parent.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
        probe = dest.parent
        while True:
            if probe.is_symlink():
                raise InitializerError(ERR_SYMLINK)
            if probe == dst_root:
                break
            if dst_root not in probe.parents and probe != dst_root:
                raise InitializerError(ERR_SYMLINK)
            probe = probe.parent

        refuse_user_overwrite(dest, owned_relative=owned, output_root=dst_root)

        try:
            data = src.read_bytes()
        except OSError:
            raise InitializerError(ERR_TEMPLATE_ROOT) from None

        # Strip execute bits from copied content (no template exec hooks).
        dest.write_bytes(data)
        mode = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH
        os.chmod(dest, mode)

        owned.add(rel)
        file_count += 1
        digest.update(rel.encode("utf-8"))
        digest.update(data)

    # Write validated non-secret app.config into the new tree.
    app_config_rel = "app.config.json"
    app_config_path = safe_join(dst_root, app_config_rel)
    refuse_user_overwrite(app_config_path, owned_relative=owned, output_root=dst_root)
    payload = (json.dumps(dict(config), indent=2, sort_keys=True) + "\n").encode("utf-8")
    app_config_path.write_bytes(payload)
    os.chmod(app_config_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    owned.add(app_config_rel)
    file_count += 1
    digest.update(app_config_rel.encode("utf-8"))
    digest.update(payload)

    # Marker documenting disabled workflows.
    marker_rel = ".github/WORKFLOWS_DISABLED_UNTIL_REVIEW"
    marker_path = safe_join(dst_root, marker_rel)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    refuse_user_overwrite(marker_path, owned_relative=owned, output_root=dst_root)
    marker_body = (
        "Workflows from the pinned template were copied to "
        ".github/workflows.pending/ and are disabled until reviewed "
        "deployment integration (APP-006+). Do not auto-enable.\n"
    ).encode("utf-8")
    marker_path.write_bytes(marker_body)
    owned.add(marker_rel)
    file_count += 1
    digest.update(marker_rel.encode("utf-8"))
    digest.update(marker_body)

    # Ensure no live .github/workflows were created from template copy.
    live_workflows = dst_root / ".github" / "workflows"
    if live_workflows.exists():
        raise InitializerError(ERR_OVERWRITE)

    return file_count, sha256_digest(digest.digest()), owned


def assert_under_output(path: Path, output_root: Path) -> Path:
    return assert_under_root(path, output_root)
