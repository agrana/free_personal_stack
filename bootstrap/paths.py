"""Path safety: no symlink escape, no traversal, no unexpected overwrite."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Set

from bootstrap.errors import (
    ERR_OVERWRITE,
    ERR_PATH_ESCAPE,
    ERR_SYMLINK,
    ERR_TEMPLATE_ROOT,
    ERR_OUTPUT_ROOT,
    InitializerError,
)

# Paths relative to template root that must never be copied into a new app tree.
EXCLUDED_RELATIVE_PREFIXES = (
    ".git/",
    ".venv/",
    "node_modules/",
    "supabase/.temp/",
    "state/",
    "temp/",
    ".temp/",
    "__pycache__/",
    ".cursor/",
    ".husky/",
)

EXCLUDED_RELATIVE_EXACT = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        ".env",
        ".env.local",
        ".env.development.local",
        ".env.test.local",
        ".env.production.local",
        "platforms.json",
        "template.config.json",
        "supabase/.temp",
        "state",
        "temp",
        ".temp",
        "package-lock.json",
        "tsconfig.tsbuildinfo",
    }
)

EXCLUDED_NAME_PREFIXES = (".env",)


def _is_excluded_relative(rel_posix: str) -> bool:
    if rel_posix in EXCLUDED_RELATIVE_EXACT:
        return True
    name = rel_posix.split("/")[-1]
    if any(name.startswith(prefix) for prefix in EXCLUDED_NAME_PREFIXES):
        return True
    for prefix in EXCLUDED_RELATIVE_PREFIXES:
        if rel_posix == prefix.rstrip("/") or rel_posix.startswith(prefix):
            return True
    return False


def resolve_tree_root(path: Path, *, label_code: str) -> Path:
    """Resolve a directory root; reject missing paths and symlink roots."""
    if not isinstance(path, Path):
        raise InitializerError(label_code)
    try:
        candidate = path.expanduser()
    except OSError as exc:
        raise InitializerError(label_code) from None
    if candidate.exists() and candidate.is_symlink():
        raise InitializerError(ERR_SYMLINK)
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        raise InitializerError(label_code) from None
    return resolved


def ensure_directory(path: Path, *, label_code: str) -> Path:
    root = resolve_tree_root(path, label_code=label_code)
    if root.exists() and not root.is_dir():
        raise InitializerError(label_code)
    if not root.exists():
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise InitializerError(label_code) from None
    if root.is_symlink():
        raise InitializerError(ERR_SYMLINK)
    return root


def assert_under_root(path: Path, root: Path) -> Path:
    try:
        resolved = path.resolve(strict=False)
        root_resolved = root.resolve(strict=False)
    except OSError:
        raise InitializerError(ERR_PATH_ESCAPE) from None
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise InitializerError(ERR_PATH_ESCAPE) from None
    return resolved


def safe_join(root: Path, *parts: str) -> Path:
    if any(
        part in ("", ".", "..")
        or "/" in part
        or "\\" in part
        or part.startswith("~")
        for part in parts
    ):
        # Allow nested relative via explicit split parts only when safe tokens.
        cleaned: list[str] = []
        for part in parts:
            for token in Path(part).parts:
                if token in ("", ".", "..") or token.startswith("~"):
                    raise InitializerError(ERR_PATH_ESCAPE)
                cleaned.append(token)
        parts = tuple(cleaned)
    candidate = root.joinpath(*parts)
    return assert_under_root(candidate, root)


def iter_template_files(template_root: Path) -> Iterable[Path]:
    root = resolve_tree_root(template_root, label_code=ERR_TEMPLATE_ROOT)
    if not root.is_dir():
        raise InitializerError(ERR_TEMPLATE_ROOT)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        # Prune excluded / symlink dirs in-place.
        keep: list[str] = []
        for name in list(dirnames):
            child = current / name
            if child.is_symlink():
                continue
            rel = child.relative_to(root).as_posix()
            if _is_excluded_relative(rel) or _is_excluded_relative(rel + "/"):
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            child = current / name
            if child.is_symlink():
                continue
            rel = child.relative_to(root).as_posix()
            if _is_excluded_relative(rel):
                continue
            yield child


def refuse_user_overwrite(
    destination: Path,
    *,
    owned_relative: Set[str],
    output_root: Path,
) -> None:
    """Refuse to overwrite files not recorded as owned by this initializer run."""
    if not destination.exists():
        return
    if destination.is_symlink():
        raise InitializerError(ERR_SYMLINK)
    rel = assert_under_root(destination, output_root).relative_to(
        output_root.resolve(strict=False)
    ).as_posix()
    if rel not in owned_relative:
        raise InitializerError(ERR_OVERWRITE)


def resolve_output_root(path: Path) -> Path:
    return ensure_directory(path, label_code=ERR_OUTPUT_ROOT)


def resolve_template_root(path: Path) -> Path:
    root = resolve_tree_root(path, label_code=ERR_TEMPLATE_ROOT)
    if not root.is_dir():
        raise InitializerError(ERR_TEMPLATE_ROOT)
    return root
