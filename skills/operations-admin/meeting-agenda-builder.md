---
name: Meeting Agenda Builder
category: operations-admin
summary: Builds a time-boxed meeting agenda from the stated purpose and topic list, sequencing items by priority and attaching owners so the meeting stays on track.
works_with: [admin-agent, scheduling-agent, delegation-agent]
version: 1.0
---

## WHEN TO USE
Use this skill before a scheduled meeting once you know its purpose, attendees, and rough topic list, to turn that into a time-boxed agenda that keeps the meeting focused. Do not use it for an unstructured brainstorm or open-forum session where a fixed agenda would work against the goal.

## INPUTS
- The meeting's stated purpose and total scheduled duration
- The list of topics or discussion items to cover, gathered from the organizer, attendee submissions, or a running backlog document
- Attendee list with roles, to determine which topics need which people present and who should own presenting each item
- Any carried-over open items from the prior meeting on this same recurring series, if applicable

## WORKFLOW
1. Confirm the meeting's total duration and stated purpose before building anything else, since every time-box depends on the total.
2. List every topic to be covered, pulling from the organizer's list, any attendee-submitted items, and carried-over open items from the prior meeting.
3. For each topic, identify the specific outcome needed, such as a decision, update, brainstorm, or approval, rather than leaving it as a vague discussion label.
4. Rank topics by priority: anything with a hard deadline or blocking another team's work goes first; informational updates go last.
5. Assign a time box to each topic based on its complexity and priority, reserving the final 5-10% of total duration for the wrap-up buffer (step 8) so the topic time boxes alone don't exceed the remaining duration.
6. If the topic list doesn't fit within the total duration even after time-boxing, cut or defer the lowest-priority items rather than compressing every item's time box unrealistically.
7. Assign an owner to present or lead each topic, matching to the attendee most responsible for that area.
8. Confirm the wrap-up buffer already reserved in step 5 (5-10% of total duration) is untouched by any topic's time box, rather than scheduling to the exact minute.
9. Note next to each topic whether it needs a decision made in the meeting or is informational only, so attendees arrive prepared appropriately.
10. Verify the sum of all time boxes plus the buffer equals the total scheduled duration before finalizing the agenda.

## OUTPUT SPEC
A time-boxed agenda: meeting purpose and duration at the top, then a table of topics with time allotment, owner, and outcome type (decision/update/brainstorm/approval), ending with a reserved wrap-up buffer — formatted to share with attendees ahead of the meeting.

## EXAMPLE PROMPT
```
Build the agenda for our 30-minute weekly leadership sync. Topics this week: Q3 budget decision (needs approval), marketing campaign update from Priya, two carried-over items from last week about the office lease, and a quick heads-up on the new hire starting Monday.
```

## QUALITY CHECKS
- The sum of all topic time boxes plus the wrap-up buffer equals the total scheduled meeting duration — fail if the math doesn't add up.
- Every topic has an assigned owner and an outcome type (decision/update/brainstorm/approval) — fail if either field is missing.
- Carried-over items from the prior meeting are explicitly included, not dropped — fail if a noted open item disappears from the new agenda.
