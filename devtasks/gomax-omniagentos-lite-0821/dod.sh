#!/usr/bin/env bash
# RIDERS: LIVE-BOUNDARY=D2:api.x.ai,D4:api.x.ai,D5:api.x.ai,D9:api.x.ai OPERATOR-VANTAGE=D3 CANNED-FALLBACK=D7 FEATURE-E2E=D4,D5,D6,D9,D13 CONTENT=D9 END-STATE=D8 INVARIANTS=D10 SWEEP=evidence/foreseeable-sweep.md
# OmniAgentOS Starter DoD oracle runner (U0). Exit 0 only if D1-D14 all PASS
# and evidence/audit-verdict.txt contains "Verified Complete" plus an auditor lineage.
#
# SCHEDULING NOTE (non-weakening): checks D1-D14 are grouped and run with
# bounded parallelism (max 3 concurrent groups) purely to fit the 900s wall
# budget as the live checks grew heavier (D6 ablation ~340s, D12 ~180s,
# D9 ~150s, D5 ~90s serially exceeded it). Every Dn still runs its OWN
# pytest process against its OWN test file, with its OWN tmp data-dir and
# `--port 0` server (spawn_serve() in _harness.py always mints a fresh
# tempdir + ephemeral port per process), so parallel groups cannot collide
# on state, port, or MAX_CONCURRENT_RUNS (each group is a distinct server
# process). No assertion, threshold, or check in any Dn changed — only WHEN
# each Dn's already-existing pytest invocation is scheduled.
#
# FAIL-CLOSED RUNNER NOTE (attested Grok audit finding F5): per-Dn status
# used to live at a fixed path cleared by a best-effort delete at the start
# of each run; if that delete silently failed, a stale PASS from a PRIOR
# run could be honoured without pytest ever re-running (fail-open). Status
# files now live under a fresh `mktemp -d` created at the start of THIS
# run and are NEVER reused across runs; each one carries this run's nonce
# plus the pytest exit code, and a missing file OR a file whose nonce does
# not match this run's nonce reads as FAIL, never a silent stale PASS. A
# failure to create or tear down that run directory is a runner-level
# failure (`FAIL RUNNER`, exit 1), distinct from any Dn.

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

