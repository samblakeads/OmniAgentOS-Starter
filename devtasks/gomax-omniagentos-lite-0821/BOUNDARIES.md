# BOUNDARIES — OmniAgentOS Starter DoD oracle (U0)

This file is binding on implementers. Tests in `tests/dod/` are the independent
oracle: they encode PLAN.md + hunt-B F1–F12, not whatever happens to land in
`omniagentos_starter/`.

## Port-discovery contract (verbatim — implementers must match)

When started with `--port 0`, the child process prints a line to stdout of the
exact form

```
LISTENING port=<n>
```

once the socket is bound, before serving. The test harness spawns the child,
reads stdout line-by-line until it sees this line, parses `<n>`, and only then
issues requests. Never guess a port or use a fixed one — this is what prevents
the F6 "decoy process on 8486" false-success class.

- The line is a single stdout line matching `^LISTENING port=(\d+)\s*$`.
- It is printed by the same process the CLI spawned (the pid `/api/health`
  returns as `pid`).
- stderr is not consulted. `PYTHONUNBUFFERED=1` is set by the harness; the
  CLI must still flush this line.

## Playwright install note

The oracle uses the repo `.venv` (gitignored). Create it with Python 3.12 if
missing, then:

```
python3.12 -m venv .venv
.venv/bin/python -m pip install pytest playwright httpx pyyaml
.venv/bin/playwright install chromium
```

Do this yourself in the checkout; do not only document it. U0 already did so
in this working tree.

## Boundary table

