---
name: Proposal Generator
category: sales
summary: Assembles a customized sales proposal document with scoped deliverables, a pricing table, and terms tailored to the specific deal, pulling directly from discovery call findings.
works_with: [sales-agent, finance-agent, crm-agent]
version: 1.0
---

## WHEN TO USE
Use this skill once discovery is complete and the prospect has confirmed interest in moving forward, and you need a formal, deal-specific proposal document to send. Trigger it immediately after a qualified discovery or demo call ends with a stated next step of "send a proposal." Do not use it to send a generic pricing sheet to an unqualified lead — that undermines the customization this skill is built to provide.

## INPUTS
- Discovery findings: the prospect's stated pain points, current tools, and desired outcomes, as call notes or a summary.
- Deal parameters: which product tiers/modules apply, quantity or seat count, and contract length, as plain text or a form.
- Pricing rules: list pricing, any approved discount range, and payment terms available, as a reference sheet.
- Decision-maker and timeline: who signs off and the prospect's stated target start date, as plain text.

## WORKFLOW
1. Open the proposal with a one-paragraph summary that names the prospect's specific stated pain point and desired outcome from the discovery findings, not a generic company overview.
2. Build the scope-of-work section listing only the product tiers/modules confirmed in the deal parameters, each with a one-sentence description of the specific outcome it delivers for this prospect.
3. Construct the pricing table using the pricing rules input: list price per line item, quantity, any applied discount with its approved range, and the total, shown transparently rather than as a single bundled number.
4. If the deal parameters specify a discount request, verify it falls within the approved discount range from the pricing rules before applying it; if it exceeds the range, flag it for approval rather than including it.
5. Draft the implementation timeline section, working backward from the prospect's stated target start date to show key milestones (contract signature, kickoff, go-live).
6. Include a "why us" section limited to the 1-2 differentiators most relevant to the specific pain points raised in discovery, not a full feature list.
7. Draft the terms section covering contract length, payment terms, and renewal/cancellation policy exactly as defined in the pricing rules input, with no invented terms.
8. Insert a clear, single next-step call to action (e.g., "sign by [date] to hold the implementation slot") at the end of the document.
9. Verify every dollar figure in the pricing table sums correctly to the stated total, and that the total matches the sum of line items before the proposal is finalized.
10. Route the completed proposal to the listed decision-maker's name and confirm the send date is logged against the deal record in the CRM.

## OUTPUT SPEC
A formatted proposal document with sections: opening summary, scope of work, pricing table (line items, discounts, total), implementation timeline, why-us, terms, and a single closing call to action. Delivered as a PDF or shareable document, typically 2-4 pages, sent to the named decision-maker and logged on the CRM deal record.

## EXAMPLE PROMPT
```
Generate a proposal for Bright Path Dental Group. Discovery findings: they're losing 15% of hygiene appointments to no-shows and currently use a manual call-reminder process. Deal parameters: our Growth tier (up to 5 locations), 12-month contract. They asked for a 10% discount; our approved range tops out at 12%. Decision-maker is Dr. Elena Ruiz, who wants to start by the 1st of next month.
```

## QUALITY CHECKS
- The pricing table's line-item total sums exactly to the stated grand total, with no arithmetic mismatch.
- Any discount applied is confirmed within the approved discount range from the pricing rules input, or explicitly flagged as needing approval.
- The opening summary references the prospect's specific stated pain point, not generic company language.
- The document contains exactly one clear call to action, not multiple competing next steps.
