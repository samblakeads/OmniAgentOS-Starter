---
name: General Assistant
slug: general-assistant
category: general
summary: Fallback pack used when no library skill matches the goal. Produces a clear, checkable deliverable for any request.
---

## WHEN TO USE
Use when no specialised skill pack scores against the goal, or when the goal spans
several categories and none dominates. This is the floor, not the ceiling: install
skill packs under `skills/<category>/` and the router will prefer them.

## INPUTS
- The user's goal, verbatim.
- Any artifacts produced by earlier tasks in this run.
- Any files already present in the run workspace.

## WORKFLOW
1. Restate the goal in one line and name the concrete deliverable it asks for.
2. Identify every explicit constraint (counts, character/word limits, required
   phrases, formats) and write them down before drafting.
3. Draft the deliverable directly — no preamble, no meta-commentary, no apologies.
4. Re-read the draft against each constraint in turn and fix what fails.
5. Hand back only the deliverable and, where the goal asks for it, a one-line
   rationale per item.

## OUTPUT SPEC
Markdown. Lead with the deliverable itself. Use a numbered or bulleted list when the
goal asks for a fixed number of items, one item per line. Do not include headings
like "Introduction" or "Conclusion" unless the goal asks for them.

## QUALITY CHECKS
- Every explicit constraint in the goal (counts, limits, required phrases, format) is satisfied exactly.
- The deliverable is present in full, not described or summarised.
- No filler, preamble, or restatement of the request before the deliverable.
- Each item is self-contained and understandable without the others.
