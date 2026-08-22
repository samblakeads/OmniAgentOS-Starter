#!/usr/bin/env bash
# RIDERS: LIVE-BOUNDARY=D2:api.x.ai,D4:api.x.ai,D5:api.x.ai,D9:api.x.ai OPERATOR-VANTAGE=D3 CANNED-FALLBACK=D7 FEATURE-E2E=D4,D5,D6,D9,D13 CONTENT=D9 END-STATE=D8 INVARIANTS=D10 SWEEP=evidence/foreseeable-sweep.md
# OmniAgentOS Starter DoD oracle runner (U0). Exit 0 only if D1-D14 all PASS
# and evidence/audit-verdict.txt contains "Verified Complete" plus an auditor lineage.

set -u
umask 077

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
EVIDENCE="$SCRIPT_DIR/evidence"
TESTS="$REPO_ROOT/tests/dod"
export OMNIAGENTOS_DOD_EVIDENCE="$EVIDENCE"
export OMNIAGENTOS_DOD_REQUIRE_LIVE=1
export PYTHONUNBUFFERED=1
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$REPO_ROOT/.venv/ms-playwright}"
mkdir -p "$EVIDENCE"

fail_riders() {
  echo "FAIL RIDERS"
  exit 1
}

fail_boundaries() {
  echo "FAIL BOUNDARIES"
  exit 1
}

# --- FIRST action: RIDERS self-check (before any Dn) ---
if [ ! -f "$EVIDENCE/foreseeable-sweep.md" ]; then
  fail_riders
fi

missing=0
for n in 01 02 03 04 05 06 07 08 09 10 11 12 13 14; do
  if ! ls "$TESTS"/test_d${n}_*.py >/dev/null 2>&1; then
    echo "missing tests/dod/test_d${n}_*.py" >&2
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  fail_riders
fi

# LIVE-BOUNDARY files must not contain transport mocks or an obvious api.x.ai stub.
live_files="$TESTS/test_d02_live_boundary.py $TESTS/test_d04_loop_until_done.py $TESTS/test_d05_self_learning_memory.py $TESTS/test_d09_demo_goals.py"
if grep -E -n "MockTransport|monkeypatch|respx" $live_files >/dev/null 2>&1; then
  echo "LIVE-BOUNDARY file contains MockTransport/monkeypatch/respx" >&2
  fail_riders
fi
if grep -E -n "BaseHTTPRequestHandler|httpx\.MockTransport|respx\.Mock|stub of api\.x\.ai|api\.x\.ai.*stub" $live_files >/dev/null 2>&1; then
  echo "LIVE-BOUNDARY file contains an obvious stub of api.x.ai" >&2
  fail_riders
fi

# BOUNDARIES.md: every table row must have a non-empty "unmocked check" column.
BFILE="$SCRIPT_DIR/BOUNDARIES.md"
if [ ! -f "$BFILE" ]; then
  fail_boundaries
fi
empty_unmocked=0
while IFS= read -r line; do
  case "$line" in
    "|"*) ;;
    *) continue ;;
  esac
  # skip header / separator
  echo "$line" | grep -qi "unmocked check" && continue
  echo "$line" | grep -qE '^\|[-: |]+\|$' && continue
  echo "$line" | grep -qE '^\|[[:space:]]*---' && continue
  # fields: empty, boundary, mocked, unmocked, env, evidence
  unmocked="$(printf '%s\n' "$line" | awk -F'|' '{gsub(/^[[:space:]]+|[[:space:]]+$/, "", $4); print $4}')"
  boundary="$(printf '%s\n' "$line" | awk -F'|' '{gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2}')"
  [ -z "$boundary" ] && continue
  if [ -z "$unmocked" ]; then
    echo "empty unmocked check for row: $line" >&2
    empty_unmocked=1
  fi
done < "$BFILE"
if [ "$empty_unmocked" -ne 0 ]; then
  fail_boundaries
fi

# Source keys at shell level BEFORE pytest. Never print values.
if [ -f "$HOME/.config/omni/connections.env" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$HOME/.config/omni/connections.env"
  set +a
fi

PY="$REPO_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "repo .venv missing; tests will fail via system python" >&2
  PY="${PYTHON:-python3}"
fi
cd "$REPO_ROOT" || exit 1
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

run_dn() {
  local n="$1"
  local file rc
  file="$(ls "$TESTS"/test_d$(printf '%02d' "$n")_*.py 2>/dev/null | head -n 1)"
  local out="$EVIDENCE/d${n}-pytest.txt"
  if [ -z "$file" ]; then
    echo "FAIL D${n}"
    echo "missing test file" > "$out"
    return 1
  fi
  "$PY" -u -m pytest --rootdir "$REPO_ROOT" -q --tb=line "$file" > "$out" 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "PASS D${n}"
    return 0
  fi
  echo "FAIL D${n}"
  return 1
}

# Do not `set -e` across the loop — we want all 14 lines.
set +e
all_pass=1
# Keep under 900s total.
START_TS="$(date +%s)"
for n in 1 2 3 4 5 6 7 8 9 10 11 12 13 14; do
  now="$(date +%s)"
  elapsed=$((now - START_TS))
  if [ "$elapsed" -gt 880 ]; then
    echo "FAIL D${n}"
    echo "budget exhausted before D${n}" > "$EVIDENCE/d${n}-pytest.txt"
    all_pass=0
    # remaining Dns
    for rest in $(seq $((n + 1)) 14); do
      echo "FAIL D${rest}"
      echo "budget exhausted" > "$EVIDENCE/d${rest}-pytest.txt"
    done
    all_pass=0
    break
  fi
  if ! run_dn "$n"; then
    all_pass=0
  fi
done

AUDIT="$EVIDENCE/audit-verdict.txt"
audit_ok=0
if [ -f "$AUDIT" ]; then
  if grep -q "Verified Complete" "$AUDIT"; then
    if grep -qiE "auditor lineage|lineage:|gemini|grok|claude|codex|gpt|opus|anthropic|xai" "$AUDIT"; then
      audit_ok=1
    fi
  fi
fi

if [ "$all_pass" -eq 1 ] && [ "$audit_ok" -eq 1 ]; then
  exit 0
fi
exit 1
