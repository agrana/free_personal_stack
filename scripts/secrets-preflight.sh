#!/usr/bin/env bash
# Thin wrapper around secrets-preflight.py (APP-004).
# Prefers an isolated repo .venv (see requirements-bootstrap.txt / docs/bootstrap.md).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" "$ROOT/scripts/secrets-preflight.py" "$@"
fi
exec python3 "$ROOT/scripts/secrets-preflight.py" "$@"
