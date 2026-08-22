---
name: Ad Copy Framework Writer
category: marketing-content
summary: Drafts direct-response ad copy using proven frameworks such as AIDA, PAS, and 4U, matched to a specific offer, audience, and platform for team review before launch.
works_with: [copywriting-agent, media-buying-agent, brand-agent]
version: 1.0
---

## WHEN TO USE
Use this skill when you need a fresh batch of ad copy variations for a specific offer and platform and want them structured against a named persuasion framework rather than written free-form. Trigger it after the offer and target audience are defined but before creative production begins. Do not use it to write long-form sales letters or full video scripts — use the VSL Script Builder or Landing Page Copy Designer for those.

## INPUTS
- Offer summary: product/service name, price, and core benefit (plain text, 2-4 sentences)
- Target audience profile: demographics, pain points, and current awareness of the problem (plain text or brief bullet list)
- Platform and placement: e.g. Meta feed ad, Google search ad, email subject line (text)
- Framework preference, if any: AIDA, PAS, or 4U, or "agent's choice" (text)

## WORKFLOW
1. Read the offer summary and extract the single strongest benefit claim and the specific proof point that supports it.
2. Identify the audience's current awareness level (problem-aware, solution-aware, or product-aware) from the profile provided.
3. Select the framework: default to PAS for problem-aware audiences, AIDA for solution-aware audiences, and 4U for product-aware or price-sensitive audiences, unless the input specifies otherwise.
4. If a framework is explicitly specified in the inputs, use that one regardless of awareness level.
5. Draft the Attention/Problem/Useful line first, keeping it under 12 words for feed placements and under 8 words for search headlines.
6. Build out the middle section (Interest/Desire for AIDA, Agitate for PAS, Urgent+Unique for 4U) using the specific proof point from step 1, not generic claims.
7. Write a single clear call-to-action that matches the platform's native action verb (Shop Now, Learn More, Sign Up).
8. Produce three copy variants per framework selected, varying the opening hook while keeping the offer and CTA constant.
9. Check each variant against the platform's character limit: 125 characters for Meta primary text, 30 for a Meta headline, 90 for a Google search ad headline, 180 for a Google search ad description, 60 for an email subject line; if the input names a platform not on this list, default to 125 characters and flag the assumption for reviewer confirmation.
10. Verify no variant contains a specific income, weight-loss, or medical outcome claim; flag and rewrite any that do.
11. Label each variant with its framework and the step of the framework each line maps to, for reviewer traceability.
12. Deliver the labeled variant set for human review before it goes to media buying.

## OUTPUT SPEC
A markdown table or list of 3-6 ad copy variants, each labeled with framework used (AIDA/PAS/4U), platform, character count, and a framework-line breakdown (e.g. "P: ... / A: ... / S: ..."). Delivered as a single document or ad-platform-ready text block, under 500 words total.

## EXAMPLE PROMPT
```
Write 4 Meta feed ad copy variants for our offer "12-Week Strength Reset,"
a $149 self-paced strength training program for women over 40 who feel
weak and low-energy but haven't started training yet. Audience is
problem-aware, not yet familiar with our program. Use the PAS framework
and keep primary text under 125 characters.
```

## QUALITY CHECKS
- Every variant fits the stated platform character limit exactly (fail if over).
- Every variant maps cleanly to its declared framework's steps in the labeled breakdown (fail if a step is missing or mislabeled).
- No variant contains an unsubstantiated income, health, or guarantee claim (fail if present).
- CTA verb matches the platform's standard action set (fail if a non-native CTA is used, e.g. "Buy Now" on a lead-gen placement).
