"""Reviewed-source materialization for the pinned template commit object.

Generated application repositories often have an independent Initial commit and
do not advertise upstream template history. Snapshot verification still requires
the exact PINNED_TEMPLATE_REVISION commit object. Materialize it only from an
allowlisted reviewed upstream identity via a bounded read-only fetch of that
exact hash. Never treat arbitrary remotes, markers, or rewritten pins as proof.

Trust boundary: only explicitly supported HTTPS and GitHub SCP-like SSH URL
shapes for the exact reviewed repository are accepted. Approved callers receive
a canonical fetch URL; raw/unvalidated source text is never passed to git.
Command-scoped ``protocol.*.allow`` limits direct transport families for that
fetch. This is not a sandbox for local Git configuration: ``url.*.insteadOf``,
credential helpers, and other trusted-environment config can still rewrite or
re-route the approved URL after it leaves this module.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from bootstrap.errors import (
    ERR_TEMPLATE_REV,
    ERR_TEMPLATE_SNAPSHOT,
    ERR_TEMPLATE_SOURCE_UNAVAILABLE,
    ERR_TEMPLATE_SOURCE_UNTRUSTED,
    InitializerError,
)
from bootstrap.snapshot import assert_git_commit_object

# Canonical reviewed upstream template repository (host/path, no .git suffix).
REVIEWED_TEMPLATE_REMOTE_IDENTITIES: tuple[str, ...] = (
    "github.com/agrana/free_personal_stack",
)

# Default HTTPS URL for CI / explicit reviewed-source fetches.
DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL = (
    "https://github.com/agrana/free_personal_stack.git"
)

# Canonical GitHub SCP-like SSH form for the same reviewed identity.
DEFAULT_REVIEWED_TEMPLATE_SOURCE_SSH = (
    "git@github.com:agrana/free_personal_stack.git"
)

_FULL_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_REMOTE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")

# Exact HTTPS shape: scheme + host + path; no userinfo, port, query, fragment.
_HTTPS_REVIEWED = re.compile(
    r"^https://github\.com/agrana/free_personal_stack(?:\.git)?$"
)
# Exact GitHub SCP-like SSH: git@host:path; no ssh://, ports, or extras.
_SSH_REVIEWED = re.compile(
    r"^git@github\.com:agrana/free_personal_stack(?:\.git)?$"
)

# Fetch transports we intentionally enable for the approved URL only.
_FETCH_PROTOCOL_CONFIG: tuple[str, ...] = (
    "-c",
    "protocol.allow=never",
    "-c",
    "protocol.https.allow=always",
    "-c",
    "protocol.ssh.allow=always",
    # file remains allowed so a trusted local `url.*.insteadOf` rewrite used by
    # offline fixtures can resolve the approved HTTPS/SSH URL. That is not a
    # sandbox: operators must treat invoking Git configuration as trusted.
    "-c",
    "protocol.file.allow=always",
)


def require_exact_commit_sha(revision: str) -> str:
    """Return lowercase 40-char SHA; reject options, partials, whitespace."""
    if not isinstance(revision, str) or _FULL_COMMIT_SHA.fullmatch(revision) is None:
        raise InitializerError(ERR_TEMPLATE_REV)
    return revision.lower()


def require_remote_name(remote_name: str) -> str:
    """Return a safe remote name; reject option-like or empty names."""
    if not isinstance(remote_name, str) or _REMOTE_NAME.fullmatch(remote_name) is None:
        raise InitializerError(ERR_TEMPLATE_SOURCE_UNTRUSTED)
    return remote_name


def approve_reviewed_template_source(url: str) -> str:
    """Return canonical fetch URL for an allowlisted reviewed source.

    Accepts only the documented HTTPS and GitHub SCP-like SSH forms of the
    exact reviewed repository. Rejects other transports, embedded credentials,
    ports, query/fragment, whitespace/control bytes, and protocol extensions.
    """
    if not isinstance(url, str) or not url or _CONTROL_OR_SPACE.search(url):
        raise InitializerError(ERR_TEMPLATE_SOURCE_UNTRUSTED)
    if _HTTPS_REVIEWED.fullmatch(url):
        return DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL
    if _SSH_REVIEWED.fullmatch(url):
        return DEFAULT_REVIEWED_TEMPLATE_SOURCE_SSH
    raise InitializerError(ERR_TEMPLATE_SOURCE_UNTRUSTED)


def normalize_git_remote_identity(url: str) -> str:
    """Return lowercase host/path identity for an approved reviewed source."""
    approved = approve_reviewed_template_source(url)
    if approved == DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL:
        return REVIEWED_TEMPLATE_REMOTE_IDENTITIES[0]
    if approved == DEFAULT_REVIEWED_TEMPLATE_SOURCE_SSH:
        return REVIEWED_TEMPLATE_REMOTE_IDENTITIES[0]
    raise InitializerError(ERR_TEMPLATE_SOURCE_UNTRUSTED)


def is_reviewed_template_remote(url: str) -> bool:
    try:
        approve_reviewed_template_source(url)
    except InitializerError:
        return False
    return True


def remote_url(repo_root: Path, remote_name: str) -> str:
    """Read a configured remote URL; fail closed if missing or unreadable."""
    name = require_remote_name(remote_name)
    try:
        repo = repo_root.resolve(strict=True)
    except OSError as exc:
        raise InitializerError(ERR_TEMPLATE_SOURCE_UNAVAILABLE) from exc
    try:
        probed = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InitializerError(ERR_TEMPLATE_SOURCE_UNAVAILABLE) from exc
    if probed.returncode != 0:
        raise InitializerError(ERR_TEMPLATE_SOURCE_UNAVAILABLE)
    url = probed.stdout.strip()
    if not url:
        raise InitializerError(ERR_TEMPLATE_SOURCE_UNAVAILABLE)
    return url


def ensure_pinned_commit_object(
    repo_root: Path,
    revision: str,
    *,
    reviewed_source_url: Optional[str] = None,
    remote_name: str = "template",
) -> str:
    """Ensure revision is a real commit object in repo_root; return the hash.

    Validates the revision as a full exact SHA and the remote name before any
    git invocation. If the object is already present, return it without
    fetching. Otherwise perform a bounded
    ``git fetch --depth 1 <canonical-reviewed-source> <revision>`` from an
    allowlisted reviewed URL (explicit or named remote). Does not rewrite
    PINNED_TEMPLATE_REVISION, merge unrelated ancestry, or trust non-allowlisted
    remotes.
    """
    rev = require_exact_commit_sha(revision)
    # Validate even when the object already exists and remote_name is unused.
    require_remote_name(remote_name)

    try:
        return assert_git_commit_object(repo_root, rev)
    except InitializerError as exc:
        if exc.code != ERR_TEMPLATE_SNAPSHOT:
            raise

    if reviewed_source_url is not None:
        source = approve_reviewed_template_source(reviewed_source_url)
    else:
        source = approve_reviewed_template_source(
            remote_url(repo_root, remote_name)
        )

    try:
        repo = repo_root.resolve(strict=True)
    except OSError as exc:
        raise InitializerError(ERR_TEMPLATE_SOURCE_UNAVAILABLE) from exc

    try:
        fetched = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                *_FETCH_PROTOCOL_CONFIG,
                "fetch",
                "--depth",
                "1",
                "--",
                source,
                rev,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InitializerError(ERR_TEMPLATE_SOURCE_UNAVAILABLE) from exc
    if fetched.returncode != 0:
        raise InitializerError(ERR_TEMPLATE_SOURCE_UNAVAILABLE)

    return assert_git_commit_object(repo_root, rev)
