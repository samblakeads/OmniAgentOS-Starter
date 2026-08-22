# Agents

An agent is a persistent, named identity you hand goals to — `agents/<slug>.md`.
The engine loads every file here the same way it loads `skills/`: a directory
scan, drop-in roster, no code change to add one. Assign a goal to an agent
(the picker in the dashboard, or an `@slug` prefix in the goal text, or
`omniagentos run --agent <slug> "<goal>"`) and that agent's persona, skills,
and tool access replace the router's default pick for that run. Leave the
picker on "Let the router decide" and behavior is unchanged from a goal with
no agent at all.

## Format

```yaml
---
name: <first name or short handle>       # no /, \, .., or NUL — see Naming below
title: <role title>
persona: <2-4 sentences — voice, priorities, what this agent will not do>
skills: [<skill-slug>, ...]        # must exist under skills/, [] is valid (no specialism)
tools: [read_file, write_file, list_files]   # narrower than this is fine; wider is a 400
memory_scope: <slug>               # this agent's own lesson namespace
visibility: public | private
version: 1.0
---

## Standing instructions

Body: the system-prompt-level instructions this agent always follows,
regardless of which goal it's handed. Specific, imperative, and scoped to
what this agent's skills actually cover — the same bar as a skill pack's
WORKFLOW section, not a generic "be helpful" paragraph.
```

An agent's `skills` list restricts which packs its Worker can be routed to —
the general-purpose router only picks among an agent's own skills, falling
back to the built-in general pack if none of them match. An empty
`skills: []` is valid and deliberate, not a mistake: the roster card reads
"no skills declared · the router may choose from the whole library" —
this agent has no fixed specialism and defers entirely to the router's
normal pick. A `skills:` entry naming a pack that isn't actually installed
is a different case and **disables the whole agent**, visibly — the roster
card shows it disabled with the reason, and `GET /api/agents` reports the
same `errors` list — never silently dropped, because that would quietly
hand a goal to a different agent than the one asked for.

**`tools`**: omit the key entirely and an agent gets the full global
allow-list (`read_file`, `write_file`, `list_files` — there is no shell
tool to grant, ever); set `tools: []` and it gets none; list a subset to
narrow it to exactly that. Any name **not** on the global allow-list is
rejected outright (`400`), not silently ignored — an agent's tools can only
ever get narrower than the global set, never wider.

Memory is scoped per agent: lessons an agent learns are recalled for that
agent first, with the global pool as fallback.

## Naming

A name containing `/`, `\`, `..`, or a NUL byte is **refused with a 400**,
not reduced to a sanitized slug — a name like `../../etc/passwd` does not
quietly become the agent `etc-passwd`. Ordinary punctuation is otherwise
fine. **The slug a new agent gets from the create form is built from both
the name and the title** — `name: Priya`, `title: Onboarding Specialist`
becomes the slug `priya-onboarding-specialist` (the title is skipped if the
name already ends with it, so it never stutters). Editing an existing agent
(`PUT /api/agents/<slug>`) is a **partial edit** — only the fields you send
change; omitted fields keep their current value.

## Assigning a goal, and how you know it took

Three channels assign a run to an agent, same precedence every time: the
dashboard's **Assign to** picker; typing `@<slug>` at the very start of the
goal text; or `omniagentos run --agent <slug> "<goal>"` from the CLI. A
picker choice always wins over an `@mention` if both are present. Before
you press Run, the line under the goal box (`agent-resolved`) says exactly
who the run will execute as — or, if an `@mention` doesn't resolve to a
real agent, says so before you can even submit it. An unresolvable or
disabled agent (`@slug` that doesn't exist, or a picker value for an agent
that's currently disabled) is refused outright — `400 UNKNOWN_AGENT` — the
run never starts, and the unresolved text never reaches a prompt. The CLI
has its own guard: `omniagentos run --agent <unknown> "..."` exits `2`
before a single provider call.

## Teams — bots managing bots

Add `team: [<slug>, ...]` to an agent's front-matter and it becomes a
**manager** — a run assigned to it doesn't execute the goal itself, it
splits the work across its listed team members instead. Each delegated
task runs under that member's own persona, skills, and tools (never the
manager's), the manager's own persona only frames the plan, and the
dashboard shows a `team.delegated` chip on each member's task as it works —
"member · delegated by manager". Each member keeps its own memory scope, so
a lesson a member learns while working for a manager is still that
member's lesson, recalled the same way whether it's delegated to or run
directly.

A `team:` entry is validated the same strictness as `skills:` — never a
silent surprise:

* an agent listing itself in its own `team:` is disabled, reason on the card;
* a team member that isn't actually in the roster disables the manager, not
  a mid-run failure;
* a **cycle** (A manages B, B manages A — or a longer loop) disables every
  agent in it;
* a delegation chain deeper than **2** (a manager whose team member is
  itself a manager whose team member is itself a manager) is disabled too —
  legal in principle, but past the point an operator can say who actually
  did the work.

A disabled manager is still visible in the roster, card and API alike, with
the reason attached — never removed and never silently downgraded to
running the goal itself.

## What's here

Six prebuilt agents, each under 1.5 KB — five workers, one per shipped sample skill area, plus a manager directing two of them:

| Slug | Name / Title | Skills / Team |
|---|---|---|
| `sales-closer` | Cole, Sales Closer | proposal-generator |
| `support-rep` | Ava, Support Rep | refund-request-handler |
| `content-writer` | Max, Content Writer | ad-copy-framework-writer, vsl-script-builder |
| `researcher` | Nora, Researcher | niche-opportunity-scorer |
| `ops-assistant` | Sage, Ops Assistant | meeting-agenda-builder, cold-email-sequence-builder |
| `studio-director` | Remy, Studio Director | **manager** — team: content-writer, researcher |

Plus `_builtin/general-worker.md` — the always-present generalist (`skills: []`,
falls back to whatever the router hands it) used when no other agent is
assigned and no specific skill matches. A `_builtin/` file in this roster
overrides the package's own packaged copy of the same slug. Its card in the
dashboard shows a disabled control reading **"built-in · cannot be
deleted"** instead of a delete button, and the API answers the same thing
if you go around the UI: `DELETE /api/agents/general-worker` → `403`.

In the dashboard, the header's **Agents** nav link scrolls to this section
(no page load). `GET /api/agents`'s `count` field is always exactly the
length of the `agents` array beside it — every agent, builtin included.

`scripts/lint_agents.py` enforces the format above (including `_builtin/`),
plus: distinct slugs, every listed skill actually exists under `skills/`,
every listed tool is on the global allow-list, a 1.5 KB ceiling per file,
and the same hostname/IP/email/local-path/provider-key leak scan
`lint_skills.py` runs on skill packs.

## Drop your own in

Create `agents/<your-slug>.md` in this same format and it appears in the
roster on the next load — no restart config, no code change. The full
**OmniRogue Enterprise** roster ships more prebuilt agents (and the full
100+ pack skills library to build new ones against) — see the main README.
