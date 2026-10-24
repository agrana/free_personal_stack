#!/usr/bin/env bash
# Thin wrapper around verify-deployment.py (APP-006).
# Prefers an isolated repo .venv (see requirements-bootstrap.txt).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" "$ROOT/scripts/verify-deployment.py" "$@"
fi
exec python3 "$ROOT/scripts/verify-deployment.py" "$@"
