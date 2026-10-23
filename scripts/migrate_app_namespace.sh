#!/usr/bin/env bash
# APP-003 app-scoped migration wrapper. Prefers repo-local .venv.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" "$ROOT/scripts/migrate_app_namespace.py" "$@"
fi
exec python3 "$ROOT/scripts/migrate_app_namespace.py" "$@"
