---
name: Niche Opportunity Scorer
category: research-analysis
summary: Evaluates candidate niches or sub-markets against a weighted scoring model covering demand, competition, and reachability, ranking them for go/no-go decisions.
works_with: [research-agent, founder-agent, marketing-agent]
version: 1.0
---

## WHEN TO USE
Use when choosing between multiple candidate niches to enter or expand into and you need an objective ranking rather than a gut call. Do not use this for evaluating a single niche in isolation — this skill's value is the comparative scoring across a shortlist.

## INPUTS
- Shortlist of 3-8 candidate niches or sub-markets to evaluate (bulleted list)
- Scoring criteria and their relative weights, e.g. demand 30%, competition 25%, reachability 25%, deal size 20% (text or table)
- Access to a keyword/search-volume data source for demand signals (tool name)
- Your current resource constraints: budget, team size, existing channel strengths (text)

## WORKFLOW
1. For each candidate niche, pull search-demand data — monthly search volume or an equivalent interest signal — for its 3-5 core terms.
2. Score demand on a 1-5 scale relative to the other niches on the shortlist, not against an absolute external benchmark.
3. Identify the top 5 visible competitors serving each niche and score competition intensity 1-5, where 5 means crowded and well-funded.
4. Score reachability 1-5 based on whether your existing channels — an ad platform, an email list, a partner network — can plausibly reach that niche without new infrastructure.
5. Estimate average deal size or customer lifetime value for each niche using any available comparable data, and score it 1-5 relative to the shortlist.
6. Apply the weights from INPUTS to calculate a weighted composite score for each niche.
7. If two niches score within 0.3 of each other, flag them as a tie requiring a qualitative tiebreaker rather than declaring a clean winner.
8. Check the top-scoring niche against your stated resource constraints — if it requires capabilities you don't currently have, note the gap explicitly.
9. Verify every score in the model has a one-line justification recorded; strip any score entered without a stated reason.
10. Rank the full shortlist by composite score and write a go/watch/no-go recommendation for each.

## OUTPUT SPEC
A doc-tool table titled "Niche Opportunity Scoring — <date>" with rows per niche, columns per weighted criterion plus composite score, followed by a ranked go/watch/no-go list with one-sentence rationale each. 300-500 words plus the table. Lands in the strategy/planning folder.

## EXAMPLE PROMPT
```
Score these 5 candidate niches for our B2B invoicing software: freelance consultants, small law firms, independent contractors, boutique agencies, and property managers. Weight demand 30%, competition 25%, reachability 25%, deal size 20%. We have a small ad budget and an existing email list of 2,000 freelancers.
```

## QUALITY CHECKS
- Every niche has a score (1-5) recorded for all four criteria with a one-line justification each — fail if any cell is blank or unjustified.
- Weighted composite score is calculated correctly using the weights supplied in INPUTS — fail if the math doesn't reconcile.
- Ties within 0.3 points are explicitly flagged with a qualitative tiebreaker note — fail if a tie is silently resolved with no explanation.
- Final ranking includes a go/watch/no-go call for every niche on the shortlist — fail if any niche is left unrecommended.
