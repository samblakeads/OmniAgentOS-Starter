#!/usr/bin/env bash
# Post-start live-receipt smoke test (POSIX).
#
# Starts a real `omniagentos serve` on an ephemeral port, confirms
# /api/health reports configured:true, submits one tiny live run, reads its
# SSE event stream (falling back to polling) to completion within 120s, and
# writes a redacted receipt to evidence/live-receipts/smoke-<ts>.json.
#
# Passing requires ALL of: run status == "done", run verified == true, a
# non-empty deliverable, and >=4 distinct roles (planner/worker/critic/
# verifier) observed in the events. A run that merely reaches status=done
# with an empty deliverable or missing roles is NOT success — exits 1.
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
HEALTH_STATUS="$(printf '%s' "$HEALTH_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status","unknown"))' 2>/dev/null || echo "unknown")"
if [ "$CONFIGURED" != "true" ]; then
  fail "/api/health did not report configured:true (got: $HEALTH_JSON) — set XAI_API_KEY / OPENROUTER_API_KEY / OPENAI_API_KEY"
fi
# Belt-and-braces: configured:true and status:"degraded" are set together
# today (both flip on the same provider-unreachable probe), but check status
# explicitly too rather than relying on that correlation holding forever.
if [ "$HEALTH_STATUS" != "ok" ]; then
  fail "/api/health reported status:$HEALTH_STATUS, not ok (got: $HEALTH_JSON) — provider is configured but unreachable"
fi
echo "==> health OK, configured:true, status:ok"

GOAL='Write a 3-bullet summary of why agent orchestration beats a chatbox.'
COMMIT_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "unknown")"

# Drive the run, read its SSE events for role coverage, and gate on the full
# contract (status==done, verified==true, non-empty deliverable, >=4 roles)
# in one place — see B5-F8: a thin {status} check alone let an empty-
# deliverable or roleless run report SMOKE OK.
set +e
python3 - "$BASE_URL" "$GOAL" "$TS" "$COMMIT_SHA" "$RECEIPT_FILE" <<'PYEOF'
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request

BASE_URL, GOAL, TS, COMMIT_SHA, RECEIPT_FILE = sys.argv[1:6]

# Mirrors scripts/drill.py's ROLE_EVENTS (literal copy, not an import, so
# this script has the same low coupling as scripts/lint_skills.py's mirror
# of redact._SHAPE_PATTERNS).
ROLE_EVENTS = {
    "planner": ("planner.plan",),
    "worker": ("worker.started", "worker.finished", "worker.delta"),
    "critic": ("critic.verdict",),
    "verifier": ("verifier.verdict",),
}

DEADLINE_S = 120.0


def http_json(method: str, url: str, body: dict | None = None, timeout: float = 15.0) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


start = time.time()
try:
    run = http_json("POST", BASE_URL + "/api/runs", {"goal": GOAL})
except Exception as exc:
    print(f"SMOKE FAIL: POST /api/runs failed: {exc!r}", file=sys.stderr)
    sys.exit(1)

run_id = run.get("run_id") or run.get("id") or ""
if not run_id:
    print(f"SMOKE FAIL: no run id in POST /api/runs response: {run!r}", file=sys.stderr)
    sys.exit(1)
print(f"==> run started: {run_id}")

event_types: set[str] = set()
req = urllib.request.Request(BASE_URL + f"/api/runs/{run_id}/events")
try:
    with urllib.request.urlopen(req, timeout=DEADLINE_S + 5) as resp:
        for raw_line in resp:
            if time.time() - start > DEADLINE_S:
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload:
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            etype = obj.get("type")
            if etype:
                event_types.add(etype)
            if etype in ("run.done", "run.failed"):
                break
except (urllib.error.URLError, TimeoutError, OSError) as exc:
    print(f"==> event stream ended early: {exc!r}", file=sys.stderr)

# Poll fallback: if the stream closed/timed out before a terminal event (e.g.
# server restarted the connection), give the final GET a few more tries
# within what's left of the 120s budget rather than trusting the stream alone.
summary: dict = {}
poll_deadline = start + DEADLINE_S
while True:
    try:
        summary = http_json("GET", BASE_URL + f"/api/runs/{run_id}", timeout=10.0)
    except Exception as exc:
        summary = {}
        print(f"==> could not fetch run summary: {exc!r}", file=sys.stderr)
    if summary.get("status") in ("done", "failed") or time.time() >= poll_deadline:
        break
    time.sleep(2)

elapsed_s = round(time.time() - start, 2)

status = summary.get("status", "unknown")
verified = bool(summary.get("verified"))
deliverable = summary.get("deliverable") or ""
roles_seen = sorted(r for r, markers in ROLE_EVENTS.items() if any(m in event_types for m in markers))
missing_roles = sorted(set(ROLE_EVENTS) - set(roles_seen))

problems = []
if status != "done":
    problems.append(f"run status is {status!r}, not done")
if not verified:
    problems.append("run.verified is not true")
if not deliverable.strip():
    problems.append("deliverable is empty")
if len(roles_seen) < 4:
    problems.append("fewer than 4 roles seen in events; missing: " + ", ".join(missing_roles))

deliverable_sha256 = hashlib.sha256(deliverable.encode("utf-8")).hexdigest()

receipt = {
    "magic": "OMNIAGENTOS-RECEIPT-1",
    "kind": "smoke",
    "ts": TS,
    "commit_sha": COMMIT_SHA,
    "run_id": run_id,
    "status": status,
    "verified": verified,
    "roles_seen": roles_seen,
    "event_types": sorted(event_types),
    "deliverable_chars": len(deliverable),
    "deliverable_sha256": deliverable_sha256,
    "elapsed_s": elapsed_s,
    "problems": problems,
    "ok": not problems,
}

# Prefer the app's real redactor (this venv has it installed); fall back to a
# small local shape scrub if the import ever fails so a receipt is never
# written unredacted either way.
try:
    from omniagentos_starter.redact import redact as _redact

    receipt = _redact(receipt)
except Exception:
    text = json.dumps(receipt)
    for pat in (r"Bearer\s+\S+", r"sk-[A-Za-z0-9]{10,}", r"xai-[A-Za-z0-9]{10,}"):
        text = re.sub(pat, "[REDACTED]", text)
    receipt = json.loads(text)

with open(RECEIPT_FILE, "w", encoding="utf-8") as f:
    json.dump(receipt, f, indent=2)
    f.write("\n")

print(f"==> receipt written: {RECEIPT_FILE}")

if problems:
    print("SMOKE FAIL: " + "; ".join(problems), file=sys.stderr)
    sys.exit(1)

print(
    f"SMOKE OK: run {run_id} done in {elapsed_s}s, verified, "
    f"{len(roles_seen)} roles ({','.join(roles_seen)}), "
    f"deliverable {len(deliverable)} chars"
)
PYEOF
PY_EXIT=$?
set -e

if [ "$PY_EXIT" -ne 0 ]; then
  fail "run did not satisfy the smoke contract (status=done AND verified AND non-empty deliverable AND >=4 roles) — see receipt/output above"
fi
