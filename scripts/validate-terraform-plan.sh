#!/usr/bin/env bash
# APP-002 offline Terraform JSON plan guard wrapper.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  PYTHON="python3"
fi

exec "${PYTHON}" "${ROOT}/scripts/validate-terraform-plan.py" "$@"
