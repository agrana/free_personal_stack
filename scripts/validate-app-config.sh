#!/usr/bin/env bash
# Thin wrapper around validate-app-config.py.
# Prefers an isolated repo .venv (see requirements-bootstrap.txt / docs/bootstrap.md).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" "$ROOT/scripts/validate-app-config.py" "$@"
fi
exec python3 "$ROOT/scripts/validate-app-config.py" "$@"
