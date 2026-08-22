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
back to the built-in general pack if none of them match. A `skills:` entry
naming a pack that isn't actually installed **disables the whole agent**,
visibly (the reason shows on its card and in `GET /api/agents`) — it is
never silently dropped, because that would quietly hand a goal to a
different agent than the one asked for.

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
quietly become the agent `etc-passwd`. Ordinary punctuation is fine:
`"Riley, Meal-Prep Support"` → slug `riley-meal-prep-support`. Editing an
existing agent (`PUT /api/agents/<slug>`) is a **partial edit** — only the
fields you send change; omitted fields keep their current value.

## What's here

Five prebuilt agents, one per shipped sample skill area, each under 1.5 KB:

| Slug | Name / Title | Skills |
|---|---|---|
| `sales-closer` | Cole, Sales Closer | proposal-generator |
| `support-rep` | Ava, Support Rep | refund-request-handler |
| `content-writer` | Max, Content Writer | ad-copy-framework-writer, vsl-script-builder |
| `researcher` | Nora, Researcher | niche-opportunity-scorer |
| `ops-assistant` | Sage, Ops Assistant | meeting-agenda-builder, cold-email-sequence-builder |

Plus `_builtin/general-worker.md` — the always-present generalist (`skills: []`,
falls back to whatever the router hands it) used when no other agent is
assigned and no specific skill matches. A `_builtin/` file in this roster
overrides the package's own packaged copy of the same slug, and answers
`403` on `DELETE` — the generalist can't be removed, only replaced.

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
