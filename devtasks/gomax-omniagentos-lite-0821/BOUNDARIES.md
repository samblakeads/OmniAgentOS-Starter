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
| api.x.ai | none in D2/D4/D5/D9/D16/D18 (forbidden: MockTransport, monkeypatch, respx, stub of api.x.ai) | D2 live unmocked run + D4 extra_dod loop + D5 memory + D9 DEMO drills + D16 agent-run persona/skill-isolation/memory/regression + D18 team-run delegation with per-task prompt isolation; every counted llm.call has provider_host=api.x.ai | XAI_API_KEY; OMNIAGENTOS_BASE_URL unset or exactly https://api.x.ai/v1 | evidence/d2-live-run.json, evidence/d4-loop.json, evidence/d5-memory.json, evidence/d9-demo-*.json, evidence/d16-agent-run.json, evidence/d18-team-run.json |
| browser-playwright | none for D3/D13/D15 (real Chromium, real child, real SSE) | D3 operator-vantage primary journey; D7 visible banner text; D13 no-key demo screenshot; D15 agent create/reload/duplicate/edit/delete via the Agents UI | XAI_API_KEY for D3/D15; no provider keys for D13; chromium in .venv | evidence/d3-vantage.png, evidence/d3-vantage.json, evidence/d13-nokey.png, evidence/d15-agent-create.json |
| GitHub-gh | none in this oracle (GH workflow state is a different unit) | D8 in-repo `pytest --collect-only` on tests/dod collected>0 and live tests do not skip while a key exists | none (XAI_API_KEY only to refuse silent skips) | evidence/d8-collect.json |
| filesystem-sandbox | none: D10 drives write_file through engine.execute_worker_tool, not a bare WorkspaceGuard unit test | D10 WORKSPACE_ESCAPE for ../x, foo/../../x, /tmp/x, prefix-collision, symlink-out, null-byte; repo-root/package-dir/data-dir refused at construction; no subprocess/os.system/Popen/eval(/exec(; planted key never leaks | planted OMNIAGENTOS_API_KEY=secret_TESTKEY_d10_7f3a9c2e (fail if unset) | evidence/d10-invariants.txt |
| agents-filesystem | none: D15/D16/D17 always point OMNIAGENTOS_AGENTS_ROOT at a fresh tmp copy of the shipped agents/ roster (tmp_agents_root() in _harness.py) — the real repo agents/ tree is NEVER written by this oracle | D15 create/reload/GET/duplicate/edit/DELETE-403 round-trip via a real browser + real HTTP on the isolated root; D17 traversal/absolute/NUL slug rejection + tool-widening rejection + persona-injection escaping, all against the isolated root | XAI_API_KEY for D15/D17's live sub-test; none for the two non-live D17 sub-tests | evidence/d15-agent-create.json, evidence/d17-slug-rejects.json, evidence/d17-persona-injection.json |

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
- **D15 (Round 6):** A UI card materialising from client-side state alone, with no server write, or `/api/agents` backed by an in-memory list that resets on reload. Now the created agent must exist as a real `agents/<slug>.md` file (isolated tmp root) with matching front-matter, survive a full page reload, be listed by a real `GET /api/agents` on the SAME running child, round-trip through duplicate (new slug, copied persona) and PUT edit (persisted to disk), and DELETE on the shipped `general-worker` builtin must 403 while the file survives; a non-builtin DELETE must succeed and remove its file.
- **D16 (Round 6):** `agent_id` accepted in the request body but silently ignored by the router (business as usual), or an `agent.assigned` event emitted without the persona/skill actually reaching the LLM. Now the worker prompt transcript must contain the persona text verbatim AND the agent's own `skill-sha256:<hex>`, and must NOT contain the `skill-sha256` of a different shipped pack (proves the agent's skill list actually restricts the router, not just suggests to it); the run must be `done` with `verified is True`; `lesson.saved.agent_id` must match; a second run by the same agent must recall that lesson via `memory.recalled.agent_id`; and a sibling run WITHOUT `agent_id` must still behave exactly as before (no `agent.assigned` event, still `done`+`verified`) — the explicit regression guard.
- **D17 (Round 6):** A slug/name filter that only rejects the literal substring `../` while a URL-encoded or absolute-path variant still escapes, or a `tools` list that is unioned with (rather than intersected against) the global allow-list, or persona text concatenated raw into the system prompt so `</worker_instructions><system>...` breaks out of its tag. Now every malicious slug (`../..`, absolute, NUL byte) is driven through the real `POST /api/agents` and must both 400 AND leave no file outside the isolated agents root; a `tools` superset of `[read_file, write_file, list_files]` must 400 while a subset succeeds; a planted `</worker_instructions><system>` in the persona must appear escaped (or absent, never raw) in the actual recorded prompt transcript; and the full D10 workspace-escape + no-shell + no-key-leak suite is re-run byte-for-byte to prove Round 6 introduced no regression in the sandbox invariants.

- **D18 (Round 8):** `agent_id` resolved to a manager but the manager silently executes both parts of the goal itself (team is decorative), or `team.delegated` fires without the delegated member's own persona/skill actually reaching THAT task's prompt (only proven somewhere in the whole transcript), or a self-referential/cyclic/too-deep team loads as if healthy and only crashes (500) later on a run. Now `POST /api/runs` against a manager must produce ≥2 `team.delegated` events naming ≥2 DISTINCT members; each delegated task's OWN `prompts.jsonl` line (attributed by `task_id`) must carry that member's persona verbatim and that member's own `skill-sha256`, and must NEVER carry the other member's or the manager's own `skill-sha256`; the run must be `done`+`verified`; `lesson.saved` must be attributed to the executing member's own scope; and a self/cycle/depth>2/missing-member team must never 500 — it must be listed `disabled` with a recorded reason in `GET /api/agents`, and `POST /api/runs` against it must 400.

## Round 6 — AGENTS pins (binding on implementers, added with D15–D17)

- **AgentStore root:** the server serves agents from `<repo>/agents` **unless**
  `OMNIAGENTOS_AGENTS_ROOT` is set, in which case that path is authoritative
  (same override pattern as `OMNIAGENTOS_SKILLS_ROOT`). Every U0 test that
  creates/edits/deletes an agent sets this env var to a **fresh tmp copy** of
  the shipped roster (`tmp_agents_root()` in `_harness.py`, mirroring the D6
  skills-ablation pattern) — the real shipped `agents/` tree is never mutated
  by this oracle.
- **tmp_agents_root() exclusions (observed r7 failure, fixed):** the tmp
  copy of the shipped roster excludes `README.md` (documentation, not an
  agent) AND any file whose front-matter `name:` starts with `Riley`
  (case-insensitive). A concurrent browser/oracle session had previously
  created `riley-meal-prep-support.md` directly in the real `<repo>/agents/`
  tree; the naive `shutil.copytree` inherited it into what was supposed to
  be an isolated tmp root, and DEMO beat 0's `POST /api/agents` then 409'd
  against that stray, racy file. Beat 0 always creates its own Riley fresh
  inside the isolated root — it must never rely on (or accidentally inherit)
  one that already exists.
- **Idempotent beat-0 agent creation:** test_d09/test_d12 (and any future
  caller) use `create_agent_idempotent()`, never a bare `create_agent()`
  call, for beat 0. On a `POST /api/agents` 409, it `DELETE`s the exact
  colliding slug (parsed from the error body, falling back to `slugify(name)`)
  and retries, asserting **HTTP 201** on the retry. It NEVER silently reuses
  a pre-existing agent file of unknown content/skills/persona. If the DELETE
  itself is refused (e.g. the collision is actually a protected builtin),
  the test fails loudly with the refusal reason rather than proceeding.
- **Builtin agent:** `agents/_builtin/general-worker.md` must always be
  present under whatever root is active (shipped copy, or synthesized by the
  harness when `agents/` doesn't exist yet in the red-first state). `DELETE`
  on that slug must 403 regardless of root.
- **`POST /api/agents` response** must include a `slug` (or `id`) field — the
  oracle treats the API's returned slug as authoritative and never assumes a
  particular `slugify()` algorithm; `duplicate`/`edit` tests resolve the file
  by that returned slug, not by guessing.
- **Global tool allow-list** is exactly `[read_file, write_file, list_files]`
  (`AGENT_GLOBAL_TOOLS` in `_harness.py`). An agent's `tools` may only be a
  SUBSET of this list; any tool outside it (e.g. `shell`, `run_command`) in
  the create/edit body must 400.
- **`agent.assigned` event**: `{agent_id, skills}` emitted once per run,
  ONLY when `agent_id` was set on `POST /api/runs`. A run without `agent_id`
  must never emit it (D16 regression guard).
- **Worker system prompt** with an agent set must contain the agent's
  `persona` text verbatim (XML-escaped exactly like goal/artifact text
  elsewhere — `<`/`>` around a literal `</worker_instructions><system>`
  payload must never appear raw) and `skill-sha256:<hex>` for each of the
  agent's own skills only — never a `skill-sha256` for a pack outside the
  agent's `skills` list.
- **`lesson.saved`** carries `agent_id` when the run that produced it had one
  set; **`memory.recalled`** carries `agent_id` and prefers that agent's own
  lessons (falls back to global recall when the agent has none — matched==0
  is still explicit, per the existing D5 contract).
- **DEMO.md `||| agent: <slug>` suffix (new, alongside the existing
  `||| dod: <criterion>` suffix):** a fenced ` ```dod-goals ` line may end
  with literal ` ||| agent: <slug>`. Fixed order when both suffixes are
  present on one line: `||| dod: ... ||| agent: ...` (dod always precedes
  agent — the agent suffix is stripped first since it anchors to end-of-line).
  `tests/dod/_harness.py::parse_demo_goals_full()` returns
  `(goal, [dod_criterion,...], agent_slug_or_None)` triples;
  `parse_demo_goals()`/`parse_demo_goals_with_dod()` are unchanged in
  behavior (existing D9/D12 callers using the old two-element contract are
  unaffected). DEMO beat 0 pins this suffix onto goal 2's line; test_d09 and
  test_d12 both create an agent matching that slug (name/title/persona
  mirroring PLAN's "Riley, Meal-Prep Support" beat, skills=[a refund-handling
  pack if one is shipped, else the first shipped skill]) via `POST
  /api/agents` and dispatch that goal with the returned `agent_id`, asserting
  `agent.assigned` fires and (D12) `t_done_ms < 120000` still holds with the
  agent in the loop.

## Round 8 — TEAMS pins (binding on implementers, added with D18)

- **`team` field:** agent front-matter gains `team: [member_slug, ...]`,
  making that agent a MANAGER. `create_agent()`/`create_agent_idempotent()`
  in `_harness.py` gained an optional `team=` kwarg that POSTs this field —
  additive, no existing caller's contract changed.
- **`POST /api/runs` with a manager `agent_id`:** the Planner assigns each
  task in the plan to one of the manager's team members; that member's
  persona+skills are used for THAT task's worker (never the manager's own
  skills, even if the manager was created with some); the manager's persona
  frames the plan only.
- **`team.delegated` event:** `{manager, task_id, member}`, one per
  delegated task. `manager` and `member` are both agent slugs.
- **Per-task prompt attribution (BINDING, new on `prompts.jsonl`):** a
  worker-role prompt line for a delegated task MUST carry a `task_id` field
  matching the `task_id` in its `team.delegated` event, so the oracle (and
  any auditor) can attribute persona/skill-sha injection to the EXACT
  delegated task rather than the whole run's transcript. This is additive
  to the existing `prompts.jsonl` contract (worker lines already carry
  `skill-sha256:<hex>`; this only adds the `task_id` field).
- **Cycle/depth/self/missing-member guard:** a team may not contain the
  agent itself, a cycle, a chain deeper than 2 levels of manager-of-manager,
  or a slug that doesn't resolve to an existing agent. Any of these must
  NEVER 500 (at creation or later at run time) — the agent is created (2xx)
  but listed `enabled: false` (or `disabled: true`) with a non-empty
  `errors`/`error`/`reason` field in `GET /api/agents`, and `POST /api/runs`
  against that slug returns 400.
- **Member tools never widen beyond global:** unchanged from D17's
  Round-6 pin — a team member's own `tools` are still bounded by
  `AGENT_GLOBAL_TOOLS`; being on a team grants no additional privilege.
- **UI `[data-testid="agent-team"]`** on the manager's roster card, listing
  its team member(s) by slug or display name (test_d15's minimal extension,
  `test_d15_manager_card_lists_team_members`, checks either form matches).
- **AMENDMENT (Grok round-5 audit) — team VALIDITY vs. team MEMBER
  disablement are two different failure classes, checked differently:**
  D17's new `test_d17_team_validity_rejected_at_create_and_update` requires
  `POST`/`PUT /api/agents` to 400 outright (never write a file, name a
  `error_tag`/`error`/`detail`/`message` string) when the TEAM ITSELF is
  structurally invalid at that moment — self-reference, a cycle (formed by
  the update that completes it), a member slug that doesn't resolve to any
  agent, or a member slug that resolves to an agent that is ITSELF already
  `disabled`. This is stricter than — and distinct from — D18's
  `test_d18_malformed_team_disables_never_crashes`, which tolerates EITHER
  a 400-at-creation OR a 2xx-create-then-listed-disabled outcome for
  self/missing-member/depth>2 teams (matching this section's original
  "Cycle/depth/self/missing-member guard" bullet above, which came from
  PLAN.md's literal "load error, agent disabled with reason" wording).
  Both oracles are binding simultaneously: an implementer MUST reject the
  four specific cases D17 covers (self/cycle/missing-member/disabled-member)
  with 400 at POST/PUT time; D18's tolerance is for cases D17 does not
  cover as strictly (e.g. depth>2, which D17 does not test) or as a
  fallback path if U1b's design genuinely differs from PLAN's original
  wording for the cases D17 DOES cover — that fallback needs a coordinator
  decision if it is ever actually hit, not a silent oracle downgrade.
- **D18 UI task-member attribution — `[data-testid="task-member"]`, PER-TASK
  JOIN (Grok round-6 audit tightening):** one element per delegated task in
  the run's detail/timeline view, inner text containing the delegated
  member's slug or display name. Existence-on-the-page alone is
  insufficient — a mismatched join (e.g. markers rendered in
  `team.delegated` order while task rows render in plan order) would pass
  that weaker check. The oracle now requires selecting the SPECIFIC task
  row via `[data-task-id="<task_id>"] [data-testid="task-member"]` and
  checking that its own text names that task's own delegated member, plus
  a reverse check that no task row names a DIFFERENT member than its own
  attribution. **BINDING pin, read from the current `static/app.js`
  (`renderTasks()`, `#workers-body .task` divs, ~line 762-770):** the
  `.task` row does **not** currently carry any task-id attribute at all —
  neither `renderTasks()`'s primary build nor the second `.task`-building
  path (~line 926) sets one. Implementers must add
  `data-task-id="<task_id>"` to the `.task` div (both render paths) for
  this selector to resolve; until then this portion of D18 is
  correctly/expectedly red.
- **D18 UI run-scoping — `[data-testid="agent-runs-filter"]`:** visible
  after clicking an agent's roster card; inner text contains a run COUNT
  (parsed via `\d+`) that must equal `len(GET /api/runs?agent_id=<slug>
  .items)`.
- **ASSUMED, RED-FIRST, TO BE CONFIRMED (not yet observed against a real
  implementation):** `test_d18_team_run_ui_task_member_attribution_and_
  runs_filter` navigates to `GET /?run_id=<id>` to reach a run's detail/
  timeline view, and assumes `GET /api/runs?agent_id=<slug>` is the
  server-side filter mechanism the UI's `agent-runs-filter` count must
  match. Neither the URL query-param convention nor the `agent_id` query
  filter on `/api/runs` has been confirmed against U1b's actual
  implementation yet — per the brief, this is expected/acceptable red-first
  right now. Whoever lands the real UI/API for this must correct this pin
  (and the test, if the mechanism differs) rather than leave it silently
  wrong once the feature exists.
- **D18 run-start cycle validation (Grok round-6 audit, defense-in-depth):**
  `test_d18_malformed_team_disables_never_crashes` now also forms a cycle
  DIRECTLY ON DISK (bypassing `POST`/`PUT /api/agents` entirely — via
  `_harness.set_agent_field_on_disk()`, a raw front-matter rewrite) between
  a manager and the member it manages, then `POST /api/runs` against that
  manager. This must **400** with a named `error_tag`/`error`/`detail`/
  `message` — **never 200/201** (silently running with a broken hierarchy)
  and **never 500** (crashing at run-start instead of a clean rejection).
  This proves team-structure validation runs at run-START time too, not
  only at agent create/update time, so a hand-edited or stale file can
  never slip a broken hierarchy past the API's own guards into an actual
  run.

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