| boundary | mocked tests | unmocked check | env required | evidence path |
|---|---|---|---|---|
| api.x.ai | none in D2/D4/D5/D9 (forbidden: MockTransport, monkeypatch, respx, stub of api.x.ai) | D2 live unmocked run + D4 extra_dod loop + D5 memory + D9 DEMO drills; every counted llm.call has provider_host=api.x.ai | XAI_API_KEY; OMNIAGENTOS_BASE_URL unset or exactly https://api.x.ai/v1 | evidence/d2-live-run.json, evidence/d4-loop.json, evidence/d5-memory.json, evidence/d9-demo-*.json |
| browser-playwright | none for D3/D13 (real Chromium, real child, real SSE) | D3 operator-vantage primary journey; D7 visible banner text; D13 no-key demo screenshot | XAI_API_KEY for D3; no provider keys for D13; chromium in .venv | evidence/d3-vantage.png, evidence/d3-vantage.json, evidence/d13-nokey.png |
| GitHub-gh | none in this oracle (GH workflow state is a different unit) | D8 in-repo `pytest --collect-only` on tests/dod collected>0 and live tests do not skip while a key exists | none (XAI_API_KEY only to refuse silent skips) | evidence/d8-collect.json |
| filesystem-sandbox | none: D10 drives write_file through engine.execute_worker_tool, not a bare WorkspaceGuard unit test | D10 WORKSPACE_ESCAPE for ../x, foo/../../x, /tmp/x, prefix-collision, symlink-out, null-byte; repo-root/package-dir/data-dir refused at construction; no subprocess/os.system/Popen/eval(/exec(; planted key never leaks | planted OMNIAGENTOS_API_KEY=secret_TESTKEY_d10_7f3a9c2e (fail if unset) | evidence/d10-invariants.txt |

## How each Dn could pass while broken, and why it can't now

- **D1:** Curling a decoy already listening on 8486 with `configured=true` from env-presence. Now we spawn `--port 0`, require stdout `LISTENING port=<n>`, `health.pid==child.pid`, nonce echo, and `configured=true` only after a live probe when a key is present.
- **D2:** A local OpenAI-compatible stub plus `payload.get("status") or "done"` with canned role events. Now every counted `llm.call` has `provider_host==api.x.ai`, HTTP 2xx, `response_id`, four distinct roles with distinct response ids and `system_prompt_sha256`, `status` present and `=="done"`, `verified is True`, deliverable not in the placeholder set.
- **D3:** Static HTML role cards, a 10ms click spinner, a non-empty placeholder deliverable, and a zero-width logo. Now busy becomes visible only after a real POST `/api/runs` 2xx, roles are parsed SSE event types, logo `naturalWidth>100` and src bytes sha256-match `assets/omnirogue-logo.png`, deliverable binds to `run.done`, timestamps strictly increase with ≥1 gap >500ms, screenshot saved.
- **D4:** Critic parse-error mapped to `pass=false` and verifier timeout mapped to `verified=true`, or a worker echoing `PRODUCTION LINE` from the goal. Now the phrase is injected via `extra_dod` (not the goal), verdicts are raw JSON with keys present (never defaulted), `repair.dispatched.task_ids` len≥1, critic/verifier `request_id`s differ, the repaired artifact changes, and `verifier_is_verified` is false for missing/malformed payloads.
- **D5:** `memory.recalled` echoing last-inserted `lesson_id` without the planner seeing the text, possibly from a shared DB. Now an isolated temp SQLite is required, `lesson.saved` from run1, run2 recall ids non-empty and include that lesson, and T appears verbatim in run2 `prompts.jsonl` inside `<recalled_lesson>`.
- **D6:** Hardcoded skills count / always-marketing `skill.selected` / event without injection. Now parsed-valid count == files on disk == `GET /api/skills` count (all three), ablation of a temp skills root forbids that category, worker prompt contains `skill-sha256:<hex>` of the file body, and `select()` is deterministic.
- **D7:** Invalid `XAI_API_KEY` falling through to OpenRouter/OpenAI, hidden `data-*` attribute match, or 401→empty `status=done`. Now 401/429/503/malformed/no-key are each driven, secondary keys unset, `status==failed`, and the visible/accessible banner inner_text equals the exact tag.
- **D8:** `pytest -q` exit 0 on an empty or fully-skipped suite, or an unpinned GH success. Now collect-only on `tests/dod` is >0, live files must not `importorskip`/`mark.skip`, and with a key `require_live()` proceeds (GH state is out of this unit).
- **D9:** Three empty `d9-demo-*.json` files unbound to DEMO.md. Now the three literal goals are parsed from DEMO.md, receipt.goal bijection-matches, each `status==done` and `len(deliverable)>40`, goal 1 exhibits the D4 loop, goal 3 has ≥5 workspace files.
- **D10:** Guard unit test of `../x` while prefix/absolute/symlink writes return `""`, or redaction grep skipped because the key is unset. Now `execute_worker_tool` is the worker path, each escape returns `error_tag=WORKSPACE_ESCAPE`, construction refuses repo/package/data roots, package scan forbids shell/eval/exec, planted key is required and grepped out of SSE/logs/API.
- **D11:** `touch` empty JSON newer than HEAD. Now schema `OMNIAGENTOS-RECEIPT-1` is required and `git_head` equals `git rev-parse HEAD`.
- **D12:** Hand-written clock JSON or wall-clock around a mocked burst. Now `t_first_event_ms` and `t_done_ms` are derived from real SSE `ts` per DEMO goal and must be <2000 and <120000; evidence/d12-clock.json.
- **D13:** Serve refusing no-key start, or `/api/demo` emitting a single canned done. Now all provider keys unset, health `configured:false` + `PROVIDER_NOT_CONFIGURED`, demo SSE has all four role events, screenshot `evidence/d13-nokey.png`.
- **D14:** LICENSE first line "MIT" on an empty file, runtime paths committed, unbounded skills. Now gitleaks (or equivalent) is clean, `git ls-files` has no `var/` `workspace/` `.env` `*.sqlite3`, LICENSE body matches OSI MIT (trademark paragraph may follow), shipped skills ≤12.

## Binding pins the oracle assumes (also listed in the U0 report)

See the harness docstrings. Critical ones:

- CLI: console script `omniagentos` and `python -m omniagentos_starter`.
- SSE: `id:` + `event:` + `data:` JSON with `"type"` and `"ts"`; headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
- `POST /api/runs` body `{goal, max_rounds?, extra_dod?:[{criterion}]}` returns `{id}` or `{run_id}`.
- `GET /api/runs/{id}` includes present `status` and `verified` (never defaulted).
- `llm.call` includes `{role, model, ms, prompt_tokens, completion_tokens, provider_host, http_status, response_id, request_id, system_prompt_sha256}` and never content.
- `critic.verdict` JSON has `pass` and `request_id`; `verifier.verdict` JSON has `verified` and `request_id`.
- `execute_worker_tool(root, name, arguments)` is the worker LLM tool-call path.
- `verifier_is_verified(payload)` is true only for dict with JSON boolean `verified is True`.
- Skills root override: `OMNIAGENTOS_SKILLS_ROOT`.
- Prompt transcript: `<data-dir>/runs/<run_id>/prompts.jsonl`.
- DEMO.md: ```dod-goals fence, or `GOAL:` lines, or `Goal 1:`/`Goal 2:`/`Goal 3:`.
- UI: `img[alt="OmniRogue"]`, `[data-testid="run-busy"]`, `[data-testid="deliverable"]`, `[data-testid="goal-input"]` or `textarea`, `[data-testid="run-button"]` or a button named Run, `[data-testid="error-banner"]` or `[role="alert"]`.
- Receipts: `magic="OMNIAGENTOS-RECEIPT-1"` plus the schema keys in PLAN addendum v2.1.
- `scripts/drill.py --goal ... --receipt <path>` is the preferred D11 producer; the oracle will synthesize a schema-valid receipt from a live run if drill.py is missing, but implementer receipts must still match.
