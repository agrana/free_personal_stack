#!/usr/bin/env python3
"""APP-006 deployment verification and health probe tests (fixture values only)."""

from __future__ import annotations

import http.server
import importlib.util
import io
import json
import os
import socket
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError

REPO_ROOT = Path(__file__).resolve().parents[1]

# Fixture-only identities — never real credentials or production hosts.
FIX_HOSTNAME = "arena.example.com"
FIX_REVISION = "abcdef0123456789abcdef0123456789abcdef01"
FIX_TOKEN = "vct_fixture_token_not_real_cccccccc"
FIX_SECRET_FRAGMENT = "supersecretpass"


def _load_module():
    path = REPO_ROOT / "scripts" / "verify-deployment.py"
    spec = importlib.util.spec_from_file_location("verify_deployment", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load verify-deployment from {path}")
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    payload: Dict[str, Any] = {}
    status_code: int = 200
    redirect_to: Optional[str] = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.redirect_to)
            self.end_headers()
            return
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(
    payload: Dict[str, Any],
    *,
    status_code: int = 200,
    redirect_to: Optional[str] = None,
) -> Tuple[http.server.HTTPServer, str, threading.Thread]:
    port = _free_loopback_port()
    handler = type(
        "BoundHealth",
        (_HealthHandler,),
        {
            "payload": payload,
            "status_code": status_code,
            "redirect_to": redirect_to,
        },
    )
    server = http.server.HTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}/api/health", thread


def _shutdown(server: http.server.HTTPServer) -> None:
    server.shutdown()
    server.server_close()


def _assert_no_secret_leak(text: str) -> None:
    for fragment in (FIX_TOKEN, FIX_SECRET_FRAGMENT, "Authorization:"):
        if fragment and fragment in text:
            raise AssertionError("secret-like value leaked into output")


class DeploymentVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_module()

    def _run_verify(self, **kwargs: Any) -> Tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = self.mod.verify_deployment(**kwargs)
        out = stdout.getvalue()
        err = stderr.getvalue()
        _assert_no_secret_leak(out + err)
        return int(code), out, err

    def test_health_route_source_allowlists_public_fields_only(self) -> None:
        health_path = REPO_ROOT / "app" / "api" / "health" / "route.ts"
        self.assertTrue(health_path.is_file(), "Next.js health route must exist at app/api/health/route.ts")
        source = health_path.read_text(encoding="utf-8")
        for forbidden in (
            "Object.keys(process.env)",
            "JSON.stringify(process.env)",
            "SUPABASE_SERVICE_ROLE",
            "VERCEL_API_TOKEN",
            "DATABASE_URL",
            "git config",
            "os.environ",
        ):
            self.assertNotIn(forbidden, source)
        # Immutable revision: Vercel SHA primary; explicit local test fallback only.
        # Must not prefer arbitrary NEXT_PUBLIC revision over the platform SHA.
        self.assertIn("VERCEL_GIT_COMMIT_SHA", source)
        self.assertIn("APP006_LOCAL_TEST_REVISION", source)
        self.assertNotIn("NEXT_PUBLIC_DEPLOYMENT_REVISION", source)
        self.assertRegex(source, r"force-dynamic|revalidate\s*=\s*0|force-no-store")
        self.assertIn("not_ready", source)
        self.assertIn("ready", source)
        self.assertIn("revision", source)
        self.assertRegex(
            source,
            r"liveness|process liveness|not database|not .*provider",
            msg="ready must be labelled as app process liveness",
        )
        self.assertNotRegex(
            source,
            r"process\.env(?!\.(VERCEL_GIT_COMMIT_SHA|APP006_LOCAL_TEST_REVISION)\b)",
        )

    def test_local_loopback_ready_revision_match_succeeds(self) -> None:
        payload = {
            "status": "ready",
            "revision": FIX_REVISION,
            "service": "app",
        }
        server, url, _thread = _serve(payload)
        try:
            code, out, err = self._run_verify(
                url=url,
                expected_hostname="127.0.0.1",
                expected_revision=FIX_REVISION,
                allow_loopback_http=True,
                require_browser_evidence=False,
            )
            self.assertEqual(code, 0, msg=f"out={out!r} err={err!r}")
            self.assertIn("http_probe", out.lower() + err.lower())
        finally:
            _shutdown(server)

    def test_hostname_resolving_to_private_ip_rejected(self) -> None:
        """SSRF: explicit host must not resolve to private/link-local addresses."""

        def fake_resolve(_host: str):
            return ["10.0.0.42"]

        code, out, err = self._run_verify(
            url=f"https://{FIX_HOSTNAME}/api/health",
            expected_hostname=FIX_HOSTNAME,
            expected_revision=FIX_REVISION,
            allow_loopback_http=False,
            require_browser_evidence=False,
            resolve_addresses=fake_resolve,
            transport=lambda **_k: (_ for _ in ()).throw(
                AssertionError("must not probe after private resolution")
            ),
        )
        self.assertNotEqual(code, 0)
        _assert_no_secret_leak(out + err)

    def test_dns_resolution_failure_fails_closed(self) -> None:
        def boom(_host: str):
            raise OSError("simulated resolution failure")

        code, out, err = self._run_verify(
            url=f"https://{FIX_HOSTNAME}/api/health",
            expected_hostname=FIX_HOSTNAME,
            expected_revision=FIX_REVISION,
            allow_loopback_http=False,
            require_browser_evidence=False,
            resolve_addresses=boom,
            transport=lambda **_k: (_ for _ in ()).throw(
                AssertionError("must not probe after DNS failure")
            ),
        )
        self.assertNotEqual(code, 0)
        _assert_no_secret_leak(out + err)

    def test_wrong_revision_fails_without_body_logging(self) -> None:
        payload = {
            "status": "ready",
            "revision": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            "service": "app",
            "leak": FIX_TOKEN,
        }
        server, url, _thread = _serve(payload)
        try:
            code, out, err = self._run_verify(
                url=url,
                expected_hostname="127.0.0.1",
                expected_revision=FIX_REVISION,
                allow_loopback_http=True,
                require_browser_evidence=False,
            )
            self.assertNotEqual(code, 0)
            combined = out + err
            self.assertNotIn(FIX_TOKEN, combined)
            self.assertNotIn("/api/health", combined)
            self.assertNotIn("deadbeef", combined)
        finally:
            _shutdown(server)

    def test_stale_or_error_status_fails(self) -> None:
        for status in ("stale", "error", "starting"):
            payload = {
                "status": status,
                "revision": FIX_REVISION,
                "service": "app",
            }
            server, url, _thread = _serve(payload)
            try:
                code, _out, _err = self._run_verify(
                    url=url,
                    expected_hostname="127.0.0.1",
                    expected_revision=FIX_REVISION,
                    allow_loopback_http=True,
                    require_browser_evidence=False,
                )
                self.assertNotEqual(code, 0, msg=f"status={status}")
            finally:
                _shutdown(server)

    def test_invalid_json_fails_safely(self) -> None:
        port = _free_loopback_port()

        class BadHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def do_GET(self) -> None:  # noqa: N802
                body = b"not-json{"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = http.server.HTTPServer(("127.0.0.1", port), BadHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            code, out, err = self._run_verify(
                url=f"http://127.0.0.1:{port}/api/health",
                expected_hostname="127.0.0.1",
                expected_revision=FIX_REVISION,
                allow_loopback_http=True,
                require_browser_evidence=False,
            )
            self.assertNotEqual(code, 0)
            _assert_no_secret_leak(out + err)
        finally:
            _shutdown(server)

    def test_extra_health_fields_rejected(self) -> None:
        payload = {
            "status": "ready",
            "revision": FIX_REVISION,
            "service": "app",
            "env": {"TOKEN": FIX_TOKEN},
        }
        server, url, _thread = _serve(payload)
        try:
            code, out, err = self._run_verify(
                url=url,
                expected_hostname="127.0.0.1",
                expected_revision=FIX_REVISION,
                allow_loopback_http=True,
                require_browser_evidence=False,
            )
            self.assertNotEqual(code, 0)
            self.assertNotIn(FIX_TOKEN, out + err)
        finally:
            _shutdown(server)

    def test_wrong_hostname_rejected(self) -> None:
        code, _out, _err = self._run_verify(
            url=f"https://{FIX_HOSTNAME}/api/health",
            expected_hostname="other.example.com",
            expected_revision=FIX_REVISION,
            allow_loopback_http=False,
            require_browser_evidence=False,
            transport=lambda **_k: (_ for _ in ()).throw(
                AssertionError("must not probe when hostname mismatches")
            ),
        )
        self.assertNotEqual(code, 0)

    def test_userinfo_and_metacharacters_rejected(self) -> None:
        for bad in (
            f"https://user:{FIX_SECRET_FRAGMENT}@{FIX_HOSTNAME}/api/health",
            f"https://{FIX_HOSTNAME}/api/health;curl evil.example.com",
            f"https://{FIX_HOSTNAME}/api/health`id`",
            f"https://{FIX_HOSTNAME}/api/health$(id)",
        ):
            code, out, err = self._run_verify(
                url=bad,
                expected_hostname=FIX_HOSTNAME,
                expected_revision=FIX_REVISION,
                allow_loopback_http=False,
                require_browser_evidence=False,
            )
            self.assertNotEqual(code, 0, msg=bad)
            _assert_no_secret_leak(out + err)

    def test_private_link_local_production_urls_rejected(self) -> None:
        for bad_host in ("10.0.0.1", "192.168.1.1", "172.16.0.5", "169.254.1.1", "::1"):
            code, _out, _err = self._run_verify(
                url=f"https://{bad_host}/api/health",
                expected_hostname=bad_host,
                expected_revision=FIX_REVISION,
                allow_loopback_http=False,
                require_browser_evidence=False,
            )
            self.assertNotEqual(code, 0, msg=bad_host)

    def test_http_non_loopback_rejected(self) -> None:
        code, _out, _err = self._run_verify(
            url=f"http://{FIX_HOSTNAME}/api/health",
            expected_hostname=FIX_HOSTNAME,
            expected_revision=FIX_REVISION,
            allow_loopback_http=True,
            require_browser_evidence=False,
        )
        self.assertNotEqual(code, 0)

    def test_redirect_to_arbitrary_domain_fails(self) -> None:
        payload = {
            "status": "ready",
            "revision": FIX_REVISION,
            "service": "app",
        }
        server, url, _thread = _serve(
            payload, redirect_to="https://evil.example.com/steal"
        )
        try:
            code, out, err = self._run_verify(
                url=url,
                expected_hostname="127.0.0.1",
                expected_revision=FIX_REVISION,
                allow_loopback_http=True,
                require_browser_evidence=False,
            )
            self.assertNotEqual(code, 0)
            self.assertNotIn("evil.example.com", out + err)
        finally:
            _shutdown(server)

    def test_production_evidence_absent_fails_closed(self) -> None:
        code, out, err = self._run_verify(
            url=None,
            expected_hostname=FIX_HOSTNAME,
            expected_revision=FIX_REVISION,
            allow_loopback_http=False,
            require_browser_evidence=True,
        )
        self.assertNotEqual(code, 0)
        combined = (out + err).lower()
        self.assertTrue(
            "browser" in combined or "evidence" in combined or "absent" in combined
        )

    def test_http_probe_is_not_browser_verification(self) -> None:
        payload = {
            "status": "ready",
            "revision": FIX_REVISION,
            "service": "app",
        }
        server, url, _thread = _serve(payload)
        try:
            result = self.mod.verify_deployment(
                url=url,
                expected_hostname="127.0.0.1",
                expected_revision=FIX_REVISION,
                allow_loopback_http=True,
                require_browser_evidence=False,
                return_report=True,
            )
            self.assertIsInstance(result, dict)
            self.assertEqual(result.get("http_probe"), "passed")
            self.assertIn(result.get("browser_verification"), ("pending", "skipped", "absent"))
            self.assertNotEqual(result.get("browser_verification"), "passed")
        finally:
            _shutdown(server)

    def test_browser_secret_exclusion_via_fixture_artifact_not_production_proof(
        self,
    ) -> None:
        """Scan a real on-disk fixture artifact; never treat fixture success as prod proof."""
        fixture = (
            REPO_ROOT
            / "tests"
            / "fixtures"
            / "secrets_preflight"
            / "browser_static_sample.js"
        )
        self.assertTrue(fixture.is_file(), "expected APP-004 browser fixture artifact")
        # Load secrets-preflight without executing live probes.
        path = REPO_ROOT / "scripts" / "secrets-preflight.py"
        spec = importlib.util.spec_from_file_location("secrets_preflight_app006", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("unable to load secrets-preflight")
        pref = importlib.util.module_from_spec(spec)
        import sys

        sys.modules[spec.name] = pref
        spec.loader.exec_module(pref)

        # Canary server-only value that must NOT appear in the static fixture.
        canary = "srv_fixture_canary_not_in_browser_bundle_zzzz"
        source = pref.CallerProvidedEnvSource(
            {
                "SUPABASE_SERVICE_ROLE_KEY": canary,
                "SUPABASE_DATABASE_PASSWORD": "db_fixture_unused_yyyy",
            }
        )
        errors: list = []
        scan = pref._scan_browser_artifacts(
            [str(fixture)],
            source=source,
            evidence_kind="static-fixture",
            errors=errors,
        )
        self.assertEqual(scan.get("status"), "ok")
        self.assertFalse(scan.get("claimsFullProductionBuild", True))
        self.assertEqual(scan.get("evidenceKind"), "static-fixture")
        self.assertNotEqual(scan.get("evidenceKind"), "built-bundle")
        self.assertEqual(scan.get("scanScope"), "supplied-artifacts-only")
        blob = json.dumps(scan) + "".join(str(e) for e in errors)
        self.assertNotIn(canary, blob)
        # Fixture artifact success is scoped evidence only — not a full production build claim.
        self.assertIn("does not claim a complete", str(scan.get("note", "")).lower())

    def test_shell_wrapper_exists(self) -> None:
        wrapper = REPO_ROOT / "scripts" / "verify-deployment.sh"
        self.assertTrue(wrapper.is_file())
        text = wrapper.read_text(encoding="utf-8")
        self.assertIn("verify-deployment.py", text)

    def test_package_json_test_is_not_fake_echo(self) -> None:
        pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        test_script = pkg["scripts"]["test"]
        self.assertNotIn('echo "No tests specified yet"', test_script)
        self.assertNotRegex(test_script, r"exit 0\s*$")
        lowered = test_script.lower()
        self.assertTrue(
            "unittest" in lowered or "pytest" in lowered or "test" in lowered,
            f"expected real test runner, got {test_script!r}",
        )

    def test_build_health_payload_helper_matches_allowlist(self) -> None:
        # Prefer a pure helper if exported; otherwise parse route constants via module.
        if hasattr(self.mod, "build_health_payload"):
            payload = self.mod.build_health_payload(revision=FIX_REVISION)
            self.assertEqual(set(payload.keys()), {"status", "revision", "service"})
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["revision"], FIX_REVISION)
            self.assertEqual(payload["service"], "app")


class HealthRouteTypescriptShapeTests(unittest.TestCase):
    def test_route_exports_get_handler(self) -> None:
        source = (REPO_ROOT / "app" / "api" / "health" / "route.ts").read_text(
            encoding="utf-8"
        )
        self.assertRegex(source, r"export\s+async\s+function\s+GET\b")
        self.assertIn("dynamic", source)


class VerifyUrlAndTimeoutHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_module()

    def _run(self, **kwargs: Any) -> Tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = self.mod.verify_deployment(**kwargs)
        out = stdout.getvalue()
        err = stderr.getvalue()
        _assert_no_secret_leak(out + err)
        self.assertNotIn("Traceback", out + err)
        return int(code), out, err

    def test_invalid_port_does_not_raise_valueerror(self) -> None:
        code, out, err = self._run(
            url="https://example.com:bad/api/health",
            expected_hostname="example.com",
            expected_revision=FIX_REVISION,
            allow_loopback_http=False,
        )
        self.assertNotEqual(code, 0)
        combined = out + err
        self.assertNotIn("ValueError", combined)
        self.assertNotIn("bad", combined)
        self.assertTrue(
            "verify.port_rejected" in combined or "verify.url_invalid" in combined
        )

    def test_production_requires_exact_health_path_and_port_443(self) -> None:
        for bad in (
            f"https://{FIX_HOSTNAME}/",
            f"https://{FIX_HOSTNAME}/health",
            f"https://{FIX_HOSTNAME}:8443/api/health",
            f"https://{FIX_HOSTNAME}/api/health/extra",
        ):
            code, out, err = self._run(
                url=bad,
                expected_hostname=FIX_HOSTNAME,
                expected_revision=FIX_REVISION,
                allow_loopback_http=False,
                transport=lambda **_k: (_ for _ in ()).throw(
                    AssertionError("must not probe invalid production URL")
                ),
            )
            self.assertNotEqual(code, 0, msg=bad)
            _assert_no_secret_leak(out + err)

    def test_ipv6_literal_formats_without_crash(self) -> None:
        payload = {
            "status": "ready",
            "revision": FIX_REVISION,
            "service": "app",
        }
        # Serve on IPv4 loopback; exercise IPv6 literal URL parsing/formatting path.
        code, out, err = self._run(
            url="https://[2001:db8::1]/api/health",
            expected_hostname="2001:db8::1",
            expected_revision=FIX_REVISION,
            allow_loopback_http=False,
            transport=lambda **_k: (_ for _ in ()).throw(
                AssertionError("documentation address must not be probed")
            ),
        )
        # 2001:db8::/32 is documentation — classified non_public / rejected.
        self.assertNotEqual(code, 0)
        _assert_no_secret_leak(out + err)
        self.assertNotIn("Traceback", out + err)
        _ = payload

    def test_explicit_loopback_ipv6_http_allowed(self) -> None:
        payload = {
            "status": "ready",
            "revision": FIX_REVISION,
            "service": "app",
        }
        port = _free_loopback_port()
        # Dual-stack bind may not always expose ::1; pin via custom transport.
        code, out, err = self._run(
            url=f"http://[::1]:{port}/api/health",
            expected_hostname="::1",
            expected_revision=FIX_REVISION,
            allow_loopback_http=True,
            transport=lambda **_k: {
                "status": 200,
                "body": json.dumps(payload).encode("utf-8"),
            },
        )
        self.assertEqual(code, 0, msg=f"out={out!r} err={err!r}")

    def test_hostname_resolving_to_loopback_rejected_even_with_local_flag(self) -> None:
        def fake_resolve(_host: str):
            return ["127.0.0.1"]

        code, out, err = self._run(
            url=f"https://{FIX_HOSTNAME}/api/health",
            expected_hostname=FIX_HOSTNAME,
            expected_revision=FIX_REVISION,
            allow_loopback_http=True,
            resolve_addresses=fake_resolve,
            transport=lambda **_k: (_ for _ in ()).throw(
                AssertionError("must not probe rebinding hostname")
            ),
        )
        self.assertNotEqual(code, 0)
        self.assertIn("verify.loopback_policy_rejected", out + err)

    def test_non_finite_timeout_rejected(self) -> None:
        for bad in (float("nan"), float("inf"), -1.0, 0, True, "5"):
            code, out, err = self._run(
                url=f"https://{FIX_HOSTNAME}/api/health",
                expected_hostname=FIX_HOSTNAME,
                expected_revision=FIX_REVISION,
                timeout=bad,  # type: ignore[arg-type]
            )
            self.assertNotEqual(code, 0, msg=repr(bad))
            self.assertIn("verify.timeout_invalid", out + err)

    def test_malformed_transport_types_fail_without_traceback(self) -> None:
        cases = (
            lambda **_k: "not-a-mapping",
            lambda **_k: {"status": "200", "body": b"{}"},
            lambda **_k: {"status": 200, "body": {"nested": True}},
            lambda **_k: {"status": 200},
        )
        for transport in cases:
            code, out, err = self._run(
                url=f"https://{FIX_HOSTNAME}/api/health",
                expected_hostname=FIX_HOSTNAME,
                expected_revision=FIX_REVISION,
                resolve_addresses=lambda _h: ["8.8.8.8"],
                transport=transport,
            )
            self.assertNotEqual(code, 0)
            self.assertNotIn("Traceback", out + err)
            self.assertNotIn(FIX_HOSTNAME, out + err)

    def test_mixed_public_and_private_addresses_rejected(self) -> None:
        def fake_resolve(_host: str):
            # 8.8.8.8 is globally routable; 10.0.0.5 is private — mixed set.
            return ["8.8.8.8", "10.0.0.5"]

        code, out, err = self._run(
            url=f"https://{FIX_HOSTNAME}/api/health",
            expected_hostname=FIX_HOSTNAME,
            expected_revision=FIX_REVISION,
            resolve_addresses=fake_resolve,
            transport=lambda **_k: (_ for _ in ()).throw(
                AssertionError("must not probe mixed addresses")
            ),
        )
        self.assertNotEqual(code, 0)
        self.assertIn("verify.mixed_address_rejected", out + err)


class ConnectionPinningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_module()

    def test_default_transport_connects_to_pinned_ip_not_hostname_resolution(self) -> None:
        """Connection must use validated addresses; getaddrinfo must not re-resolve."""
        payload = {
            "status": "ready",
            "revision": FIX_REVISION,
            "service": "app",
        }
        server, url, _thread = _serve(payload)
        try:
            # url is http://127.0.0.1:PORT/api/health — use hostname form with pin.
            parsed_port = int(url.rsplit(":", 1)[1].split("/", 1)[0])
            resolve_calls = {"n": 0}

            def resolve(_host: str):
                resolve_calls["n"] += 1
                return ["127.0.0.1"]

            real_getaddrinfo = socket.getaddrinfo
            re_resolve = {"n": 0}

            def guarded_getaddrinfo(*args: Any, **kwargs: Any):
                host = args[0] if args else kwargs.get("host")
                if host == FIX_HOSTNAME:
                    re_resolve["n"] += 1
                    raise AssertionError("transport must not re-resolve hostname")
                return real_getaddrinfo(*args, **kwargs)

            target_err, target = self.mod._parse_and_check_target(
                f"http://{FIX_HOSTNAME}:{parsed_port}/api/health",
                expected_hostname=FIX_HOSTNAME,
                allow_loopback_http=True,
                resolve_addresses=resolve,
            )
            # Hostname is not explicit loopback → loopback policy rejects even with flag.
            self.assertIsNotNone(target_err)
            self.assertIsNone(target)

            # Pin via explicit loopback host + default transport (no proxy / no re-resolve).
            os_env_proxy = {
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "http_proxy": "http://127.0.0.1:9",
                "https_proxy": "http://127.0.0.1:9",
                "ALL_PROXY": "http://127.0.0.1:9",
                "all_proxy": "http://127.0.0.1:9",
            }
            saved = {k: os.environ.get(k) for k in os_env_proxy}
            try:
                os.environ.update(os_env_proxy)
                socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = self.mod.verify_deployment(
                        url=url,
                        expected_hostname="127.0.0.1",
                        expected_revision=FIX_REVISION,
                        allow_loopback_http=True,
                    )
                self.assertEqual(code, 0, msg=stderr.getvalue())
                self.assertEqual(re_resolve["n"], 0)
            finally:
                socket.getaddrinfo = real_getaddrinfo  # type: ignore[assignment]
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        finally:
            _shutdown(server)

    def test_docs_mention_dns_timeout_limitation(self) -> None:
        docs = (REPO_ROOT / "docs" / "app-deployment.md").read_text(encoding="utf-8")
        source = (REPO_ROOT / "scripts" / "verify-deployment.py").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            docs.lower(),
            r"getaddrinfo|dns.*timeout|cannot be cancell",
        )
        self.assertIn("getaddrinfo", source)
        self.assertIn("cannot be cancelled", source.lower() or source)


