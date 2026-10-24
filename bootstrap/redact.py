"""Redaction helpers — fixed codes and scrubbed text only."""

from __future__ import annotations

import re
from typing import Iterable

_REDACT_PATTERNS = (
    re.compile(r"://[^/\s]+:[^/@\s]+@", re.IGNORECASE),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"(?i)\b(password|secret|token|api[_-]?key)\s*=\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\bvct_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"postgresql://[^\s]+", re.IGNORECASE),
    re.compile(r"postgres://[^\s]+", re.IGNORECASE),
    re.compile(r"https?://[^\s]+", re.IGNORECASE),
)


def redact_text(
    message: str,
    *,
    extra_fragments: Iterable[str] = (),
    max_length: int | None = 240,
) -> str:
    text = str(message)
    for fragment in extra_fragments:
        if fragment and fragment in text:
            text = text.replace(fragment, "[redacted]")
    for pattern in _REDACT_PATTERNS:
        text = pattern.sub("[redacted]", text)
    if max_length is not None and len(text) > max_length:
        text = text[:max_length] + "…"
    return text


def safe_code(code: str) -> str:
    text = str(code).strip()
    if not text or any(ch in text for ch in "\n\r\t") or len(text) > 120:
        return "error.unspecified"
    return text
