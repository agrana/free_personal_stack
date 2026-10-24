#!/usr/bin/env python3
"""APP-006 deployment smoke verification (HTTPS health probe).

Verifies an allowlisted hostname health endpoint for ready status and exact
revision match. Distinguishes HTTP probes from browser verification.

DNS is resolved once per request; the TCP/TLS connection is pinned to those
validated addresses (no urllib re-resolution, no environment proxies). TLS
certificate validation and SNI remain bound to the original hostname; the HTTP
Host header matches that hostname. Redirects are rejected.

stdlib getaddrinfo has no portable cancellation: resolution wait is bounded via
a worker-thread join, but a stuck resolver thread may linger until process exit.

Never logs response bodies, URL paths, tokens, or secret-like fragments.
Production evidence absent => fail closed. Does not authorize deploy/apply.
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union
from urllib.parse import urlparse

MAX_BODY_BYTES = 4096
MAX_HEADER_BYTES = 8192
# Chunk-size lines / CRLF overhead beyond decoded body (not a second body budget).
_MAX_WIRE_SLACK_BYTES = 2048
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 30.0
DEFAULT_DNS_TIMEOUT_SECONDS = 3.0
PRODUCTION_HTTPS_PORT = 443
PRODUCTION_HEALTH_PATH = "/api/health"

HEALTH_ALLOWLIST_KEYS = frozenset({"status", "revision", "service"})
READY_STATUS = "ready"
SERVICE_NAME = "app"

EXPLICIT_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Brackets omitted so IPv6 literals like https://[::1]/api/health parse; path is
# still constrained to the exact production health path.
SHELL_METACHAR_RE = re.compile(r"""[;|&$`<>(){}\\"'\n\r\t]""")
URL_USERINFO_RE = re.compile(r"://[^/\s]+:[^/@\s]+@")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

ERR_URL_INVALID = "verify.url_invalid"
ERR_URL_USERINFO = "verify.url_userinfo_rejected"
ERR_URL_METACHAR = "verify.url_metachar_rejected"
ERR_HOSTNAME_MISMATCH = "verify.hostname_mismatch"
ERR_SCHEME = "verify.scheme_rejected"
ERR_PRIVATE_TARGET = "verify.private_or_link_local_rejected"
ERR_DNS = "verify.dns_resolution_failed"
ERR_REDIRECT = "verify.redirect_rejected"
ERR_HTTP_STATUS = "verify.http_status"
ERR_BODY_SIZE = "verify.body_too_large"
ERR_JSON = "verify.invalid_json"
ERR_SCHEMA = "verify.schema_rejected"
ERR_STATUS = "verify.status_not_ready"
ERR_REVISION = "verify.revision_mismatch"
ERR_EVIDENCE = "verify.production_evidence_absent"
ERR_BROWSER = "verify.browser_evidence_absent"
ERR_TRANSPORT = "verify.transport_failed"
ERR_TIMEOUT = "verify.timeout_invalid"
ERR_PATH = "verify.path_rejected"
ERR_PORT = "verify.port_rejected"
ERR_LOOPBACK_POLICY = "verify.loopback_policy_rejected"
ERR_MIXED_ADDR = "verify.mixed_address_rejected"

TransportFn = Callable[..., Mapping[str, Any]]
ResolveFn = Callable[[str], Sequence[str]]


class ProbeFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProbeTarget:
    scheme: str
    host: str
    port: int
    path: str
    addresses: Tuple[str, ...]
    allow_loopback_http: bool

    @property
    def origin_url(self) -> str:
        return f"{self.scheme}://{_format_host_port(self.host, self.port)}{self.path}"


def build_health_payload(*, revision: str) -> Dict[str, str]:
    """Public health payload allowlist (shared shape with the Next.js route)."""
    return {
        "status": READY_STATUS,
        "revision": revision,
        "service": SERVICE_NAME,
    }


def _safe_print(message: str, *, stream=None) -> None:
    print(message, file=stream or sys.stdout)


def _is_explicit_loopback_host(host: str) -> bool:
    if host.casefold() in {h.casefold() for h in EXPLICIT_LOOPBACK_HOSTS}:
        return True
    try:
        return bool(ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return False


def _classify_ip(host: str) -> Optional[str]:
    """Return 'loopback', 'non_public', or 'public' if an IP literal; else None."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return None
    if addr.is_loopback:
        return "loopback"
    if (
        addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        return "non_public"
    return "public"


def _format_host_port(host: str, port: Optional[int]) -> str:
    display = host
    try:
        addr = ipaddress.ip_address(host)
        if isinstance(addr, ipaddress.IPv6Address):
            display = f"[{host}]"
    except ValueError:
        pass
    if port is None:
        return display
    return f"{display}:{port}"


def _validate_timeout(timeout: Any) -> Optional[str]:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return ERR_TIMEOUT
    value = float(timeout)
    if not math.isfinite(value) or value <= 0.0 or value > MAX_TIMEOUT_SECONDS:
        return ERR_TIMEOUT
    return None


def _default_resolve_addresses(host: str) -> List[str]:
    results: List[str] = []
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ProbeFailure(ERR_DNS) from exc
    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            results.append(str(sockaddr[0]))
    if not results:
        raise ProbeFailure(ERR_DNS)
    seen = set()
    ordered: List[str] = []
    for item in results:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _resolve_addresses_bounded(
    host: str,
    *,
    timeout: float,
    resolve_addresses: Optional[ResolveFn],
) -> List[str]:
    """Resolve with a join-bounded wait.

    Limitation: CPython getaddrinfo cannot be cancelled; after join timeout the
    worker may still run until the process exits. We fail closed on timeout.
    """
    resolver = resolve_addresses or _default_resolve_addresses
    if resolve_addresses is not None:
        try:
            return list(resolver(host))
        except ProbeFailure:
            raise
        except OSError as exc:
            raise ProbeFailure(ERR_DNS) from exc
        except Exception as exc:
            raise ProbeFailure(ERR_DNS) from exc

    box: Dict[str, Any] = {}

    def worker() -> None:
        try:
            box["addrs"] = list(resolver(host))
        except BaseException as exc:  # noqa: BLE001 — surface as DNS failure
            box["err"] = exc

    thread = threading.Thread(target=worker, name="app006-dns", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise ProbeFailure(ERR_DNS)
    if "err" in box:
        err = box["err"]
        if isinstance(err, ProbeFailure):
            raise err
        raise ProbeFailure(ERR_DNS) from err
    addrs = box.get("addrs")
    if not addrs:
        raise ProbeFailure(ERR_DNS)
    return list(addrs)


def _validate_url_string(url: str) -> Optional[str]:
    if not url or not isinstance(url, str):
        return ERR_URL_INVALID
    if CONTROL_CHAR_RE.search(url) or SHELL_METACHAR_RE.search(url):
        return ERR_URL_METACHAR
    if URL_USERINFO_RE.search(url):
        return ERR_URL_USERINFO
    if "://" not in url:
        return ERR_URL_INVALID
    return None


def _check_resolved_targets(
    addresses: Sequence[str],
    *,
    allow_loopback_http: bool,
    host_is_explicit_loopback: bool,
) -> Optional[str]:
    if not addresses:
        return ERR_DNS
    kinds: List[str] = []
    for raw in addresses:
        kind = _classify_ip(raw)
        if kind is None:
            return ERR_DNS
        kinds.append(kind)
    unique = set(kinds)
    if len(unique) > 1:
        return ERR_MIXED_ADDR
    kind = kinds[0]
    if kind == "public":
        return None
    if kind == "loopback":
        if allow_loopback_http and host_is_explicit_loopback:
            return None
        return ERR_LOOPBACK_POLICY if allow_loopback_http else ERR_PRIVATE_TARGET
    return ERR_PRIVATE_TARGET


def _safe_parsed_port(parsed: Any) -> Tuple[Optional[int], Optional[str]]:
    try:
        port = parsed.port
    except ValueError:
        return None, ERR_PORT
    return port, None


def _parse_and_check_target(
    url: str,
    *,
    expected_hostname: str,
    allow_loopback_http: bool,
    resolve_addresses: Optional[ResolveFn] = None,
    dns_timeout: float = DEFAULT_DNS_TIMEOUT_SECONDS,
) -> Tuple[Optional[str], Optional[ProbeTarget]]:
    err = _validate_url_string(url)
    if err:
        return err, None
    if not expected_hostname or not isinstance(expected_hostname, str):
        return ERR_URL_INVALID, None
    if CONTROL_CHAR_RE.search(expected_hostname) or SHELL_METACHAR_RE.search(
        expected_hostname
    ):
        return ERR_URL_METACHAR, None

    parsed = urlparse(url)
    if parsed.username or parsed.password:
        return ERR_URL_USERINFO, None
    host = parsed.hostname
    if not host or not isinstance(host, str):
        return ERR_URL_INVALID, None
    if host.casefold() != expected_hostname.casefold():
        return ERR_HOSTNAME_MISMATCH, None

    port, port_err = _safe_parsed_port(parsed)
    if port_err:
        return port_err, None

    path = parsed.path or ""
    if path != PRODUCTION_HEALTH_PATH:
        return ERR_PATH, None
    if parsed.query or parsed.fragment or parsed.params:
        return ERR_URL_INVALID, None
    if SHELL_METACHAR_RE.search(path) or CONTROL_CHAR_RE.search(path):
        return ERR_URL_METACHAR, None

    scheme = (parsed.scheme or "").lower()
    host_is_explicit_loopback = _is_explicit_loopback_host(host)
    literal_kind = _classify_ip(host)

    if scheme == "https":
        effective_port = PRODUCTION_HTTPS_PORT if port is None else int(port)
        if effective_port != PRODUCTION_HTTPS_PORT:
            return ERR_PORT, None
    elif scheme == "http":
        if not (allow_loopback_http and host_is_explicit_loopback):
            return ERR_SCHEME, None
        if port is None:
            return ERR_PORT, None
        effective_port = int(port)
        if effective_port < 1 or effective_port > 65535:
            return ERR_PORT, None
    else:
        return ERR_SCHEME, None

    if literal_kind is not None:
        addresses = [host]
        resolve_err = _check_resolved_targets(
            addresses,
            allow_loopback_http=allow_loopback_http,
            host_is_explicit_loopback=host_is_explicit_loopback,
        )
        if resolve_err:
            return resolve_err, None
    else:
        # Hostname: resolve once; reject private/link-local and non-explicit loopback.
        try:
            addresses = _resolve_addresses_bounded(
                host,
                timeout=dns_timeout,
                resolve_addresses=resolve_addresses,
            )
        except ProbeFailure as exc:
            return exc.code, None
        resolve_err = _check_resolved_targets(
            addresses,
            allow_loopback_http=allow_loopback_http,
            host_is_explicit_loopback=host_is_explicit_loopback,
        )
        if resolve_err:
            return resolve_err, None

    return None, ProbeTarget(
        scheme=scheme,
        host=host,
        port=effective_port,
        path=path,
        addresses=tuple(addresses),
        allow_loopback_http=allow_loopback_http,
    )


class _DeadlineBudgetFile:
    """Binary file wrapper: total request deadline + bounded wire reads.

    Used under http.client.HTTPResponse so Content-Length and chunked framing are
    decoded by the stdlib parser while we still fail closed on oversized or
    stalled responses.
    """

    def __init__(
        self,
        raw: Any,
        *,
        sock: socket.socket,
        budget: int,
        deadline: float,
    ) -> None:
        self._raw = raw
        self._sock = sock
        self._budget = int(budget)
        self._deadline = deadline
        self.closed = False

    def _arm_timeout(self) -> None:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeFailure(ERR_TRANSPORT)
        self._sock.settimeout(remaining)

    def _consume(self, data: bytes) -> bytes:
        self._budget -= len(data)
        if self._budget < 0:
            self._budget = 0
            raise ProbeFailure(ERR_BODY_SIZE)
        return data

    def read(self, amt: Optional[int] = None) -> bytes:
        self._arm_timeout()
        if self._budget <= 0:
            raise ProbeFailure(ERR_BODY_SIZE)
        if amt is None or amt < 0:
            take = self._budget
        else:
            take = min(int(amt), self._budget)
        try:
            data = self._raw.read(take)
        except (socket.timeout, TimeoutError, OSError) as exc:
            raise ProbeFailure(ERR_TRANSPORT) from exc
        return self._consume(data)

    def readline(self, size: Optional[int] = None) -> bytes:
        self._arm_timeout()
        if self._budget <= 0:
            raise ProbeFailure(ERR_BODY_SIZE)
        if size is None or size < 0:
            limit = self._budget
        else:
            limit = min(int(size), self._budget)
        try:
            data = self._raw.readline(limit)
        except (socket.timeout, TimeoutError, OSError) as exc:
            raise ProbeFailure(ERR_TRANSPORT) from exc
        data = self._consume(data)
        # Hit wire budget mid-line without a terminator ⇒ framing/size failure.
        if data and not data.endswith((b"\n", b"\r")) and self._budget <= 0:
            raise ProbeFailure(ERR_BODY_SIZE)
        return data

    def readinto(self, b: Any) -> int:
        self._arm_timeout()
        if self._budget <= 0:
            raise ProbeFailure(ERR_BODY_SIZE)
        mv = memoryview(b)
        take = min(len(mv), self._budget)
        try:
            data = self._raw.read(take)
        except (socket.timeout, TimeoutError, OSError) as exc:
            raise ProbeFailure(ERR_TRANSPORT) from exc
        self._consume(data)
        mv[: len(data)] = data
        return len(data)

    def close(self) -> None:
        self.closed = True
        try:
            self._raw.close()
        except Exception:
            pass

    def flush(self) -> None:
        return None


def _read_http_response(
    sock: socket.socket,
    *,
    max_bytes: int,
    deadline: float,
) -> Mapping[str, Any]:
    """Parse one HTTP response with stdlib framing (Content-Length + chunked)."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProbeFailure(ERR_TRANSPORT)
    sock.settimeout(remaining)

    wire_budget = MAX_HEADER_BYTES + int(max_bytes) + _MAX_WIRE_SLACK_BYTES
    response = http.client.HTTPResponse(sock, method="GET")
    underlying = response.fp
    if underlying is None:
        raise ProbeFailure(ERR_TRANSPORT)
    response.fp = _DeadlineBudgetFile(
        underlying,
        sock=sock,
        budget=wire_budget,
        deadline=deadline,
    )

    try:
        try:
            response.begin()
        except ProbeFailure:
            raise
        except (
            http.client.HTTPException,
            socket.timeout,
            TimeoutError,
            OSError,
            ValueError,
        ) as exc:
            raise ProbeFailure(ERR_TRANSPORT) from exc

        status = int(response.status)
        if status in {301, 302, 303, 307, 308}:
            raise ProbeFailure(ERR_REDIRECT)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeFailure(ERR_TRANSPORT)
        sock.settimeout(remaining)
        try:
            raw = response.read(int(max_bytes) + 1)
        except ProbeFailure:
            raise
        except (
            http.client.HTTPException,
            socket.timeout,
            TimeoutError,
            OSError,
            ValueError,
        ) as exc:
            raise ProbeFailure(ERR_TRANSPORT) from exc

        if len(raw) > int(max_bytes):
            raise ProbeFailure(ERR_BODY_SIZE)
        return {"status": status, "body": bytes(raw)}
    finally:
        try:
            response.close()
        except Exception:
            pass


def _connect_pinned(
    *,
    target: ProbeTarget,
    address: str,
    timeout: float,
) -> socket.socket:
    remaining = timeout
    if remaining <= 0:
        raise ProbeFailure(ERR_TRANSPORT)
    try:
        sock = socket.create_connection((address, target.port), timeout=remaining)
    except OSError as exc:
        raise ProbeFailure(ERR_TRANSPORT) from exc

    if target.scheme == "https":
        context = ssl.create_default_context()
        try:
            sock = context.wrap_socket(sock, server_hostname=target.host)
        except ssl.SSLError as exc:
            sock.close()
            raise ProbeFailure(ERR_TRANSPORT) from exc
        except OSError as exc:
            sock.close()
            raise ProbeFailure(ERR_TRANSPORT) from exc
    return sock


def _default_transport(
    *,
    target: ProbeTarget,
    timeout: float,
    max_bytes: int,
) -> Mapping[str, Any]:
    """Pin TCP to pre-validated addresses; preserve SNI/Host; ignore env proxies."""
    deadline = time.monotonic() + timeout
    last_error: Optional[BaseException] = None
    for address in target.addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock: Optional[socket.socket] = None
        try:
            sock = _connect_pinned(target=target, address=address, timeout=remaining)
            host_header = target.host
            try:
                if ipaddress.ip_address(target.host).version == 6:
                    host_header = f"[{target.host}]"
            except ValueError:
                pass
            if target.port not in (80, 443):
                request_host = f"{host_header}:{target.port}"
            else:
                request_host = host_header
            request = (
                f"GET {target.path} HTTP/1.1\r\n"
                f"Host: {request_host}\r\n"
                "Accept: application/json\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            sock.sendall(request)
            return _read_http_response(sock, max_bytes=max_bytes, deadline=deadline)
        except ProbeFailure:
            raise
        except (OSError, TimeoutError, socket.timeout) as exc:
            last_error = exc
            continue
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
    if last_error is not None:
        raise ProbeFailure(ERR_TRANSPORT) from last_error
    raise ProbeFailure(ERR_TRANSPORT)


def _coerce_response(response: Any) -> Mapping[str, Any]:
    if not isinstance(response, Mapping):
        raise ProbeFailure(ERR_TRANSPORT)
    if "status" not in response or "body" not in response:
        raise ProbeFailure(ERR_TRANSPORT)
    status = response.get("status")
    if isinstance(status, bool) or not isinstance(status, int):
        raise ProbeFailure(ERR_TRANSPORT)
    body = response.get("body")
    if not isinstance(body, (bytes, bytearray, str)):
        raise ProbeFailure(ERR_TRANSPORT)
    return response


def _validate_health_payload(
    payload: Any, *, expected_revision: str
) -> Optional[str]:
    if not isinstance(payload, dict):
        return ERR_SCHEMA
    if set(payload.keys()) != HEALTH_ALLOWLIST_KEYS:
        return ERR_SCHEMA
    status = payload.get("status")
    revision = payload.get("revision")
    service = payload.get("service")
    if not isinstance(status, str) or not isinstance(revision, str) or not isinstance(
        service, str
    ):
        return ERR_SCHEMA
    if service != SERVICE_NAME:
        return ERR_SCHEMA
    if status != READY_STATUS:
        return ERR_STATUS
    if revision != expected_revision:
        return ERR_REVISION
    return None


def verify_deployment(
    *,
    url: Optional[str],
    expected_hostname: str,
    expected_revision: str,
    allow_loopback_http: bool = False,
    require_browser_evidence: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    transport: Optional[TransportFn] = None,
    resolve_addresses: Optional[ResolveFn] = None,
    return_report: bool = False,
) -> Union[int, Dict[str, Any]]:
    """Probe health endpoint. Returns 0 on HTTP probe success, non-zero otherwise."""
    report: Dict[str, Any] = {
        "http_probe": "failed",
        "browser_verification": "pending",
        "error": None,
    }

    timeout_err = _validate_timeout(timeout)
    if timeout_err:
        report["error"] = timeout_err
        _safe_print(f"FAIL {timeout_err}", stream=sys.stderr)
        return report if return_report else 1

    if not url:
        report["error"] = ERR_EVIDENCE
        report["browser_verification"] = "absent"
        _safe_print(f"FAIL {ERR_EVIDENCE}", stream=sys.stderr)
        return report if return_report else 1

    if not expected_hostname or not expected_revision:
        report["error"] = ERR_EVIDENCE
        _safe_print(f"FAIL {ERR_EVIDENCE}", stream=sys.stderr)
        return report if return_report else 1
    if not isinstance(expected_revision, str) or not isinstance(expected_hostname, str):
        report["error"] = ERR_EVIDENCE
        _safe_print(f"FAIL {ERR_EVIDENCE}", stream=sys.stderr)
        return report if return_report else 1

    dns_timeout = min(float(timeout), DEFAULT_DNS_TIMEOUT_SECONDS)
    try:
        err, target = _parse_and_check_target(
            url,
            expected_hostname=expected_hostname,
            allow_loopback_http=allow_loopback_http,
            resolve_addresses=resolve_addresses,
            dns_timeout=dns_timeout,
        )
    except Exception:
        report["error"] = ERR_URL_INVALID
        _safe_print(f"FAIL {ERR_URL_INVALID}", stream=sys.stderr)
        return report if return_report else 1

    if err or target is None:
        report["error"] = err or ERR_URL_INVALID
        _safe_print(f"FAIL {report['error']}", stream=sys.stderr)
        return report if return_report else 1

    if transport is None:

        def _transport(**_kwargs: Any) -> Mapping[str, Any]:
            return _default_transport(
                target=target, timeout=float(timeout), max_bytes=MAX_BODY_BYTES
            )

        transport_fn: TransportFn = _transport
    else:
        transport_fn = transport

    try:
        response = transport_fn(
            url=target.origin_url,
            target=target,
            timeout=float(timeout),
            max_bytes=MAX_BODY_BYTES,
        )
        response = _coerce_response(response)
    except ProbeFailure as exc:
        report["error"] = exc.code
        _safe_print(f"FAIL {exc.code}", stream=sys.stderr)
        return report if return_report else 1
    except Exception:
        report["error"] = ERR_TRANSPORT
        _safe_print(f"FAIL {ERR_TRANSPORT}", stream=sys.stderr)
        return report if return_report else 1

    status = int(response["status"])
    if status in {301, 302, 303, 307, 308}:
        report["error"] = ERR_REDIRECT
        _safe_print(f"FAIL {ERR_REDIRECT}", stream=sys.stderr)
        return report if return_report else 1
    if status != 200:
        report["error"] = ERR_HTTP_STATUS
        _safe_print(f"FAIL {ERR_HTTP_STATUS}", stream=sys.stderr)
        return report if return_report else 1

    body = response.get("body", b"")
    if isinstance(body, str):
        raw = body.encode("utf-8", errors="replace")
    else:
        raw = bytes(body)
    if len(raw) > MAX_BODY_BYTES:
        report["error"] = ERR_BODY_SIZE
        _safe_print(f"FAIL {ERR_BODY_SIZE}", stream=sys.stderr)
        return report if return_report else 1

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        report["error"] = ERR_JSON
        _safe_print(f"FAIL {ERR_JSON}", stream=sys.stderr)
        return report if return_report else 1

    schema_err = _validate_health_payload(payload, expected_revision=expected_revision)
    if schema_err:
        report["error"] = schema_err
        _safe_print(f"FAIL {schema_err}", stream=sys.stderr)
        return report if return_report else 1

    report["http_probe"] = "passed"
    if require_browser_evidence:
        report["browser_verification"] = "absent"
        report["error"] = ERR_BROWSER
        _safe_print(f"FAIL {ERR_BROWSER}", stream=sys.stderr)
        _safe_print("http_probe=passed browser_verification=absent")
        return report if return_report else 1

    report["browser_verification"] = "pending"
    _safe_print("http_probe=passed browser_verification=pending")
    return report if return_report else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="APP-006 deployment health verification (HTTP probe only)."
    )
    parser.add_argument("--url", default=None, help="Health URL (https://hostname/api/health)")
    parser.add_argument("--expected-hostname", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument(
        "--allow-loopback-http",
        action="store_true",
        help="Permit http:// only for explicit loopback hosts (127.0.0.1/::1/localhost)",
    )
    parser.add_argument(
        "--require-browser-evidence",
        action="store_true",
        help="Fail closed when browser verification evidence is required but absent",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    try:
        result = verify_deployment(
            url=args.url,
            expected_hostname=args.expected_hostname,
            expected_revision=args.expected_revision,
            allow_loopback_http=args.allow_loopback_http,
            require_browser_evidence=args.require_browser_evidence,
            timeout=args.timeout,
        )
    except Exception:
        _safe_print(f"FAIL {ERR_TRANSPORT}", stream=sys.stderr)
        return 1
    return int(result)


if __name__ == "__main__":
    sys.exit(main())