class HealthRouteHttpSmokePresenceTests(unittest.TestCase):
    def test_health_http_smoke_script_exists(self) -> None:
        path = REPO_ROOT / "scripts" / "health-route-http-smoke.py"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("APP006_LOCAL_TEST_REVISION", text)
        self.assertIn("/api/health", text)
        self.assertIn("verify_deployment", text)
        self.assertNotIn("build_health_payload", text)


class _RawOneShotHTTPServer:
    """Accept one TCP client and write a fixed raw HTTP response (socket-level)."""

    def __init__(self, response: bytes) -> None:
        self._response = response
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = int(self._sock.getsockname()[1])
        self.url = f"http://127.0.0.1:{self.port}/api/health"
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._error: Optional[BaseException] = None

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        try:
            self._sock.settimeout(5.0)
            conn, _addr = self._sock.accept()
            with conn:
                conn.settimeout(5.0)
                try:
                    conn.recv(65536)
                except OSError:
                    pass
                conn.sendall(self._response)
        except BaseException as exc:  # noqa: BLE001 — capture for assertion
            self._error = exc
        finally:
            try:
                self._sock.close()
            except OSError:
                pass

    def join(self, timeout: float = 5.0) -> None:
        self._thread.join(timeout=timeout)


def _chunked_http_response(body: bytes, *, extra_headers: Tuple[bytes, ...] = ()) -> bytes:
    hex_len = f"{len(body):x}".encode("ascii")
    header_lines = [
        b"HTTP/1.1 200 OK",
        b"Transfer-Encoding: chunked",
        b"Content-Type: application/json",
        b"Connection: close",
        *extra_headers,
        b"",
        b"",
    ]
    head = b"\r\n".join(header_lines[:-1]) + b"\r\n"
    return head + hex_len + b"\r\n" + body + b"\r\n" + b"0\r\n\r\n"


