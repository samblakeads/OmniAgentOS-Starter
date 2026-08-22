---
name: VSL Script Builder
category: creative-production
summary: Writes a full video sales letter script — hook, story, mechanism, offer, and close — structured for a long-form persuasive video presentation.
works_with: [copywriting-agent, video-editing-agent, sales-agent]
version: 1.0
---

## WHEN TO USE
Use this skill when you need a complete, long-form video sales letter script for a specific offer, meant to run as a standalone persuasion video (not a live webinar). Trigger it once the offer and unique mechanism are already defined. Do not use it for short-form social video — use Short Form Video Scripter for that.

## INPUTS
- The offer and price being sold at the end of the script (plain text)
- The product's unique mechanism, if already articulated (plain text, or note "not yet defined")
- Target viewer's core pain point and current failed attempts to solve it (plain text)
- Target script length in minutes (number)

## WORKFLOW
1. Confirm target length and allocate roughly: hook 5%, story/problem 25%, mechanism/solution reveal 20%, proof 15%, offer 25%, close 10%.
2. Write the hook: open with a bold claim, question, or pattern interrupt tied directly to the pain point from the inputs — no scene-setting before it.
3. Write the story section in first- or third-person narrative naming the specific failed attempts the viewer has likely already tried, drawn from the inputs.
4. Introduce the "turning point" moment in the story where the mechanism was discovered, transitioning from problem-focus to solution-focus.
5. If the unique mechanism input is marked "not yet defined," insert a flagged placeholder noting the script needs the Unique Mechanism Articulator skill run first rather than inventing one.
6. Reveal the mechanism by name and explain its cause-and-effect logic in plain terms, contrasting it against the failed attempts named in step 3.
7. Insert 2-3 proof points (results, testimonials, or credibility markers) immediately after the mechanism reveal, before the offer is presented.
8. Present the offer: name it, list the value stack, state the price, and present the guarantee, in that order.
9. Add a scarcity or urgency line only if it corresponds to a real, statable constraint — otherwise omit it rather than fabricating one.
10. Write the close: restate the transformation from problem to result promised in the hook, then give one explicit action instruction (click the button below, etc.).
11. Verify the script's total word count is consistent with the target runtime at a natural speaking pace (~140 words/minute).
12. Verify the mechanism named in step 6 is the same one referenced in the offer section — no unexplained new claims appear in the pitch.
13. Deliver the full script broken into labeled sections (Hook, Story, Mechanism, Proof, Offer, Close) with estimated timing per section.

## OUTPUT SPEC
A full script broken into six labeled sections (Hook, Story, Mechanism, Proof, Offer, Close), each with estimated run time, sized to the target duration at ~140 words/minute. Plain text, length scales with target runtime.

## EXAMPLE PROMPT
```
Write a 10-minute VSL script for our offer "The Sleep Reset Protocol,"
a $97 digital program, priced with a $47 fast-action bonus disappearing
at midnight tonight (real cart-close). Mechanism: a 3-step evening
light/temperature/timing sequence that resets circadian rhythm without
melatonin. Target viewer has tried melatonin, blackout curtains, and
sleep apps without lasting results.
```

## QUALITY CHECKS
- All six sections (Hook, Story, Mechanism, Proof, Offer, Close) are present and labeled in order (fail if any is missing or reordered).
- Total script word count is within roughly 15% of the target runtime at 140 words/minute (fail if far off).
- Any urgency/scarcity line traces to a real constraint from the inputs; none is fabricated (fail if invented).
- The mechanism referenced in the Offer section matches the one revealed earlier in the script, with no new unexplained claims (fail if inconsistent).
