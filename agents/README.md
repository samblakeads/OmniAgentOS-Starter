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
name: <first name or short handle>
title: <role title>
persona: <2-4 sentences — voice, priorities, what this agent will not do>
skills: [<skill-slug>, ...]        # must exist under skills/
tools: [read_file, write_file, list_files]   # subset only, never wider
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
back to the built-in general pack if none of them match. An agent's `tools`
list can only **narrow** the global tool allow-list (`read_file`,
`write_file`, `list_files` — there is no shell tool to grant), never widen it.
Memory is scoped per agent: lessons an agent learns are recalled for that
agent first, with the global pool as fallback.

## What's here

Five prebuilt agents, one per shipped sample skill area, each under 1.5 KB:

| Slug | Name / Title | Skills |
|---|---|---|
| `sales-closer` | Cole, Sales Closer | proposal-generator |
| `support-rep` | Ava, Support Rep | refund-request-handler |
| `content-writer` | Max, Content Writer | ad-copy-framework-writer, vsl-script-builder |
| `researcher` | Nora, Researcher | niche-opportunity-scorer |
| `ops-assistant` | Sage, Ops Assistant | meeting-agenda-builder, cold-email-sequence-builder |

`scripts/lint_agents.py` enforces the format above, plus: distinct slugs,
every listed skill actually exists under `skills/`, every listed tool is on
the global allow-list, a 1.5 KB ceiling per file, and the same
hostname/IP/email/local-path/provider-key leak scan `lint_skills.py` runs on
skill packs.

## Drop your own in

Create `agents/<your-slug>.md` in this same format and it appears in the
roster on the next load — no restart config, no code change. The full
**OmniRogue Enterprise** roster ships more prebuilt agents (and the full
100+ pack skills library to build new ones against) — see the main README.
