#!/usr/bin/env python3
"""HTTP smoke against the real Next.js /api/health route (build + start).

Requires an existing `.next` build. Sets APP006_LOCAL_TEST_REVISION (server-side)
and asserts allowlisted JSON. Does not use Python payload helpers as the SUT.

After route checks, runs verify_deployment against the same explicit loopback URL
and exact FIX_REVISION so chunked framing bugs in the probe transport are caught
(urllib alone hides Transfer-Encoding issues).
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIX_REVISION = "abcdef0123456789abcdef0123456789abcdef01"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _load_verify_deployment():
    path = REPO_ROOT / "scripts" / "verify-deployment.py"
    spec = importlib.util.spec_from_file_location("verify_deployment_smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load verify-deployment")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    next_dir = REPO_ROOT / ".next"
    if not next_dir.is_dir():
        print("FAIL health.http_smoke_missing_build", file=sys.stderr)
        return 1

    next_bin = REPO_ROOT / "node_modules" / ".bin" / "next"
    if not next_bin.is_file():
        print("FAIL health.http_smoke_missing_next", file=sys.stderr)
        return 1

    port = _free_port()
    env = os.environ.copy()
    # Do not rely on NEXT_PUBLIC_DEPLOYMENT_REVISION for actual revision.
    env.pop("NEXT_PUBLIC_DEPLOYMENT_REVISION", None)
    env["APP006_LOCAL_TEST_REVISION"] = FIX_REVISION
    env["PORT"] = str(port)

    proc = subprocess.Popen(
        [str(next_bin), "start", "-H", "127.0.0.1", "-p", str(port)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        deadline = time.time() + 45
        last_err = "unreached"
        body = b""
        status = 0
        while time.time() < deadline:
            if proc.poll() is not None:
                print("FAIL health.http_smoke_server_exit", file=sys.stderr)
                return 1
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    status = int(resp.status)
                    body = resp.read(4096)
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_err = type(exc).__name__
                time.sleep(0.4)
        else:
            print(f"FAIL health.http_smoke_timeout:{last_err}", file=sys.stderr)
            return 1

        if status != 200:
            print("FAIL health.http_smoke_status", file=sys.stderr)
            return 1
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            print("FAIL health.http_smoke_json", file=sys.stderr)
            return 1
        if set(payload.keys()) != {"status", "revision", "service"}:
            print("FAIL health.http_smoke_schema", file=sys.stderr)
            return 1
        if payload.get("status") != "ready":
            print("FAIL health.http_smoke_not_ready", file=sys.stderr)
            return 1
        if payload.get("revision") != FIX_REVISION:
            print("FAIL health.http_smoke_revision", file=sys.stderr)
            return 1
        if payload.get("service") != "app":
            print("FAIL health.http_smoke_service", file=sys.stderr)
            return 1

        verify = _load_verify_deployment()
        code = verify.verify_deployment(
            url=url,
            expected_hostname="127.0.0.1",
            expected_revision=FIX_REVISION,
            allow_loopback_http=True,
            require_browser_evidence=False,
            timeout=5.0,
        )
        if int(code) != 0:
            print("FAIL health.http_smoke_verify_deployment", file=sys.stderr)
            return 1

        print("health_http_smoke=passed")
        return 0
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
