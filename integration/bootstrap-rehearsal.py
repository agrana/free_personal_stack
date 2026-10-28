#!/usr/bin/env python3
"""APP-008 local bootstrap rehearsal (stub/local fixture only).

Generates a disposable second-app snapshot from an exact Git tracked-tree export,
runs synthetic lint/type-check/build, exercises the secrets-only launcher against
a local Vercel CLI stub, and writes a sanitized evidence manifest.

provider_mode=stub, live_cloud_rehearsal=false. Never provisions cloud resources.
Never deletes caller-supplied directories. Never inherits real provider secrets.
Safety boundary is patched provider clients + stub CLI (not an OS network sandbox).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from unittest.mock import patch

PROVIDER_MODE = "stub"
LIVE_CLOUD_REHEARSAL = False

FIXTURE_APP_SLUG = "fixture-app008-rehearsal"
FIXTURE_REPOSITORY = "FixtureOwner/fixture-app008-rehearsal"
FIXTURE_REPOSITORY_ID = "3000000008"
FIXTURE_NAMESPACE = "fixture_app008_ns"
FIXTURE_TEAM_ID = "team_FixtureApp008"
FIXTURE_PROJECT_ID = "prj_FixtureApp008"
FIXTURE_DEPLOYMENT_ID = "dpl_FixtureApp008"

SYNTHETIC_SUPABASE_URL = "https://fixture-app008.supabase.co"
SYNTHETIC_SUPABASE_ANON_KEY = (
    "sb_publishable_"
    + hashlib.sha256(b"app008-rehearsal-anon-seed").hexdigest()
)

# STATIC source-review reference only — not runtime verification of a live manifest.
# Pointer only — this rehearsal does not invent a live production evidence blob.
ARENA_EVIDENCE_POINTER = {
    "review_kind": "static_source_review_reference",
    "runtime_live_manifest_verified": False,
    "source_path": "../agent-arena/scripts/deploy-arena-shell-cli.py",
    "docs_path": "../agent-arena/docs/arena-shell-cli.md",
    "reviewed_source_sha_literal": "fa9b793dec761ac859ada434510c79b7248635e4",
    "note": (
        "Status categories and emit fields below are manually observed constants "
        "from Arena CLI source emit() call sites (static source review). "
        "This is not runtime verification of an actual live evidence manifest. "
        "APP-012 owns historical production evidence gaps and live deploy-no-op proof. "
        "APP-013 owns clean GitHub Actions plus actual second-repo/provider E2E."
    ),
}

ARENA_EMIT_STATUS_CATEGORIES = (
    "project_readback",
    "deployment_created",
    "page_and_health_verified",
    "ready_but_verification_protected",
)

ARENA_EMIT_SUCCESS_FIELDS = (
    "status",
    "project_id",
    "deployment_id",
    "url",
    "source_sha",
)

TEMPLATE_LAUNCHER_STATUS_CATEGORIES = (
    "setup_required",
    "plan",
    "project_created_recovery_handle",
    "project_readback",
    "page_and_health_verified",
    "ready_but_verification_protected",
)

EMITTED_EVENT_ALLOWLIST = (
    "status",
    "project_id",
    "deployment_id",
    "url",
    "source_sha",
    "digest",
    "repository_id",
    "team_id",
    "project_name",
    "public_configuration_sha256",
    "allowed_mutations",
    "required_secret_names",
)

# Minimal keys allowed into npm/git child processes. No USER/LOGNAME/NODE_OPTIONS.
CHILD_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "CI",
    "SYSTEMROOT",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "npm_config_cache",
    "NPM_CONFIG_CACHE",
    "HUSKY",
)

SAFE_DOTENV_BASENAMES = frozenset({
    ".env.example",
    ".env.sample",
    ".env.template",
})

BANNED_PATH_SEGMENTS = frozenset({
    "node_modules",
    ".next",
    ".venv",
    ".vercel",
})

REQUIRED_NODE_MAJOR = 24
PINNED_NODE_PACKAGE = "node@24.22.1"

REPO_ROOT = Path(__file__).resolve().parents[1]

VERCEL_CLI_STUB = r'''#!/usr/bin/env python3
import json, os, pathlib, sys

def check(cond, msg="stub_check_failed"):
    if not cond:
        raise RuntimeError(msg)

p = pathlib.Path(__file__).with_suffix(".json")
s = json.loads(p.read_text())
a = sys.argv[1:]
check(os.environ.get("VERCEL_TOKEN") == "fixture-offline-token", "stub_token")
check(not any(k in os.environ for k in (
    "SUPABASE_URL", "SUPABASE_ANON_KEY", "VERCEL_API_TOKEN", "GITHUB_TOKEN")),
    "stub_secret_leak")
check("--token" not in a and "--scope" in a, "stub_cli_args")
value = sys.stdin.read()
s["calls"].append([a, value])
if a[:2] == ["api", "/v10/projects"]:
    check(a[2:6] == ["-X", "POST", "--input", "-"], "stub_create_args")
    body = json.loads(value)
    check(body["framework"] == "nextjs" and body["nodeVersion"] == "24.x", "stub_create_body")
    if s.get("collision409"):
        p.write_text(json.dumps(s)); print("HTTP 409", file=sys.stderr); sys.exit(1)
    check(not s["project"], "stub_project_exists")
    s["project"] = {
        "id": "prj_FixtureApp008",
        "name": body["name"],
        "accountId": "team_FixtureApp008",
    }
    print(json.dumps(s["project"]))
elif a[:2] == ["project", "inspect"]:
    check(a[2] == s["project"]["id"], "stub_inspect_id")
    print(json.dumps({
        "id": s.get("inspect_id", s["project"]["id"]),
        "name": s["project"]["name"],
        "owner": {"slug": "not-an-id"},
        "config": {},
    }))
elif a[:2] == ["env", "add"]:
    check("--type" in a and a[a.index("--type") + 1] == "config", "stub_env_type")
    key = a[2]
    check(key.startswith("TEMPLATE_REPOSITORY_ID_") or key in (
        "NEXT_PUBLIC_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_ANON_KEY"), "stub_env_key")
    check(("--force" in a) == key.startswith("NEXT_PUBLIC_"), "stub_env_force")
    if s.get("interrupt") == key:
        p.write_text(json.dumps(s)); sys.exit(1)
    s["envs"] = [e for e in s["envs"] if e["key"] != key]
    s["envs"].append({
        "key": key,
        "value": "opaque-ciphertext",
        "target": ["production"],
        "type": "encrypted",
        "visibility": "config",
    })
elif a[0] == "deploy":
    sha = a[a.index("--env") + 1].split("=", 1)[1]
    check(a[a.index("--build-env") + 1] == "VERCEL_GIT_COMMIT_SHA=" + sha, "stub_build_env")
    metas = dict(a[i + 1].split("=", 1) for i, x in enumerate(a) if x == "--meta")
    check(metas["githubCommitSha"] == sha, "stub_meta_sha")
    s["remote"] = {
        "id": "dpl_FixtureApp008",
        "url": "fixture-app008-rehearsal.vercel.app",
        "projectId": "prj_FixtureApp008",
        "target": "production",
        "readyState": "READY",
        "meta": metas,
        "alias": ["fixture-app008-rehearsal.vercel.app"],
    }
    s["project"]["targets"] = {"production": {"id": "dpl_FixtureApp008"}}
    print(json.dumps({
        "id": "dpl_FixtureApp008",
        "url": "fixture-app008-rehearsal.vercel.app",
        "productionUrl": "fixture-app008-rehearsal.vercel.app",
        "target": "production",
        "readyState": "READY",
    }))
else:
    raise RuntimeError("stub_unknown_command")
p.write_text(json.dumps(s))
'''


class RehearsalError(RuntimeError):
    pass


def fixture_labels() -> Dict[str, Any]:
    return {
        "provider_mode": PROVIDER_MODE,
        "live_cloud_rehearsal": LIVE_CLOUD_REHEARSAL,
        "app_slug": FIXTURE_APP_SLUG,
        "repository": FIXTURE_REPOSITORY,
        "repository_id": FIXTURE_REPOSITORY_ID,
        "app_namespace": FIXTURE_NAMESPACE,
        "namespace_provisioned": False,
        "team_id": FIXTURE_TEAM_ID,
        "project_id": FIXTURE_PROJECT_ID,
        "deployment_id": FIXTURE_DEPLOYMENT_ID,
    }


def minimal_process_base_env() -> Dict[str, str]:
    """Sanitized base env for clear=True child/scenario patches (no credentials)."""
    env: Dict[str, str] = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME") or tempfile.gettempdir(),
        "TMPDIR": os.environ.get("TMPDIR") or tempfile.gettempdir(),
        "TEMP": os.environ.get("TEMP") or tempfile.gettempdir(),
        "TMP": os.environ.get("TMP") or tempfile.gettempdir(),
        "LANG": os.environ.get("LANG") or "C.UTF-8",
        "LC_ALL": os.environ.get("LC_ALL") or "C.UTF-8",
        "TERM": os.environ.get("TERM") or "dumb",
        "CI": "true",
        "HUSKY": "0",
        "VERCEL_TELEMETRY_DISABLED": "1",
    }
    for key in (
        "SYSTEMROOT",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "npm_config_cache",
        "NPM_CONFIG_CACHE",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def sanitized_child_env(
    base: Optional[Mapping[str, str]] = None,
    *,
    extras: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Build child env from an allowlist. Does not claim OS network sandboxing."""
    source = dict(base if base is not None else minimal_process_base_env())
    env: Dict[str, str] = {}
    for key in CHILD_ENV_ALLOWLIST:
        if key in source and source[key]:
            env[key] = source[key]
    env["HUSKY"] = "0"
    env["VERCEL_TELEMETRY_DISABLED"] = "1"
    env["CI"] = source.get("CI", "true")
    if extras:
        for key, value in extras.items():
            if value is None:
                continue
            env[str(key)] = str(value)
    return env