class DefaultTransportFramingTests(unittest.TestCase):
    """Regression: _default_transport must decode chunked framing (not inject maps)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_module()

    def _run_default(self, url: str) -> Tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = self.mod.verify_deployment(
                url=url,
                expected_hostname="127.0.0.1",
                expected_revision=FIX_REVISION,
                allow_loopback_http=True,
                require_browser_evidence=False,
                timeout=3.0,
            )
        out = stdout.getvalue()
        err = stderr.getvalue()
        _assert_no_secret_leak(out + err)
        self.assertNotIn("Traceback", out + err)
        return int(code), out, err

    def test_chunked_ready_body_via_default_transport_succeeds(self) -> None:
        payload = {
            "status": "ready",
            "revision": FIX_REVISION,
            "service": "app",
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        server = _RawOneShotHTTPServer(_chunked_http_response(body))
        server.start()
        try:
            code, out, err = self._run_default(server.url)
            self.assertEqual(code, 0, msg=f"out={out!r} err={err!r}")
            self.assertIn("http_probe=passed", out)
        finally:
            server.join()

    def test_content_length_ready_body_via_default_transport_succeeds(self) -> None:
        payload = {
            "status": "ready",
            "revision": FIX_REVISION,
            "service": "app",
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: "
            + str(len(body)).encode("ascii")
            + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            + body
        )
        server = _RawOneShotHTTPServer(raw)
        server.start()
        try:
            code, out, err = self._run_default(server.url)
            self.assertEqual(code, 0, msg=f"out={out!r} err={err!r}")
        finally:
            server.join()

    def test_truncated_chunk_fails_fixed_code_without_raw_input(self) -> None:
        # Declare 20-byte chunk but close after 4 bytes of payload.
        raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"Content-Type: application/json\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            b"14\r\n"
            b"abcd"
        )
        server = _RawOneShotHTTPServer(raw)
        server.start()
        try:
            code, out, err = self._run_default(server.url)
            self.assertNotEqual(code, 0)
            combined = out + err
            self.assertIn(self.mod.ERR_TRANSPORT, combined)
            self.assertNotIn("abcd", combined)
            self.assertNotIn("14\r\n", combined)
            self.assertNotIn(FIX_TOKEN, combined)
        finally:
            server.join()

    def test_malformed_chunk_size_fails_fixed_code_without_raw_input(self) -> None:
        raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"Content-Type: application/json\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            b"not-hex\r\n"
            b"{}\r\n"
            b"0\r\n\r\n"
        )
        server = _RawOneShotHTTPServer(raw)
        server.start()
        try:
            code, out, err = self._run_default(server.url)
            self.assertNotEqual(code, 0)
            combined = out + err
            self.assertIn(self.mod.ERR_TRANSPORT, combined)
            self.assertNotIn("not-hex", combined)
            self.assertNotIn("{}\r\n", combined)
        finally:
            server.join()

    def test_oversized_chunked_body_fails_body_too_large(self) -> None:
        max_bytes = int(self.mod.MAX_BODY_BYTES)
        body = b"x" * (max_bytes + 64)
        server = _RawOneShotHTTPServer(_chunked_http_response(body))
        server.start()
        try:
            code, out, err = self._run_default(server.url)
            self.assertNotEqual(code, 0)
            combined = out + err
            self.assertIn(self.mod.ERR_BODY_SIZE, combined)
            self.assertNotIn("xxxxx", combined)
            self.assertNotIn(str(max_bytes + 64), combined)
        finally:
            server.join()

    def test_oversized_content_length_fails_body_too_large(self) -> None:
        max_bytes = int(self.mod.MAX_BODY_BYTES)
        body = b"y" * (max_bytes + 32)
        raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: "
            + str(len(body)).encode("ascii")
            + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            + body
        )
        server = _RawOneShotHTTPServer(raw)
        server.start()
        try:
            code, out, err = self._run_default(server.url)
            self.assertNotEqual(code, 0)
            combined = out + err
            self.assertIn(self.mod.ERR_BODY_SIZE, combined)
            self.assertNotIn("yyyyy", combined)
        finally:
            server.join()


if __name__ == "__main__":
    unittest.main()
