# Security policy

## Reporting a vulnerability

If you find a security issue in OmniAgentOS Starter, please report it
privately rather than opening a public issue: open a
[GitHub security advisory](../../security/advisories/new) on this repository,
or email the maintainers listed in the repo's GitHub profile. Please include:

- a description of the issue and its impact,
- steps to reproduce (a minimal goal/config that triggers it is ideal),
- the version/commit you tested against.

We will acknowledge reports within a few days and aim to ship a fix or
mitigation before any public disclosure.

## Binding security properties

These are the invariants the project is designed and tested to hold. If you
find a case where one of them doesn't hold, that's a security bug, not just a
regular bug:

- **Local-only by default.** The server binds `127.0.0.1` unless you pass
  `--host 0.0.0.0` explicitly (for reaching it from another device on your
  LAN, for example). Binding to `0.0.0.0` **requires** `OMNIAGENTOS_TOKEN` to
  be set — the server refuses to start bound to a non-loopback address
  without one. Every `/api/*` request must then be authorised, one of three
  ways: an API client sends `Authorization: Bearer <token>`; a browser opens
  `http://host:port/?token=<token>` once, which exchanges it at
  `POST /api/session` for a same-origin `httpOnly` cookie (then scrubs the
  token out of the address bar) so the SSE stream and workspace file links
  keep working with no header support needed; or, on the events route only,
  a `?token=` query parameter for a hand-driven `curl` against the stream —
  never accepted on anything that mutates state. A missing/invalid token
  returns `error_tag: APP_AUTH` (distinct from `PROVIDER_AUTH`, which is
  about your LLM provider key, not this token).
- **No shell access for agents.** Worker agents can only call the workspace
  tools (`read_file`, `write_file`, `list_files`) scoped to a per-run
  workspace directory. There is no shell/exec tool, and the package contains
  no `subprocess` or `os.system` calls that an agent's output could reach.
- **Agent files are just files, sandboxed the same way.** An agent is
  `agents/<slug>.md`; creating, editing, or duplicating one goes through the
  same path hygiene as the workspace guard — a slug charset check, no `..`
  traversal, no absolute paths, nothing written outside `agents/`. An
  agent's persona and standing instructions are XML-escaped into the Worker
  prompt exactly like any other artifact, so a persona containing prompt-
  injection-shaped text (a fake closing tag, for instance) renders as inert
  text, not as instructions. An agent's `tools` list can only be a *subset*
  of the global tool allow-list — it can narrow what a Worker running as
  that agent can do, never widen it — and the built-in general-purpose agent
  cannot be deleted.
- **Workspace containment.** The workspace guard rejects absolute paths,
  `..` traversal, null bytes, symlink escapes, and directory-prefix
  collisions. It refuses to ever treat the repo root, package directory, or
  data directory as a run's workspace.
- **Keys never leave the process.** Provider API keys are read from
  environment variables only, are never written to logs, event streams, or
  API responses, and every user-facing string (including error bodies) is
  passed through redaction before it can reach a client or a log line.

## Local data — never commit it

This project stores its working state on disk under directories that are
`.gitignore`d for a reason. Never commit:

- `var/` — the SQLite memory database (runs, events, lessons) and any
  operator data it accumulates.
- `workspace/` — the per-run file workspaces agents write into; these can
  contain whatever the operator's goals produced.
- `.env` or any file holding `XAI_API_KEY`, `OPENROUTER_API_KEY`,
  `OPENAI_API_KEY`, `OMNIAGENTOS_API_KEY`, or `OMNIAGENTOS_TOKEN`.
- Anything under `evidence/live-receipts/` beyond the tracked `.gitkeep` —
  receipts are generated locally by `scripts/smoke.sh` and are redacted, but
  they are still local run artifacts, not repository content.

If a key is ever accidentally committed, rotate it immediately at the
provider — do not rely on a force-push or history rewrite alone.
