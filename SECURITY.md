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
  `--host 0.0.0.0` explicitly. Binding to `0.0.0.0` **requires**
  `OMNIAGENTOS_TOKEN` to be set, and every `/api/*` request must then carry it
  as a `Bearer` token — the server refuses to start bound to a non-loopback
  address without a token configured.
- **No shell access for agents.** Worker agents can only call the workspace
  tools (`read_file`, `write_file`, `list_files`) scoped to a per-run
  workspace directory. There is no shell/exec tool, and the package contains
  no `subprocess` or `os.system` calls that an agent's output could reach.
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
