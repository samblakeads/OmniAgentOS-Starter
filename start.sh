#!/usr/bin/env bash
# One-command bootstrap: create a venv if missing, install the package, and
# serve. Any extra arguments are passed straight through to `omniagentos serve`
# (e.g. ./start.sh --port 9000). Passing --host 0.0.0.0 requires
# OMNIAGENTOS_TOKEN to be set first (the server refuses to bind off-loopback
# without one — see SECURITY.md).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# Fail fast with a clear message rather than a wall of pip/setuptools text
# from a `requires-python = ">=3.11"` mismatch discovered mid-install.
PY_BIN="${PYTHON:-python3}"
if ! command -v "$PY_BIN" >/dev/null 2>&1; then
  echo "OmniAgentOS Starter needs Python 3.11+. No '$PY_BIN' found on PATH." >&2
  exit 1
fi
if ! "$PY_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  PY_VERSION="$("$PY_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || echo unknown)"
  echo "OmniAgentOS Starter needs Python 3.11+, found $PY_VERSION ($PY_BIN)." >&2
  echo "Install Python 3.11 or newer, or set PYTHON=/path/to/python3.11 and re-run." >&2
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "==> Creating virtual environment in .venv"
  "$PY_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# Skip the network round-trip on every launch: only (re)install when
# pyproject.toml actually changed since the last successful install (or the
# stamp is missing), unless OMNIAGENTOS_FORCE_INSTALL=1 is set. This is what
# makes a second `./start.sh` come up in seconds on flaky stage wifi.
STAMP_FILE=".venv/.omniagentos-install-stamp"
CURRENT_HASH="$(shasum -a 256 pyproject.toml 2>/dev/null | awk '{print $1}')"
STAMP_HASH="$(cat "$STAMP_FILE" 2>/dev/null || true)"

if [ "${OMNIAGENTOS_FORCE_INSTALL:-}" = "1" ] || [ -z "$CURRENT_HASH" ] || [ "$CURRENT_HASH" != "$STAMP_HASH" ]; then
  echo "==> Installing OmniAgentOS Starter (editable)"
  pip install --quiet --upgrade pip
  pip install --quiet -e .
  if [ -n "$CURRENT_HASH" ]; then
    echo "$CURRENT_HASH" > "$STAMP_FILE"
  fi
else
  echo "==> OmniAgentOS Starter already installed and up to date, skipping pip (set OMNIAGENTOS_FORCE_INSTALL=1 to force)"
fi

echo "==> Starting OmniAgentOS Starter"
exec omniagentos serve --open "$@"
