# Foreseeable false-success sweep (U0 oracle)

Hunt-B already closed F1–F12 (stub origin / default status, workspace prefix+vacuous redaction, verifier fail-open, static DOM spinner, secondary-key fallback, decoy :8486, empty pytest + unpinned GH, lesson id without injection, hardcoded skills count, mtime receipts, unbound DEMO files, goal-echo flake). The tests encode those tightened checks.

Additional paths noticed while writing the oracle, and how they are closed:

1. **LISTENING printed before bind / printed to stderr.** A wrapper could echo `LISTENING port=8486` and then fail to bind. Closed: harness never guesses 8486; it parses the line then GETs `/api/health` on that port and requires `health.pid == child.pid`. Dead port or foreign pid fails D1.

2. **SSE `event:` is `message` with type only in HTML.** D3/D4/D13 parse SSE records (`event:` or `data.type`), not card text. Missing role event types fail.

3. **Busy CSS `opacity:0` / `aria-hidden` counted as visible.** D3 uses computed style + bounding box; hidden-only D7 banner inner_text must equal the tag, not a data-attribute.

4. **`configured:true` after env-presence without the 1-token probe.** D1 requires `configured is True` only when a live key is present (probe must have succeeded). D13 requires the opposite with keys unset.

5. **`execute_worker_tool` as a test-only wrapper around WorkspaceGuard while the worker uses a second `os.open`.** Closed as far as an independent oracle can: D10 imports `omniagentos_starter.engine.execute_worker_tool` (BINDING: this is the worker loop's function) AND greps the package for `subprocess|os.system|Popen|eval(|exec(`. A second raw open that is not those tokens is a residual risk; prefix/symlink/absolute still go through that function.

6. **Receipts with `git_head` copied from `git rev-parse` at package-build time, not receipt-write time.** D11 compares to `git rev-parse HEAD` of the tree under test at validation time. Stale baked sha fails.

7. **D9/D12 reading DEMO.md prose and treating headings as goals.** Parser requires a `dod-goals` fence, `GOAL:` lines, or `Goal 1/2/3:` literals — three strings, bijection with receipt.goal.

8. **Health JSON containing key fragments under unused fields (`debug_env`, headers echo).** D1/D10 grep the planted/real key (and a mid-fragment) against `/api/health`, SSE, logs, and `/api/*` bodies.

9. **Demo replay emitting four role *names* in one `run.done` payload.** D13 requires distinct SSE event types `planner.plan`, `worker.finished`, `critic.verdict`, `verifier.verdict`.

10. **`status="done"` with `verified` omitted.** Readers use `"status" in run` and `"verified" in obj` (never `.get(..., True)`). D4 helper `verifier_is_verified` is false for missing keys.

None of these replace hunt-B F1–F12; they are extras the tests also close.

Residual (not closed without implementer cooperation, flagged as pins): proving `execute_worker_tool` is *called* from the LLM tool loop (not only exported) and proving `verifier_is_verified` is *called* from the run loop. The live D4/D10 paths still fail closed if those functions are unused and the engine fail-opens — because SSE/API assertions look at the actual run records, not the helper return values alone.
