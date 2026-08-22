# Skills

Each skill is a single self-contained Markdown pack: `skills/<category>/<slug>.md`.
The engine scans this directory tree at boot, parses every pack, and selects the
best-matching one (or two) for a given goal by keyword score. Dropping a new pack
into any category folder — no code change, no restart config — makes it available
immediately; `GET /api/skills` reports whatever is on disk.

## Format

Every pack follows the same structure (see `FORMAT.md` upstream in the OmniRogue
Agent Skills Library for the full spec this mirrors):

```yaml
---
name: <Title Case skill name>
category: <one of the 10 categories below>
summary: <one sentence, 15-35 words, the outcome this skill produces>
works_with: [<2-4 agent roles>]
version: 1.0
---
```

followed by six required sections, in order: `## WHEN TO USE`, `## INPUTS`,
`## WORKFLOW` (≥6 numbered, imperative steps with at least one decision point and
one verification step), `## OUTPUT SPEC`, `## EXAMPLE PROMPT` (a fenced, filled-in
instruction), `## QUALITY CHECKS` (≥3 mechanical pass/fail checks).

`scripts/lint_skills.py` enforces this format plus slug uniqueness, a 900-byte
floor per file, and a hard fail on any internal hostname, IP address, email
address, or local filesystem path leaking into a pack.

## What's here

Ten sample packs, one per category, chosen for having the most mechanically
checkable `QUALITY CHECKS` in their category. The engine's own general-purpose
fallback pack (used when no category pack matches a goal) ships inside the
package, not here — it works even if this whole folder is empty.

| Category | Sample pack |
|---|---|
| lead-generation | cold-email-sequence-builder |
| sales | proposal-generator |
| customer-support | refund-request-handler |
| marketing-content | ad-copy-framework-writer |
| creative-production | vsl-script-builder |
| operations-admin | meeting-agenda-builder |
| research-analysis | niche-opportunity-scorer |
| development-technical | form-integration-tester |
| finance-reporting | expense-categorizer |
| ecommerce-retail | bundle-offer-designer |

## The full library

These 10 are samples, MIT-licensed, shipped as a working demonstration of the
format and the loader. The full **OmniRogue Agent Skills Library** — 100+ packs
across the same 10 categories — is an **OmniRogue Enterprise** entitlement. Drop
it into this folder in place of (or alongside) the samples and the count rises
automatically; nothing in the loader is hard-coded to 10.

→ https://omnirogue.com
