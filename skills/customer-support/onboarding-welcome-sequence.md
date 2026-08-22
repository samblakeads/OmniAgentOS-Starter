---
name: Onboarding Welcome Sequence
category: customer-support
summary: Builds a multi-touch welcome email or in-app message sequence for new customers, timed to their signup date and tailored to their plan tier and stated goals.
works_with: [support-agent, onboarding-agent, follow-up-agent]
version: 1.0
---

## WHEN TO USE
Use this skill immediately after a new customer signs up or converts from trial to paid, to build the sequence of welcome touches that gets them to first value. Do not use it for existing customers upgrading plans — that needs a plan-change sequence, not a first-time welcome.

## INPUTS
- New customer record: name, plan tier, signup date/time, and any onboarding survey answers about their goal or use case
- The product's defined "first value" milestone for this plan tier (e.g., first project created, first integration connected)
- Existing welcome sequence templates or brand voice guide, if one exists, for tone and formatting
- The delivery channel(s) available: email platform, in-app messaging, or both

## WORKFLOW
1. Pull the customer's signup timestamp, plan tier, and any stated goal from the onboarding survey.
2. Identify the plan tier's defined first-value milestone and confirm whether the customer has already reached it.
3. Draft message 1, sent within 1 hour of signup: a welcome note confirming the account is active and pointing to the single next step toward first value.
4. Draft message 2, sent on day 2, tailored to the stated goal from the survey if one was given, or to the most common use case for that plan tier if not.
5. Check whether the customer reached the first-value milestone before message 3 is due; if yes, switch message 3 to a congratulations-plus-next-feature note instead of a nudge.
6. If the customer has NOT reached the first-value milestone by day 5, draft message 4 as a targeted nudge naming the specific blocked step and offering a specific resource (guide, template, or support contact).
7. Draft message 5 on day 7 (default for Free/Pro tiers) or day 14 (default for Enterprise) checking in on satisfaction and inviting a reply, or a call-booking link for Enterprise tier.
8. Set the send timing for each message relative to signup date, not calendar date, so the sequence self-adjusts to each customer.
9. Match tone and formatting to the brand voice guide for every message in the sequence.
10. Verify every message in the sequence has a send-timing trigger, a subject line, and body text before handing the sequence off to the email platform.

## OUTPUT SPEC
A 4-6 message sequence, each with: send-timing trigger (relative to signup), subject line, body copy (80-150 words), and channel (email or in-app) — formatted for direct import into the email platform's automation builder.

## EXAMPLE PROMPT
```
Build a welcome sequence for a new Pro-tier customer who signed up today and told us in the onboarding survey their goal is "get my team using the shared calendar within the first week." First value for Pro is connecting their first calendar integration.
```

## QUALITY CHECKS
- Every message has a defined send-timing trigger relative to signup date — fail if any message has no timing or uses an absolute calendar date.
- At least one message references the customer's stated goal or plan-tier first-value milestone by name — fail if the sequence is entirely generic.
- The sequence includes a conditional branch for reaching vs. not reaching the first-value milestone — fail if message content never adapts to milestone status.
