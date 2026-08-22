---
name: Cold Email Sequence Builder
category: lead-generation
summary: Builds a multi-touch cold email sequence with personalized subject lines, escalating value propositions, and scheduled send timing tailored to a target prospect list.
works_with: [sales-agent, outreach-agent, follow-up-agent]
version: 1.0
---

## WHEN TO USE
Use this skill when you have a defined target list (from a CRM export or purchased list) and need a 4-6 email outbound sequence to book meetings with cold prospects who have never interacted with your brand. Trigger it at the start of a new outbound campaign or when reviving a stale prospect list segment. Do not use it for warm leads who already replied or attended a demo — route those to the follow-up cadence skill instead.

## INPUTS
- Target prospect list: CSV or CRM export with columns for first name, company, title, industry, and email address.
- Value proposition brief: 2-3 sentence plain-text description of the product/service and the primary pain point it solves for this segment.
- Sender identity: name, title, company, and a one-line credibility statement (e.g., a customer count or notable client) as plain text.
- Sending constraints: daily send cap and preferred send windows (e.g., "50/day, Tue-Thu 9am-11am recipient local time") as plain text or spreadsheet note.
- Historical open rate for this segment/industry (optional): the average open rate from prior campaigns to this segment, as a percentage. Default: treat as unknown and use the shorter subject-line format (step 7) if no historical figure is supplied.

## WORKFLOW
1. Segment the prospect list by industry and title into groups of no more than 200 contacts each, using the CSV's industry and title columns.
2. Draft a subject line variant set (3 options per email) under 50 characters, referencing the segment's specific pain point rather than the company name.
3. Write Email 1 as a problem-identification message: name a specific, observable trigger (e.g., a recent funding round, job posting, or product launch) pulled from the prospect list's notes column, then ask one low-friction question.
4. Write Email 2 (sent 3 business days later) as a value-proof message: include one quantifiable case example or a link to a relevant resource, and repeat the ask in one sentence.
5. Write Email 3 (sent 4 business days after Email 2) as a re-frame message: approach the same pain point from a different angle (cost of inaction, competitor movement, or a seasonal deadline).
6. Write a breakup email (final touch) that explicitly offers to close the loop and gives the prospect an easy opt-out phrase to reply with.
7. Check the historical open rate input: if it is below 15%, or if no historical figure was supplied, shorten all subject lines to under 30 characters and rewrite the first line of each email to lead with the trigger event instead of a greeting.
8. Insert merge fields for first name, company name, and the specific trigger detail into every email body, and mark any email missing a merge field for manual review.
9. Set the send schedule in the email platform: space touches per the sending constraints input, and stagger by segment to keep daily volume under the cap.
10. Verify every email in the sequence includes a single, unambiguous call to action (one link or one question, never both) before scheduling; flag and rewrite any email with more than one CTA.
11. Log the finished sequence (all four emails, subject line variants, and schedule) to the sequence tracker with the segment name and creation date.

## OUTPUT SPEC
A four-email sequence document (plain text or Google Doc) containing: 3 subject line variants per email, full email body text with merge fields marked in brackets, the send-day offset for each touch (Day 0, Day 3, Day 7, Day 12), and a one-line sequence name. Total length: 150-250 words per email body. Delivered as a single document per segment, filed in the campaign folder alongside the source prospect list.

## EXAMPLE PROMPT
```
Build a 4-email cold sequence for our list of 180 mid-market logistics companies (CSV attached, columns: first_name, company, title, trigger_note). We sell a route-optimization SaaS that cuts fuel spend by identifying inefficient delivery routes. Sender is Maria Chen, VP of Sales at RouteWise. Cap sends at 40/day, Tuesday-Thursday 9-11am recipient time. Target ask: book a 20-minute demo call.
```

## QUALITY CHECKS
- Each email body contains exactly one call-to-action link or question, never zero or more than one.
- Every subject line is under 50 characters and does not contain the word "free" or ALL CAPS words.
- All four required merge fields (first name, company, trigger detail, sender name) are present and non-empty in every email.
- The full sequence totals no more than 5 touches including the breakup email.
