---
name: Refund Request Handler
category: customer-support
summary: Evaluates a customer's refund request against the order record and refund policy window, decides approve, deny, or escalate, and drafts the customer-facing response and processor action.
works_with: [support-agent, billing-agent, escalation-agent]
version: 1.0
---

## WHEN TO USE
Use this skill when a customer emails, chats, or submits a form asking for a refund, credit, or chargeback reversal on a specific order. Do not use it for general billing questions with no refund request attached — route those through the ticket triage skill instead.

## INPUTS
- The refund request text, including the customer's stated reason and the order ID or invoice number referenced
- The order record: purchase date, amount, product/plan, payment processor (e.g., Stripe, PayPal), and current status (paid, disputed, already refunded)
- The written refund policy: eligibility window in days, non-refundable item list, and any prior refund history for this customer
- Customer account standing: number of prior refunds, chargeback history, and current subscription status if applicable

## WORKFLOW
1. Extract the order ID or invoice number from the request; if none is given, search the order history by customer email and purchase date range mentioned.
2. Pull the order record and confirm the purchase date, amount charged, and payment processor.
3. Calculate the number of days between the purchase date and today, and compare it to the refund policy's eligibility window (e.g., 30 days).
4. If the request falls outside the eligibility window, check for a documented exception reason (product defect, service outage, duplicate charge) before denying.
5. Check the customer's refund and chargeback history; if this is their third or more refund request in 90 days, or they have any prior chargeback on file, flag the case for manual review instead of auto-approving.
6. If the request is within the window, not on the non-refundable item list, and has no history flag, mark it Approved and note the refund amount.
7. If the request is outside the window with no exception, or on the non-refundable list, mark it Denied and select the matching policy clause to cite.
8. If the case was flagged for history or ambiguous reasons, mark it Escalate and route to the escalation-agent with a summary of the conflict.
9. Draft the customer-facing reply matching the decision: approval confirms the amount and processing time (e.g., "5-7 business days"); denial cites the specific policy clause; escalation sets expectations for a follow-up window.
10. Verify the decision (Approved/Denied/Escalate), the amount if approved, and the policy clause if denied are all present before sending the reply.

## OUTPUT SPEC
A decision record (Approved / Denied / Escalate), the refund amount and processor action if approved, the policy clause cited if denied, and a customer-facing email reply (100-180 words) matching that decision, ready to send or queue for approval.

## EXAMPLE PROMPT
```
Handle this refund request from a customer at jane.doe@example.com: "I bought the Pro plan on your site 12 days ago (order #48213) and it doesn't do what I expected — I'd like a full refund." Check it against our 30-day refund policy and draft the reply.
```

## QUALITY CHECKS
- The decision field is exactly one of Approved, Denied, or Escalate — fail if blank or any other value.
- If Denied, a specific policy clause is cited in both the internal record and the customer reply — fail if the denial has no cited reason.
- The days-since-purchase calculation in the record matches the actual gap between the order date and today's date — fail on a math mismatch.
