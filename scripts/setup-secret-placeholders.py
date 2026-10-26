#!/usr/bin/env python3
"""Create missing repository Actions secret placeholders; dry-run by default.

Uses authenticated gh, never reads secret values. Run without concurrent secret
editors: GitHub's set API is an upsert, not an atomic create-if-absent operation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

SENTINEL = "SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE"
EXAMPLE = Path(__file__).resolve().parents[1] / "examples/app.config.example.json"


class SetupError(Exception):
    pass


def gh(*args: str, value: str | None = None) -> str:
    try:
        result = subprocess.run(
            ["gh", *args], input=value, text=True, capture_output=True, check=False,
        )
    except OSError:
        raise SetupError("Cannot execute gh; install and authenticate GitHub CLI.") from None
    if result.returncode:
        # Never echo remote stdout/stderr: it may include credential material.
        raise SetupError("GitHub command failed; check authentication and repository Secrets permissions.")
    return result.stdout


def existing_names(repo: str) -> set[str]:
    try:
        pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/actions/secrets"))
        if not isinstance(pages, list) or not pages:
            raise ValueError
        names = set()
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get("secrets"), list):
                raise ValueError
            for secret in page["secrets"]:
                if not isinstance(secret, dict) or not isinstance(secret.get("name"), str):
                    raise ValueError
                names.add(secret["name"].upper())
        return names
    except (ValueError, TypeError):
        raise SetupError("Invalid secret inventory; no further writes attempted.") from None


def onboard(repo: str, apply: bool = False) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repo) or repo.split("/")[1] in {".", ".."}:
        raise SetupError("--repo must explicitly name OWNER/REPO (not a URL).")
    aliases = json.loads(EXAMPLE.read_text())["secretAliases"]
    names = sorted(set(aliases.values()))
    if len(names) != 7 or not all(re.fullmatch(r"[A-Z][A-Z0-9_]+", name) for name in names):
        raise SetupError("Expected exactly seven valid example secret aliases.")
    try:
        identity = json.loads(gh("api", f"repos/{repo}"))["full_name"]
    except (ValueError, KeyError, TypeError):
        raise SetupError("Cannot verify target repository.") from None
    if not isinstance(identity, str) or identity.lower() != repo.lower():
        raise SetupError("GitHub target differs from explicit --repo; refusing writes.")
    print(f"Verified target: {identity}")
    existing = existing_names(repo)  # Complete inventory must succeed before any write.
    for name in names:
        if name in existing:
            print(f"Preserve existing: {name}")
            continue
        if not apply:
            print(f"Would create placeholder: {name}")
            continue
        # Recheck before each upsert; never intentionally replace an existing entry.
        if name in existing_names(repo):
            print(f"Preserve existing: {name}")
            continue
        gh("secret", "set", name, "--repo", repo, value=SENTINEL)
        print(f"Created placeholder: {name}")
    print("Placeholders are NOT readiness. Replace them in repository Settings > Secrets and variables > Actions.")
    print(f"https://github.com/{identity}/settings/secrets/actions")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Explicit target OWNER/REPO; never inferred from remotes")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Create missing entries (remote mutation)")
    mode.add_argument("--dry-run", action="store_true", help="List planned changes only (default)")
    args = parser.parse_args(argv)
    try:
        onboard(args.repo, args.apply)
    except (SetupError, OSError, ValueError, KeyError, TypeError):
        print("Secret placeholder setup failed; no further writes attempted. Check gh authentication, target and Secrets permissions. Earlier successful entries, if any, remain; rerun safely after fixing the error.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
