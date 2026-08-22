#!/usr/bin/env bash
# Post-start live-receipt smoke test (POSIX).
#
# Starts a real `omniagentos serve` on an ephemeral port, confirms
# /api/health reports configured:true, submits one tiny live run, polls it
# to completion (done|failed) within 120s, and writes a redacted receipt to
# evidence/live-receipts/smoke-<ts>.json. Exits non-zero on any failure.
#
# This is a LIVE test: it spends real provider credits and requires a real
# key (XAI_API_KEY / OPENROUTER_API_KEY / OPENAI_API_KEY) in the environment.
# It is never run in CI — see .github/workflows/ci.yml.
#
# Usage: scripts/smoke.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

RECEIPT_DIR="$REPO_ROOT/evidence/live-receipts"
mkdir -p "$RECEIPT_DIR"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$(mktemp -t omniagentos-smoke-log.XXXXXX)"
DATA_DIR="$(mktemp -d -t omniagentos-smoke-data.XXXXXX)"
RECEIPT_FILE="$RECEIPT_DIR/smoke-$TS.json"

SERVER_PID=""
cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$DATA_DIR"
}
trap cleanup EXIT

fail() {
  echo "SMOKE FAIL: $1" >&2
  echo "  server log: $LOG_FILE" >&2
  tail -n 40 "$LOG_FILE" >&2 2>/dev/null || true
  exit 1
}

# Make sure `omniagentos` is on PATH; bootstrap a venv if this is a bare checkout.
if ! command -v omniagentos >/dev/null 2>&1; then
  if [ -d "$REPO_ROOT/.venv" ]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.venv/bin/activate"
  fi
fi
if ! command -v omniagentos >/dev/null 2>&1; then
  echo "==> omniagentos not on PATH, bootstrapping .venv"
  python3 -m venv "$REPO_ROOT/.venv"
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.venv/bin/activate"
  pip install --quiet -e "$REPO_ROOT"
fi

PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
BASE_URL="http://127.0.0.1:$PORT"

echo "==> Starting omniagentos serve on port $PORT (data-dir $DATA_DIR)"
omniagentos serve --port "$PORT" --host 127.0.0.1 --data-dir "$DATA_DIR" >"$LOG_FILE" 2>&1 &
SERVER_PID=$!

echo "==> Waiting for /api/health"
HEALTH_JSON=""
for _ in $(seq 1 30); do
  if HEALTH_JSON="$(curl -fsS "$BASE_URL/api/health" 2>/dev/null)"; then
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    fail "server process exited before becoming healthy"
  fi
  sleep 1
done
if [ -z "$HEALTH_JSON" ]; then
  fail "server never answered /api/health within 30s"
fi

CONFIGURED="$(printf '%s' "$HEALTH_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(str(bool(d.get("configured"))).lower())' 2>/dev/null || echo "unknown")"
if [ "$CONFIGURED" != "true" ]; then
  fail "/api/health did not report configured:true (got: $HEALTH_JSON) — set XAI_API_KEY / OPENROUTER_API_KEY / OPENAI_API_KEY"
fi
echo "==> health OK, configured:true"

GOAL='Write a 3-bullet summary of why agent orchestration beats a chatbox.'
START_TS=$(python3 -c 'import time; print(time.time())')

RUN_JSON="$(curl -fsS -X POST "$BASE_URL/api/runs" \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"goal": sys.argv[1]}))' "$GOAL")" )" \
  || fail "POST /api/runs failed"

RUN_ID="$(printf '%s' "$RUN_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("id") or d.get("run_id") or "")' 2>/dev/null || true)"
if [ -z "$RUN_ID" ]; then
  fail "could not extract a run id from POST /api/runs response: $RUN_JSON"
fi
echo "==> run started: $RUN_ID"

STATUS="unknown"
DEADLINE=$((SECONDS + 120))
while [ "$SECONDS" -lt "$DEADLINE" ]; do
  RUN_STATE_JSON="$(curl -fsS "$BASE_URL/api/runs/$RUN_ID" 2>/dev/null || true)"
  STATUS="$(printf '%s' "$RUN_STATE_JSON" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get("status",""))
except Exception:
    print("")' 2>/dev/null || true)"
  if [ "$STATUS" = "done" ] || [ "$STATUS" = "failed" ]; then
    break
  fi
  sleep 2
done

END_TS=$(python3 -c 'import time; print(time.time())')
ELAPSED_S=$(python3 -c "print(round($END_TS - $START_TS, 2))")

if [ "$STATUS" != "done" ] && [ "$STATUS" != "failed" ]; then
  fail "run $RUN_ID did not reach done|failed within 120s (last status: '$STATUS')"
fi

COMMIT_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "unknown")"

# Build the receipt, then run it through a defensive redaction pass — even
# though every field here is a control value (no provider text), never trust
# that blindly for anything that touches a live run.
python3 - "$RECEIPT_FILE" "$TS" "$COMMIT_SHA" "$RUN_ID" "$STATUS" "$ELAPSED_S" <<'PYEOF'
import json
import re
import sys

receipt_file, ts, commit_sha, run_id, status, elapsed_s = sys.argv[1:7]

receipt = {
    "ts": ts,
    "commit_sha": commit_sha,
    "run_id": run_id,
    "status": status,
    "elapsed_s": float(elapsed_s),
}

text = json.dumps(receipt, indent=2)
key_patterns = [
    re.compile(r"Bearer\s+\S+"),
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
    re.compile(r"xai-[A-Za-z0-9]{10,}"),
]
for pat in key_patterns:
    text = pat.sub("[REDACTED]", text)

with open(receipt_file, "w") as f:
    f.write(text + "\n")
PYEOF

echo "==> receipt written: $RECEIPT_FILE"

if [ "$STATUS" = "failed" ]; then
  fail "run $RUN_ID finished with status=failed"
fi

echo "SMOKE OK: run $RUN_ID done in ${ELAPSED_S}s"
