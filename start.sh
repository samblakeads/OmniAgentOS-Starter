#!/usr/bin/env bash
# One-command bootstrap: create a venv if missing, install the package, and
# serve. Any extra arguments are passed straight through to `omniagentos serve`
# (e.g. ./start.sh --port 9000, ./start.sh --host 0.0.0.0).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -d ".venv" ]; then
  echo "==> Creating virtual environment in .venv"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing OmniAgentOS Starter (editable)"
pip install --quiet --upgrade pip
pip install --quiet -e .

echo "==> Starting OmniAgentOS Starter"
exec omniagentos serve --open "$@"
