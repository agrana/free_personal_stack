#!/usr/bin/env python3
"""APP-005 reproducible app launcher (plan / apply / resume).

Consumes a validated app.config and an explicit pinned template revision.
Local planning/scaffold are separated from remote execution. The shipped
default apply adapter does not perform live cloud mutations
(apply.live_integration_missing).

Never logs secret values, raw Terraform plans, response bodies, or headers.
Does not read DATABASE_URL / home credential stores / supabase/.temp values.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bootstrap import PINNED_TEMPLATE_REVISION  # noqa: E402
from bootstrap.adapters import BlockedLiveApplyAdapter  # noqa: E402
from bootstrap.engine import apply_app, plan_app, resume_app  # noqa: E402
from bootstrap.errors import InitializerError  # noqa: E402
from bootstrap.redact import redact_text, safe_code  # noqa: E402
from bootstrap.validators_bridge import load_json_object  # noqa: E402


def _emit(payload: Mapping[str, Any], *, stream=None) -> None:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    # Pattern scrub only — never truncate structured JSON envelopes.
    text = redact_text(text, max_length=None)
    print(text, file=stream or sys.stdout)


def _fail(code: str, **extra: Any) -> int:
    _emit({"ok": False, "code": safe_code(code), **extra}, stream=sys.stderr)
    return 1


def _load_config(path: Path) -> dict:
    return load_json_object(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app-new",
        description=(
            "APP-005 app initializer: plan (local), apply (approved digest), resume. "
            "Default apply is blocked without a live integration adapter."
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to validated non-secret app.config JSON",
    )
    parser.add_argument(
        "--template-revision",
        required=True,
        help=f"Exact pinned template commit (expected {PINNED_TEMPLATE_REVISION})",
    )
    parser.add_argument(
        "--template-root",
        type=Path,
        help="Local template tree containing TEMPLATE_REVISION marker",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        help="Destination root for generated scaffold (plan)",
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        required=True,
        help="Private per-app state root (manifest, plan artifact, lock)",
    )
    parser.add_argument(
        "--plan-digest",
        help="Exact reviewed plan digest required for apply/resume",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apply path simulation only (no provider_operation persistence)",
    )
    parser.add_argument(
        "--config-mode",
        choices=("draft", "deployment-ready"),
        default="draft",
        help="Config validation mode for plan (default: draft)",
    )
    parser.add_argument(
        "--format",
        choices=("json",),
        default="json",
        help="Output format (json only)",
    )
    parser.add_argument(
        "command",
        choices=("plan", "apply", "resume"),
        help="Launcher command",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = _load_config(args.config)
    except InitializerError as exc:
        return _fail(exc.code)

    try:
        if args.command == "plan":
            if args.template_root is None or args.output_root is None:
                return _fail("input.invalid", reason="plan_requires_template_and_output_root")
            result = plan_app(
                config=config,
                template_revision=args.template_revision,
                template_root=args.template_root,
                output_root=args.output_root,
                private_state_root=args.state_root,
                config_mode=args.config_mode,
            )
        elif args.command == "apply":
            if not args.plan_digest:
                return _fail("plan.digest_required")
            result = apply_app(
                config=config,
                template_revision=args.template_revision,
                private_state_root=args.state_root,
                plan_digest=args.plan_digest,
                adapter=BlockedLiveApplyAdapter(),
                dry_run=bool(args.dry_run),
            )
        else:  # resume
            if not args.plan_digest:
                return _fail("plan.digest_required")
            result = resume_app(
                config=config,
                template_revision=args.template_revision,
                private_state_root=args.state_root,
                plan_digest=args.plan_digest,
                adapter=BlockedLiveApplyAdapter(),
                dry_run=bool(args.dry_run),
            )
    except InitializerError as exc:
        return _fail(exc.code)
    except Exception:
        # Never surface exception text (may contain paths/secrets from lower layers).
        return _fail("error.unspecified")

    payload = {
        "ok": result.ok,
        "code": safe_code(result.code),
        "planDigest": result.plan_digest,
        "status": result.status,
        "details": result.details or {},
    }
    _emit(payload, stream=sys.stdout if result.ok else sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