def _git(args: Sequence[str], *, cwd: Path, env: Optional[Mapping[str, str]] = None) -> str:
    child_env = sanitized_child_env(env or minimal_process_base_env())
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=child_env,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RehearsalError(f"git_transport:{exc}") from None
    if result.returncode != 0:
        raise RehearsalError(f"git_failed:{' '.join(args)}:{result.stderr.strip()[:200]}")
    return result.stdout.strip()


def _resolve_revision(source_repo: Path, revision: Optional[str]) -> str:
    if revision:
        rev = revision.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", rev):
            raise RehearsalError("revision_invalid")
        _git(["cat-file", "-t", rev], cwd=source_repo)
        return rev
    return _git(["rev-parse", "HEAD"], cwd=source_repo).lower()


def _posix_relpath(path: str) -> str:
    rel = path.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def is_banned_tracked_archive_path(relpath: str) -> bool:
    """Fail-closed allowlist for trusted source-tree export (not a general archive framework)."""
    rel = _posix_relpath(relpath)
    if not rel:
        return False
    parts = rel.split("/")
    basename = parts[-1]
    if any(part in BANNED_PATH_SEGMENTS for part in parts):
        return True
    if basename == ".npmrc":
        return True
    if basename.startswith(".env"):
        return basename not in SAFE_DOTENV_BASENAMES
    return False


def preflight_tracked_archive_paths(source_repo: Path, source_revision: str) -> None:
    listing = _git(["ls-tree", "-r", "--name-only", source_revision], cwd=source_repo)
    for rel in listing.splitlines():
        if is_banned_tracked_archive_path(rel):
            # Fixed error only — never print file contents.
            raise RehearsalError("tracked_secret_or_cache_path_rejected")