fail_runner() {
  echo "FAIL RUNNER" >&2
  echo "FAIL RUNNER"
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

# Fail-closed run directory: a fresh mktemp -d, never a fixed/reused path,
# so there is no stale file for a silently-failed delete to leave behind
# (the vulnerability this replaces: `find ... -delete` on a fixed path
# could fail silently and a leftover PASS from a PRIOR run would then be
# honoured without pytest ever re-running for THIS run). If creation fails
# for any reason, that is a runner-level failure, not a Dn failure.
RUNDIR="$(mktemp -d "${TMPDIR:-/tmp}/omniagentos-dod-run.XXXXXX" 2>/dev/null)"
if [ -z "$RUNDIR" ] || [ ! -d "$RUNDIR" ]; then
  echo "mktemp -d failed to create a fresh run directory" >&2
  fail_runner
fi
# Per-run nonce: binds every status file to THIS invocation. A status file
# missing this exact nonce (wrong run, stale copy, hand-edited, etc.) is
# never honoured as a PASS — it reads as FAIL below.
RUN_NONCE="dod-$$-$(date +%s)-${RANDOM:-0}-${RANDOM:-0}"

# run_one_dn: identical semantics to the previous serial run_dn — same
# pytest invocation, same evidence file, same PASS/FAIL determination —
# just writes the verdict to a status file instead of printing directly,
# so it is safe to call from a backgrounded subshell.
run_one_dn() {
  local n="$1"
  local file rc out status_file
  file="$(ls "$TESTS"/test_d$(printf '%02d' "$n")_*.py 2>/dev/null | head -n 1)"
  out="$EVIDENCE/d${n}-pytest.txt"
  status_file="$RUNDIR/d${n}.status"
  if [ -z "$file" ]; then
    echo "missing test file" > "$out"
    printf '%s\n%s\n' "$RUN_NONCE" "127" > "$status_file"
    return
  fi
  "$PY" -u -m pytest --rootdir "$REPO_ROOT" -q --tb=line "$file" > "$out" 2>&1
  rc=$?
  printf '%s\n%s\n' "$RUN_NONCE" "$rc" > "$status_file"
}

# run_group: runs its member Dns SEQUENTIALLY within the group (each still
# its own pytest process/server), so distinct groups never share a live
# server. Groups themselves run in parallel across background jobs.
run_group() {
  local ids="$1" n
  for n in $ids; do
    run_one_dn "$n"
  done
}

# Grouping (independent live boundaries -> parallel; heavy solo checks get
# their own group so they don't block/be blocked by unrelated fast checks):
#   A: D2 D4 D5   (xAI live: single run, extra_dod loop, memory 2-run)
#   B: D6         (skills ablation, several live runs, ~340s alone)
#   C: D9 D12     (DEMO goal drills + stage-clock, share the drill shape)
#   D: D3 D7 D13  (playwright/browser + fallback + no-key)
#   E: D1 D8 D10 D11 D14 (fast: health/pid/nonce, collect-only, invariants,
#      receipt schema, hygiene — no long-running live run in this set)
set +e
DOD_GROUPS="2 4 5|6|9 12|3 7 13|1 8 10 11 14"
MAX_CONC=3

START_TS="$(date +%s)"
# Watchdog: if the whole parallel section somehow runs past budget, kill
# every remaining dod.sh child so we still reach the "print all 14 lines"
# step below (any Dn without a status file is read as FAIL, never omitted).
# It must NEVER be included in the group-completion `wait` below, and its
# own job slot must NEVER count against MAX_CONC — both bugs (fixed here)
# would silently floor the real completion time at the watchdog's own
# sleep duration even when every check finished far earlier.
(
  sleep 850
  echo "dod.sh watchdog: 850s elapsed, terminating remaining child processes" >&2
  pkill -TERM -P $$ 2>/dev/null
) &
WATCHDOG_PID=$!

OLDIFS="$IFS"
IFS='|'
set -- $DOD_GROUPS
IFS="$OLDIFS"

GROUP_PIDS=""
for group in "$@"; do
  while [ "$(jobs -rp | grep -vc "^${WATCHDOG_PID}\$")" -ge "$MAX_CONC" ]; do
    sleep 1
  done
  run_group "$group" &
  GROUP_PIDS="$GROUP_PIDS $!"
done
# Wait ONLY on the group PIDs (never bare `wait`, which would also block on
# the still-sleeping watchdog job and floor total runtime at 850s).
wait $GROUP_PIDS 2>/dev/null

kill "$WATCHDOG_PID" 2>/dev/null
wait "$WATCHDOG_PID" 2>/dev/null

ELAPSED="$(( $(date +%s) - START_TS ))"
echo "dod.sh: parallel checks finished in ${ELAPSED}s" >&2

# Print exactly one PASS Dn / FAIL Dn line per D1-D14, IN ORDER, after all
# groups finish. A missing status file (crash/kill) reads as FAIL, never
# silently omitted.
all_pass=1
for n in 1 2 3 4 5 6 7 8 9 10 11 12 13 14; do
  status_file="$RUNDIR/d${n}.status"
  verdict="FAIL"
  if [ -f "$status_file" ]; then
    file_nonce="$(sed -n '1p' "$status_file")"
    file_rc="$(sed -n '2p' "$status_file")"
    numeric_rc=0
    case "$file_rc" in
      [0-9]|[0-9][0-9]|[0-9][0-9][0-9]) numeric_rc=1 ;;
    esac
    if [ "$file_nonce" = "$RUN_NONCE" ] && [ "$numeric_rc" -eq 1 ] && [ "$file_rc" -eq 0 ]; then
      verdict="PASS"
    fi
  fi
  if [ "$verdict" = "PASS" ]; then
    echo "PASS D${n}"
  else
    echo "FAIL D${n}"
    all_pass=0
    if [ ! -f "$status_file" ]; then
      echo "no status file written (crashed/killed before completion)" > "$EVIDENCE/d${n}-pytest.txt"
    elif [ "$file_nonce" != "$RUN_NONCE" ]; then
      echo "status file nonce mismatch (stale/foreign file) — treated as FAIL" > "$EVIDENCE/d${n}-pytest.txt"
    fi
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

# Fail-closed teardown: this run's status files must not survive to be
# mistaken for a fresh run's — but a failed teardown is a runner problem,
# never silently ignored (that silence is exactly finding F5).
find "$RUNDIR" -delete 2>/dev/null
if [ -d "$RUNDIR" ]; then
  echo "run directory $RUNDIR still exists after teardown" >&2
  fail_runner
fi

if [ "$all_pass" -eq 1 ] && [ "$audit_ok" -eq 1 ]; then
  exit 0
fi
exit 1
