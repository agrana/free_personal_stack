#!/usr/bin/env bash
# Thin wrapper around app-new.py (APP-005).
# Prefers an isolated repo .venv (see requirements-bootstrap.txt / docs/bootstrap.md).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" "$ROOT/scripts/app-new.py" "$@"
fi
exec python3 "$ROOT/scripts/app-new.py" "$@"