def export_tracked_app_snapshot(
    *,
    source_repo: Path,
    revision: Optional[str],
    destination: Path,
) -> Dict[str, Any]:
    """Export Git tracked tree into destination and commit as independent history.

    Does not delete destination's parent. Refuses if destination already exists.
    Preflights banned secret/cache paths before extraction or independent commit.
    """
    source_repo = source_repo.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise RehearsalError("destination_exists")

    source_revision = _resolve_revision(source_repo, revision)
    preflight_tracked_archive_paths(source_repo, source_revision)

    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="app008-archive-") as archive_tmp:
        archive_path = Path(archive_tmp) / "tracked.tar"
        _git(
            ["archive", "--format=tar", "-o", str(archive_path), source_revision],
            cwd=source_repo,
        )
        destination.mkdir(parents=False)
        subprocess.run(
            ["tar", "-xf", str(archive_path), "-C", str(destination)],
            check=True,
            capture_output=True,
            timeout=120,
        )

    # Independent Git history (not a clone of the template remote).
    _git(["init", "-q", "-b", "main"], cwd=destination)
    namespace_doc = {
        "app_namespace": FIXTURE_NAMESPACE,
        "namespace_provisioned": False,
        "provider_mode": PROVIDER_MODE,
        "live_cloud_rehearsal": LIVE_CLOUD_REHEARSAL,
        "note": "DB-free shell rehearsal; namespace is metadata only and unprovisioned.",
    }
    (destination / "app008-rehearsal-namespace.json").write_text(
        json.dumps(namespace_doc, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _git(["add", "-A"], cwd=destination)
    commit_env = sanitized_child_env(
        minimal_process_base_env(),
        extras={
            "GIT_AUTHOR_NAME": "APP008 Fixture",
            "GIT_AUTHOR_EMAIL": "app008-fixture@example.invalid",
            "GIT_COMMITTER_NAME": "APP008 Fixture",
            "GIT_COMMITTER_EMAIL": "app008-fixture@example.invalid",
        },
    )
    try:
        result = subprocess.run(
            ["git", "-c", "user.name=APP008 Fixture",
             "-c", "user.email=app008-fixture@example.invalid",
             "commit", "-qm", "fixture: independent second-app snapshot for APP-008"],
            cwd=str(destination),
            env=commit_env,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RehearsalError(f"git_commit_transport:{exc}") from None
    if result.returncode != 0:
        raise RehearsalError(f"git_commit_failed:{result.stderr.strip()[:200]}")
    app_revision = _git(["rev-parse", "HEAD"], cwd=destination).lower()
    if app_revision == source_revision:
        raise RehearsalError("app_revision_must_differ")

    for banned in ("node_modules", ".env", ".next", ".venv", ".vercel", ".npmrc"):
        if (destination / banned).exists():
            raise RehearsalError(f"banned_path_present:{banned}")

    return {
        "provider_mode": PROVIDER_MODE,
        "live_cloud_rehearsal": LIVE_CLOUD_REHEARSAL,
        "snapshot_root": str(destination),
        "source_repo": str(source_repo),
        "source_revision": source_revision,
        "app_revision": app_revision,
        "app_slug": FIXTURE_APP_SLUG,
        "repository": FIXTURE_REPOSITORY,
        "repository_id": FIXTURE_REPOSITORY_ID,
        "app_namespace": FIXTURE_NAMESPACE,
        "namespace_provisioned": False,
    }


def compare_status_schema_to_arena() -> Dict[str, Any]:
    shared = sorted(
        set(ARENA_EMIT_STATUS_CATEGORIES) & set(TEMPLATE_LAUNCHER_STATUS_CATEGORIES)
    )
    arena_only = sorted(
        set(ARENA_EMIT_STATUS_CATEGORIES) - set(TEMPLATE_LAUNCHER_STATUS_CATEGORIES)
    )
    template_only = sorted(
        set(TEMPLATE_LAUNCHER_STATUS_CATEGORIES) - set(ARENA_EMIT_STATUS_CATEGORIES)
    )
    return {
        "provider_mode": PROVIDER_MODE,
        "live_cloud_rehearsal": LIVE_CLOUD_REHEARSAL,
        "review_kind": "static_source_review_reference",
        "runtime_live_manifest_verified": False,
        "invented_live_manifest": False,
        "arena_evidence_pointer": dict(ARENA_EVIDENCE_POINTER),
        "shared_status_categories": shared,
        "arena_only_status_categories": arena_only,
        "template_launcher_only_status_categories": template_only,
        "shared_success_fields": sorted(
            set(ARENA_EMIT_SUCCESS_FIELDS)
            & {"status", "project_id", "deployment_id", "url", "source_sha"}
        ),
        "absent_or_different": {
            "arena_emits_deployment_created": True,
            "template_emits_plan_and_setup_required": True,
            "template_adds_public_configuration_sha256_on_plan": True,
            "arena_homepage_checks_literal_Agent_Arena": True,
            "template_homepage_checks_generic_html_doctype": True,
            "live_production_evidence_blob": "absent_owned_by_APP-012",
            "live_deploy_noop_proof": "absent_owned_by_APP-012",
            "actions_and_second_repo_provider_e2e": "absent_owned_by_APP-013",
        },
    }


def _load_launcher(snapshot_root: Path):
    """Load launch-app.py and its helpers from the snapshot, not the controlling repo."""
    path = Path(snapshot_root).resolve() / "scripts" / "launch-app.py"
    if not path.is_file():
        raise RehearsalError("launcher_missing")
    # Unique module name per path so helpers bind to this snapshot __file__.
    mod_name = "launch_app_rehearsal_" + hashlib.sha256(str(path).encode()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise RehearsalError("launcher_missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _write_stub(work_dir: Path) -> Tuple[Path, Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    binary = work_dir / "vercel"
    state = binary.with_suffix(".json")
    binary.write_text(VERCEL_CLI_STUB, encoding="utf-8")
    binary.chmod(0o755)
    state.write_text(json.dumps({"project": None, "envs": [], "calls": []}), encoding="utf-8")
    return binary, state


def _state(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(path: Path, data: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _allowlisted_event(raw: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"fixture": True}
    for key in EMITTED_EVENT_ALLOWLIST:
        if key in raw:
            out[key] = raw[key]
    return out


def _parse_emitted_events(stdout_text: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "status" in payload:
            events.append(_allowlisted_event(payload))
    return events


@contextlib.contextmanager
def _provider_scenario_socket_guard():
    """Optional catch-all: accidental unpatched sockets fail inside provider scenarios."""
    original = socket.socket

    def blocked(*_a: Any, **_k: Any) -> Any:
        raise OSError("provider_scenario_unpatched_network")

    socket.socket = blocked  # type: ignore[misc, assignment]
    try:
        yield
    finally:
        socket.socket = original  # type: ignore[misc]


def run_launcher_scenarios(
    *,
    snapshot_root: Path,
    app_revision: str,
    work_dir: Path,
) -> Dict[str, Any]:
    """Exercise collision, interrupt/resume, and unchanged no-op against local stub."""
    snapshot_root = Path(snapshot_root).resolve()
    app = _load_launcher(snapshot_root)
    if Path(app.__file__ or "").resolve().parent != (snapshot_root / "scripts").resolve():
        raise RehearsalError("launcher_not_from_snapshot")
    binary, state_path = _write_stub(work_dir)
    steps: List[Dict[str, Any]] = []
    invocations: List[Dict[str, Any]] = []
    t0 = time.perf_counter()

    def record(name: str, started: float, **fields: Any) -> None:
        steps.append({
            "step": name,
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            **fields,
        })

    synthetic = {
        "GITHUB_REPOSITORY": FIXTURE_REPOSITORY,
        "GITHUB_REPOSITORY_ID": FIXTURE_REPOSITORY_ID,
        "GITHUB_SHA": app_revision,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "VERCEL_API_TOKEN": "fixture-offline-token",
        "VERCEL_TEAM_ID": FIXTURE_TEAM_ID,
        "SUPABASE_URL": SYNTHETIC_SUPABASE_URL,
        "SUPABASE_ANON_KEY": SYNTHETIC_SUPABASE_ANON_KEY,
        "GITHUB_TOKEN": "fixture-github-unused",
    }
    scenario_env = {**minimal_process_base_env(), **synthetic}

    vercel = app.load("check-vercel-access")
    supabase = app.load("check-supabase-access")

    def github(path: str) -> Dict[str, Any]:
        if path.startswith("/repositories/"):
            return {
                "id": int(FIXTURE_REPOSITORY_ID),
                "full_name": FIXTURE_REPOSITORY,
                "is_template": False,
                "fork": False,
                "default_branch": "main",
            }
        return {"sha": app_revision}

    def items(route, key, token, query=None):
        return iter(
            [{"id": FIXTURE_TEAM_ID}]
            if key == "teams"
            else ([_state(state_path)["project"]] if _state(state_path)["project"] else [])
        )

    def read(path, token):
        s = _state(state_path)
        if "/env?" in path:
            return {"envs": s["envs"]}
        if path.startswith("/v9/projects/"):
            return s["project"]
        if path.startswith("/v13/deployments/"):
            return s["remote"]
        raise RuntimeError(f"unexpected_read_path:{path}")

    def page(url: str):
        if "fixture-app008" not in url and "vercel.app" in url:
            raise RuntimeError(f"non_fixture_url:{url}")
        if url.endswith("/api/health"):
            return (
                200,
                json.dumps(
                    {"status": "ready", "service": "app", "revision": app_revision}
                ).encode(),
            )
        return (200, b"<!doctype html><html>Shell</html>")

    def run_app(scenario: str, **kw: Any) -> int:
        buf = io.StringIO()
        code: Optional[int] = None
        failure: Optional[str] = None
        try:
            with contextlib.redirect_stdout(buf):
                code = app.run(snapshot_root, str(binary), **kw)
        except app.Failure as exc:
            failure = str(exc)
            raise
        finally:
            events = _parse_emitted_events(buf.getvalue())
            invocations.append({
                "scenario": scenario,
                "exit_code": code,
                "failure": failure,
                "events": events,
            })
        if code is None:
            raise RehearsalError("run_app_missing_exit_code")
        return code

    def deploy_count() -> int:
        return sum(c[0][0] == "deploy" for c in _state(state_path)["calls"])

    def create_count() -> int:
        return sum(c[0][:2] == ["api", "/v10/projects"] for c in _state(state_path)["calls"])

    patches = [
        patch.dict(os.environ, scenario_env, clear=True),
        patch.object(app, "github", side_effect=github),
        patch.object(
            app,
            "load",
            side_effect=lambda name: vercel if name == "check-vercel-access" else supabase,
        ),
        patch.object(vercel, "items", side_effect=items),
        patch.object(vercel, "get_json", side_effect=read),
        patch.object(supabase, "check", return_value="passed"),
        patch.object(app, "get_page", side_effect=page),
    ]
    for p in patches:
        p.start()
    try:
        with _provider_scenario_socket_guard():
            # 1) First deploy
            started = time.perf_counter()
            code = run_app("first_deploy")
            if code != 0:
                raise RehearsalError(f"first_deploy_failed:{code}")
            first_deploys = deploy_count()
            record("first_deploy", started, exit_code=code, deployment_count=first_deploys)

            # 2) Collision rejection on a fresh slug-state create attempt
            started = time.perf_counter()
            collision_state = {"project": None, "envs": [], "calls": [], "collision409": True}
            _save_state(state_path, collision_state)
            collision_rejected = False
            try:
                run_app("collision_reject")
            except app.Failure as exc:
                collision_rejected = str(exc) == "cli_failed"
            if not collision_rejected:
                raise RehearsalError("collision_not_rejected")
            if _state(state_path)["envs"] or deploy_count():
                raise RehearsalError("collision_mutated")
            record("collision_reject", started, rejected=True)

            # 3) Interrupt during public env bind, then resume without recreating.
            started = time.perf_counter()
            _save_state(
                state_path,
                {
                    "project": None,
                    "envs": [],
                    "calls": [],
                    "interrupt": "NEXT_PUBLIC_SUPABASE_URL",
                },
            )
            interrupted = False
            try:
                run_app("interrupt")
            except app.Failure as exc:
                interrupted = str(exc) == "cli_failed"
            if not interrupted:
                raise RehearsalError("interrupt_not_raised")
            s = _state(state_path)
            s.pop("interrupt", None)
            _save_state(state_path, s)
            code = run_app("resume")
            if code != 0:
                raise RehearsalError(f"resume_failed:{code}")
            resumed_creates = create_count()
            if resumed_creates != 1:
                raise RehearsalError(f"resume_recreated_project:{resumed_creates}")
            resumed_deploys = deploy_count()
            record(
                "interrupt_resume",
                started,
                exit_code=code,
                project_create_count=resumed_creates,
                deployment_count=resumed_deploys,
            )

            # 4) Unchanged no-op: same SHA + config must not add deployments.
            started = time.perf_counter()
            before = deploy_count()
            code = run_app("unchanged_noop")
            if code != 0:
                raise RehearsalError(f"noop_deploy_failed:{code}")
            after = deploy_count()
            if after != before:
                raise RehearsalError(f"noop_increased_deployments:{before}->{after}")
            code_verify = run_app("unchanged_verify", operation="verify")
            if code_verify != 0:
                raise RehearsalError(f"noop_verify_failed:{code_verify}")
            if deploy_count() != after:
                raise RehearsalError("verify_mutated_deployments")
            record(
                "unchanged_noop",
                started,
                deployment_count_before=before,
                deployment_count_after=after,
                verify_exit_code=code_verify,
            )
            final_deploys = after
            final_creates = create_count()
    finally:
        for p in patches:
            p.stop()

    # Actual emitted events (not fabricated). Require URL/source_sha on success paths.
    success_events = [
        ev
        for inv in invocations
        if inv["scenario"] in ("first_deploy", "resume", "unchanged_noop", "unchanged_verify")
        for ev in inv["events"]
        if ev.get("status") == "page_and_health_verified"
    ]
    if not success_events:
        raise RehearsalError("missing_verified_events")
    for ev in success_events:
        if not ev.get("url") or not ev.get("source_sha"):
            raise RehearsalError("verified_event_missing_url_or_source_sha")
        if ev.get("source_sha") != app_revision:
            raise RehearsalError("verified_event_source_sha_mismatch")

    return {
        "provider_mode": PROVIDER_MODE,
        "live_cloud_rehearsal": LIVE_CLOUD_REHEARSAL,
        "collision_rejected": True,
        "interrupt_recovered": True,
        "unchanged_noop": True,
        "project_create_count": final_creates,
        "deployment_count_after_first": first_deploys,
        "deployment_count_after_noop": final_deploys,
        "operator_steps": steps,
        "operator_step_count": len(steps),
        "elapsed_seconds": round(time.perf_counter() - t0, 4),
        "synthetic_ids": {
            "project_id": FIXTURE_PROJECT_ID,
            "team_id": FIXTURE_TEAM_ID,
            "deployment_id": FIXTURE_DEPLOYMENT_ID,
            "repository_id": FIXTURE_REPOSITORY_ID,
            "app_slug": FIXTURE_APP_SLUG,
        },
        "invocations": invocations,
        "events": [
            ev
            for inv in invocations
            for ev in inv["events"]
        ],
        "safety_boundary": "patched_provider_clients_and_stub_cli",
        "os_network_sandbox": False,
    }


def probe_runtime(*, snapshot_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(snapshot_root) if snapshot_root else REPO_ROOT
    package_path = root / "package.json"
    declared = None
    if package_path.is_file():
        try:
            declared = json.loads(package_path.read_text(encoding="utf-8")).get(
                "engines", {}
            ).get("node")
        except (OSError, json.JSONDecodeError, TypeError):
            declared = None

    def _version(cmd: Sequence[str]) -> Tuple[Optional[str], Optional[int]]:
        try:
            result = subprocess.run(
                list(cmd),
                env=sanitized_child_env(minimal_process_base_env()),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None, None
        if result.returncode != 0:
            return None, None
        text = (result.stdout or result.stderr or "").strip().splitlines()
        raw = text[0].strip() if text else ""
        match = re.search(r"v?(\d+)\.", raw)
        major = int(match.group(1)) if match else None
        return raw, major

    node_raw, node_major = _version(["node", "--version"])
    npm_raw, _npm_major = _version(["npm", "--version"])
    compatible = (
        node_major == REQUIRED_NODE_MAJOR
        and isinstance(declared, str)
        and declared.startswith(f"{REQUIRED_NODE_MAJOR}")
    )
    return {
        "node_version": node_raw,
        "npm_version": npm_raw,
        "node_major": node_major,
        "declared_engines_node": declared,
        "required_node_major": REQUIRED_NODE_MAJOR,
        "compatible_with_declared_engines": bool(compatible),
        "pinned_ephemeral_node_package": PINNED_NODE_PACKAGE,
    }


def require_node24_for_build(runtime: Mapping[str, Any]) -> None:
    if not runtime.get("compatible_with_declared_engines"):
        raise RehearsalError("runtime_mismatch")


def _run_npm(
    args: Sequence[str],
    *,
    cwd: Path,
    extras: Optional[Mapping[str, str]] = None,
    timeout: int = 900,
    diagnostics_dir: Optional[Path] = None,
    diagnostic_label: Optional[str] = None,
) -> Dict[str, Any]:
    env = sanitized_child_env(minimal_process_base_env(), extras=extras)
    started = time.perf_counter()
    try:
        result = subprocess.run(
            ["npm", *args],
            cwd=str(cwd),
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "command": ["npm", *args],
            "exit_code": 124,
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "executed": True,
        }
    except OSError as exc:
        return {
            "command": ["npm", *args],
            "exit_code": 127,
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "executed": False,
            "error_kind": type(exc).__name__,
        }
    if diagnostics_dir is not None and diagnostic_label:
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        log_path = diagnostics_dir / f"{diagnostic_label}.log"
        # Private diagnostic only — not copied into sanitized evidence.
        log_path.write_text(
            "stdout:\n"
            + (result.stdout or "")[-20000:]
            + "\nstderr:\n"
            + (result.stderr or "")[-20000:],
            encoding="utf-8",
        )
    return {
        "command": ["npm", *args],
        "exit_code": result.returncode,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "executed": True,
    }


def _verify_built_health(
    snapshot_root: Path,
    *,
    expected_sha: str,
    extras: Mapping[str, str],
) -> Dict[str, Any]:
    """Start the built app briefly and assert /api/health revision == snapshot HEAD."""
    next_bin = snapshot_root / "node_modules" / ".bin" / "next"
    if not next_bin.is_file():
        return {"executed": False, "exit_code": 127, "error": "next_bin_missing"}
    if not (snapshot_root / ".next").is_dir():
        return {"executed": False, "exit_code": 127, "error": "build_missing"}

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])

    env = sanitized_child_env(
        minimal_process_base_env(),
        extras={
            **dict(extras),
            "PORT": str(port),
            "APP006_LOCAL_TEST_REVISION": expected_sha,
            "VERCEL_GIT_COMMIT_SHA": expected_sha,
        },
    )
    started = time.perf_counter()
    proc = subprocess.Popen(
        [str(next_bin), "start", "-H", "127.0.0.1", "-p", str(port)],
        cwd=str(snapshot_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        deadline = time.time() + 45
        body = b""
        status = 0
        last_err = "unreached"
        while time.time() < deadline:
            if proc.poll() is not None:
                return {
                    "executed": True,
                    "exit_code": 1,
                    "error": "server_exit",
                    "elapsed_seconds": round(time.perf_counter() - started, 4),
                }
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    status = int(resp.status)
                    body = resp.read(4096)
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last_err = type(exc).__name__
                time.sleep(0.4)
        else:
            return {
                "executed": True,
                "exit_code": 1,
                "error": f"timeout:{last_err}",
                "elapsed_seconds": round(time.perf_counter() - started, 4),
            }
        if status != 200:
            return {
                "executed": True,
                "exit_code": 1,
                "error": "bad_status",
                "elapsed_seconds": round(time.perf_counter() - started, 4),
            }
        payload = json.loads(body.decode("utf-8"))
        ok = (
            payload.get("status") == "ready"
            and payload.get("service") == "app"
            and payload.get("revision") == expected_sha
        )
        return {
            "executed": True,
            "exit_code": 0 if ok else 1,
            "expected_sha": expected_sha,
            "observed_revision": payload.get("revision"),
            "elapsed_seconds": round(time.perf_counter() - started, 4),
        }
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def run_snapshot_quality(
    snapshot_root: Path,
    *,
    skip_npm: bool = False,
    diagnostics_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    snapshot_root = Path(snapshot_root).resolve()
    app_revision = _git(["rev-parse", "HEAD"], cwd=snapshot_root).lower()
    runtime = probe_runtime(snapshot_root=snapshot_root)
    synthetic = {
        "NEXT_PUBLIC_SUPABASE_URL": SYNTHETIC_SUPABASE_URL,
        "NEXT_PUBLIC_SUPABASE_ANON_KEY": SYNTHETIC_SUPABASE_ANON_KEY,
        "APP006_LOCAL_TEST_REVISION": app_revision,
        "VERCEL_GIT_COMMIT_SHA": app_revision,
    }
    steps: List[Dict[str, Any]] = []
    if skip_npm:
        return {
            "provider_mode": PROVIDER_MODE,
            "live_cloud_rehearsal": LIVE_CLOUD_REHEARSAL,
            "skipped": True,
            "executed": False,
            "build_executed": False,
            "exit_code": None,
            "steps": steps,
            "synthetic_public_config_labeled": True,
            "build_revision": app_revision,
            "runtime": runtime,
            "status_hint": "local_contract_only",
        }

    require_node24_for_build(runtime)

    install = _run_npm(
        ["ci"],
        cwd=snapshot_root,
        extras=synthetic,
        timeout=900,
        diagnostics_dir=diagnostics_dir,
        diagnostic_label="npm_ci",
    )
    steps.append({"step": "npm_ci", **install})
    if install["exit_code"] != 0:
        return {
            "provider_mode": PROVIDER_MODE,
            "live_cloud_rehearsal": LIVE_CLOUD_REHEARSAL,
            "skipped": False,
            "executed": True,
            "build_executed": False,
            "exit_code": install["exit_code"],
            "steps": steps,
            "synthetic_public_config_labeled": True,
            "failed_step": "npm_ci",
            "build_revision": app_revision,
            "runtime": runtime,
        }

    for name, npm_args in (
        ("npm_lint", ["run", "lint"]),
        ("npm_type_check", ["run", "type-check"]),
        ("npm_build", ["run", "build"]),
    ):
        result = _run_npm(
            npm_args,
            cwd=snapshot_root,
            extras=synthetic,
            timeout=900,
            diagnostics_dir=diagnostics_dir,
            diagnostic_label=name,
        )
        steps.append({"step": name, **result})
        if result["exit_code"] != 0:
            return {
                "provider_mode": PROVIDER_MODE,
                "live_cloud_rehearsal": LIVE_CLOUD_REHEARSAL,
                "skipped": False,
                "executed": True,
                "build_executed": name == "npm_build",
                "exit_code": result["exit_code"],
                "steps": steps,
                "synthetic_public_config_labeled": True,
                "failed_step": name,
                "build_revision": app_revision,
                "runtime": runtime,
            }

    # Source-only static checks (not a substitute for the actual build above).
    started = time.perf_counter()
    page = (snapshot_root / "app" / "page.tsx").read_text(encoding="utf-8")
    health = (snapshot_root / "app" / "api" / "health" / "route.ts").read_text(encoding="utf-8")
    focused_ok = (
        "Application ready" in page
        and "/api/health" in page
        and "VERCEL_GIT_COMMIT_SHA" in health
        and (snapshot_root / "package-lock.json").is_file()
        and (snapshot_root / "scripts" / "launch-app.py").is_file()
    )
    steps.append({
        "step": "focused_snapshot_source_checks",
        "kind": "source_only",
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "exit_code": 0 if focused_ok else 1,
        "executed": True,
    })

    health_check = _verify_built_health(
        snapshot_root, expected_sha=app_revision, extras=synthetic
    )
    steps.append({"step": "built_health_revision_check", **health_check})
    if health_check.get("exit_code") != 0:
        return {
            "provider_mode": PROVIDER_MODE,
            "live_cloud_rehearsal": LIVE_CLOUD_REHEARSAL,
            "skipped": False,
            "executed": True,
            "build_executed": True,
            "exit_code": health_check.get("exit_code", 1),
            "steps": steps,
            "synthetic_public_config_labeled": True,
            "failed_step": "built_health_revision_check",
            "build_revision": app_revision,
            "runtime": runtime,
        }

    build_step = next(s for s in steps if s["step"] == "npm_build")
    return {
        "provider_mode": PROVIDER_MODE,
        "live_cloud_rehearsal": LIVE_CLOUD_REHEARSAL,
        "skipped": False,
        "executed": True,
        "build_executed": True,
        "exit_code": 0 if focused_ok else 1,
        "steps": steps,
        "synthetic_public_config_labeled": True,
        "build_elapsed_seconds": build_step["elapsed_seconds"],
        "build_revision": app_revision,
        "runtime": runtime,
    }


def run_local_rehearsal(
    *,
    source_repo: Path,
    output_dir: Path,
    revision: Optional[str] = None,
    work_root: Optional[Path] = None,
    skip_npm: bool = False,
) -> Dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_dir = output_dir / "private-diagnostics"
    tmp_holder = None
    if work_root is None:
        tmp_holder = tempfile.TemporaryDirectory(prefix="app008-rehearsal-")
        work_root = Path(tmp_holder.name)
    else:
        work_root = work_root.resolve()
        work_root.mkdir(parents=True, exist_ok=True)

    operator_steps: List[Dict[str, Any]] = []
    wall_start = time.perf_counter()
    try:
        started = time.perf_counter()
        snapshot = export_tracked_app_snapshot(
            source_repo=source_repo,
            revision=revision,
            destination=work_root / "second-app",
        )
        operator_steps.append({
            "step": "export_tracked_snapshot",
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "source_revision": snapshot["source_revision"],
            "app_revision": snapshot["app_revision"],
        })

        # Launcher refuses a dirty worktree; run stub scenarios before npm
        # install/build artifacts (ignored or tracked) appear.
        started = time.perf_counter()
        launcher = run_launcher_scenarios(
            snapshot_root=Path(snapshot["snapshot_root"]),
            app_revision=snapshot["app_revision"],
            work_dir=work_root / "stub-cli",
        )
        operator_steps.append({
            "step": "launcher_scenarios",
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "unchanged_noop": launcher["unchanged_noop"],
            "collision_rejected": launcher["collision_rejected"],
        })

        started = time.perf_counter()
        quality = run_snapshot_quality(
            Path(snapshot["snapshot_root"]),
            skip_npm=skip_npm,
            diagnostics_dir=diagnostics_dir,
        )
        operator_steps.append({
            "step": "snapshot_quality",
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "exit_code": quality.get("exit_code"),
            "executed": quality.get("executed"),
            "build_executed": quality.get("build_executed"),
        })

        started = time.perf_counter()
        schema = compare_status_schema_to_arena()
        operator_steps.append({
            "step": "schema_compare",
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "review_kind": "static_source_review_reference",
        })

        runtime = quality.get("runtime") or probe_runtime(
            snapshot_root=Path(snapshot["snapshot_root"])
        )
        build_executed = bool(quality.get("build_executed"))
        build_ok = build_executed and quality.get("exit_code") == 0
        launcher_ok = bool(launcher.get("unchanged_noop"))
        if skip_npm or not build_executed:
            acceptance_status = "local_contract_only"
            acceptance_complete = False
        elif build_ok and launcher_ok:
            acceptance_status = "local_rehearsal_complete"
            acceptance_complete = True
        else:
            acceptance_status = "local_rehearsal_incomplete"
            acceptance_complete = False

        manifest: Dict[str, Any] = {
            "task": "APP-008",
            "provider_mode": PROVIDER_MODE,
            "live_cloud_rehearsal": LIVE_CLOUD_REHEARSAL,
            "acceptance_status": acceptance_status,
            "acceptance_complete": acceptance_complete,
            "fixture_labels": fixture_labels(),
            "runtime": {
                "node_version": runtime.get("node_version"),
                "npm_version": runtime.get("npm_version"),
                "node_major": runtime.get("node_major"),
                "declared_engines_node": runtime.get("declared_engines_node"),
                "compatible_with_declared_engines": runtime.get(
                    "compatible_with_declared_engines"
                ),
                "pinned_ephemeral_node_package": PINNED_NODE_PACKAGE,
            },
            "snapshot": {
                k: snapshot[k]
                for k in (
                    "source_revision",
                    "app_revision",
                    "app_slug",
                    "repository",
                    "repository_id",
                    "app_namespace",
                    "namespace_provisioned",
                )
            },
            "invoked_snapshot_source": {
                "source_revision": snapshot["source_revision"],
                "app_revision": snapshot["app_revision"],
                "controlling_repo_basename": Path(source_repo).resolve().name,
                "revision_argument": revision,
            },
            "second_app_build": {
                "executed": bool(quality.get("executed")) and not quality.get("skipped"),
                "build_executed": build_executed,
                "exit_code": quality.get("exit_code"),
                "failed_step": quality.get("failed_step"),
                "build_revision": quality.get("build_revision"),
                "steps": [
                    {
                        "step": s["step"],
                        "exit_code": s.get("exit_code"),
                        "elapsed_seconds": s.get("elapsed_seconds"),
                        "executed": s.get("executed"),
                        **({"kind": s["kind"]} if "kind" in s else {}),
                    }
                    for s in quality.get("steps", [])
                ],
            },
            "launcher_scenarios": {
                "collision_rejected": launcher["collision_rejected"],
                "interrupt_recovered": launcher["interrupt_recovered"],
                "unchanged_noop": launcher["unchanged_noop"],
                "project_create_count": launcher["project_create_count"],
                "deployment_count_after_noop": launcher["deployment_count_after_noop"],
                "synthetic_ids": launcher["synthetic_ids"],
                "invocations": launcher["invocations"],
                "events": launcher["events"],
                "safety_boundary": launcher["safety_boundary"],
                "os_network_sandbox": launcher["os_network_sandbox"],
            },
            "status_schema_comparison": schema,
            "operator_steps": operator_steps,
            "operator_step_count": len(operator_steps),
            "elapsed_seconds_total": round(time.perf_counter() - wall_start, 4),
            "boundaries": {
                "local_fixture": True,
                "live_deployment": False,
                "live_cloud_rehearsal": False,
                "approval_required_for_live_resources": True,
                "post_shell_isolation_gates": ["APP-011"],
                "live_evidence_and_deploy_noop": ["APP-012"],
                "actions_and_second_repo_provider_e2e": ["APP-013"],
                "custom_dns": ["APP-010"],
                "os_network_sandbox": False,
                "safety_boundary": "patched_provider_clients_and_stub_cli",
            },
        }

        evidence_path = output_dir / "app008-local-rehearsal-evidence.json"
        evidence_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        md_path = output_dir / "app008-local-rehearsal-evidence.md"
        md_path.write_text(
            _evidence_markdown(manifest, evidence_path), encoding="utf-8"
        )
        manifest["evidence_json"] = str(evidence_path)
        manifest["evidence_markdown"] = str(md_path)
        return manifest
    finally:
        if tmp_holder is not None:
            tmp_holder.cleanup()
        # Never delete caller-supplied work_root.


def _evidence_markdown(manifest: Mapping[str, Any], evidence_path: Path) -> str:
    snap = manifest["snapshot"]
    build = manifest["second_app_build"]
    launch = manifest["launcher_scenarios"]
    schema = manifest["status_schema_comparison"]
    runtime = manifest.get("runtime", {})
    lines = [
        "# APP-008 local rehearsal evidence (fixture)",
        "",
        f"- provider_mode: `{manifest['provider_mode']}`",
        f"- live_cloud_rehearsal: `{manifest['live_cloud_rehearsal']}`",
        f"- acceptance_status: `{manifest.get('acceptance_status')}`",
        f"- acceptance_complete: `{manifest.get('acceptance_complete')}`",
        f"- source_revision: `{snap['source_revision']}`",
        f"- app_revision (independent history): `{snap['app_revision']}`",
        f"- app_slug: `{snap['app_slug']}`",
        f"- repository_id: `{snap['repository_id']}`",
        f"- app_namespace: `{snap['app_namespace']}` (unprovisioned)",
        f"- runtime.node_version: `{runtime.get('node_version')}`",
        f"- runtime.npm_version: `{runtime.get('npm_version')}`",
        f"- runtime.compatible_with_declared_engines: `{runtime.get('compatible_with_declared_engines')}`",
        f"- second_app_build.build_executed: `{build.get('build_executed')}` exit `{build['exit_code']}`",
        f"- build_revision: `{build.get('build_revision')}`",
        f"- collision_rejected: `{launch['collision_rejected']}`",
        f"- interrupt_recovered: `{launch['interrupt_recovered']}`",
        f"- unchanged_noop: `{launch['unchanged_noop']}`",
        f"- operator_step_count: `{manifest['operator_step_count']}`",
        f"- elapsed_seconds_total: `{manifest['elapsed_seconds_total']}`",
        f"- machine-readable: `{evidence_path.name}`",
        "",
        "## Status schema vs Agent Arena CLI emitter (STATIC source review)",
        "",
        f"- review_kind: `{schema.get('review_kind')}`",
        f"- runtime_live_manifest_verified: `{schema.get('runtime_live_manifest_verified')}`",
        f"- pointer: `{schema['arena_evidence_pointer']['source_path']}`",
        f"- reviewed_source_sha_literal: `{schema['arena_evidence_pointer']['reviewed_source_sha_literal']}`",
        f"- shared_status_categories: {', '.join(schema['shared_status_categories'])}",
        f"- arena_only: {', '.join(schema['arena_only_status_categories']) or '(none)'}",
        f"- template_launcher_only: {', '.join(schema['template_launcher_only_status_categories'])}",
        f"- invented_live_manifest: `{schema['invented_live_manifest']}`",
        "",
        "This is a local stub fixture, not live deployment proof. "
        "APP-012 owns live evidence. APP-013 owns Actions + second-repo/provider E2E.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-repo",
        type=Path,
        default=REPO_ROOT,
        help="Controlling repository used for git archive (default: this template).",
    )
    parser.add_argument(
        "--revision",
        help="Exact 40-char commit to export (default: HEAD).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for sanitized evidence JSON/Markdown (created if needed).",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        help="Optional durable work root (never deleted by this tool).",
    )
    parser.add_argument(
        "--skip-npm",
        action="store_true",
        help="Skip npm ci/lint/type-check/build (contract-only / incomplete; not full acceptance).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        manifest = run_local_rehearsal(
            source_repo=args.source_repo.resolve(),
            output_dir=args.output_dir,
            revision=args.revision,
            work_root=args.work_root,
            skip_npm=args.skip_npm,
        )
    except RehearsalError as exc:
        print(json.dumps({
            "provider_mode": PROVIDER_MODE,
            "live_cloud_rehearsal": LIVE_CLOUD_REHEARSAL,
            "status": "rehearsal_failed",
            "error": str(exc),
        }, sort_keys=True))
        return 1

    status = manifest.get("acceptance_status", "local_rehearsal_incomplete")
    build = manifest["second_app_build"]
    print(json.dumps({
        "provider_mode": manifest["provider_mode"],
        "live_cloud_rehearsal": manifest["live_cloud_rehearsal"],
        "status": status,
        "acceptance_complete": manifest.get("acceptance_complete"),
        "build_executed": build.get("build_executed"),
        "source_revision": manifest["snapshot"]["source_revision"],
        "app_revision": manifest["snapshot"]["app_revision"],
        "second_app_build_exit_code": build["exit_code"],
        "unchanged_noop": manifest["launcher_scenarios"]["unchanged_noop"],
        "operator_step_count": manifest["operator_step_count"],
        "elapsed_seconds_total": manifest["elapsed_seconds_total"],
        "node_version": (manifest.get("runtime") or {}).get("node_version"),
        "npm_version": (manifest.get("runtime") or {}).get("npm_version"),
        "evidence_json": manifest["evidence_json"],
    }, sort_keys=True))

    if not manifest["launcher_scenarios"]["unchanged_noop"]:
        return 1
    if args.skip_npm:
        # Documented partial exit: contract-only path is incomplete but exit 0.
        return 0
    if not build.get("build_executed"):
        return 1
    if build.get("exit_code") not in (0,):
        return 1
    if not manifest.get("acceptance_complete"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
