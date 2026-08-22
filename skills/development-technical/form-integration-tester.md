---
name: Form Integration Tester
category: development-technical
summary: Verifies that a website form correctly captures, validates, and delivers submitted data to its connected destination end-to-end, such as a CRM, email, or spreadsheet.
works_with: [dev-agent, qa-agent, ops-agent]
version: 1.0
---

## WHEN TO USE
Use whenever a new form is built or an existing form's destination or logic changes, before it goes live to real customers. Do not use this for pure visual or copy review of a form — pair it with a QA checker skill for that.

## INPUTS
- URL of the page containing the form (URL)
- The form's intended destination(s): CRM record, email notification address, spreadsheet, or automation platform (text)
- Field list with expected validation rules — required fields, format checks like email/phone (spreadsheet or text)
- Test data set: at least one valid and one intentionally invalid submission per field (text)
- Acceptable auto-responder/confirmation-email delivery window (text, e.g. "under 5 minutes"); default: 5 minutes if not specified

## WORKFLOW
1. Inspect the form's field list against the expected field list from INPUTS to confirm every required field is marked required in the live form.
2. Submit a fully valid test entry using realistic test data and time how long until the confirmation message appears.
3. Verify the submission arrives at every listed destination — check the CRM record, inbox, spreadsheet row, or automation log — and that every field value matches what was submitted.
4. If any field's value is truncated, mismatched, or missing at the destination, flag it as a mapping defect and note which field and which destination is affected.
5. Submit an intentionally invalid entry for each field with a validation rule — malformed email, letters in a phone field — and confirm the form blocks submission with a clear error message.
6. Test required-field enforcement by submitting with each required field empty in turn, confirming the form won't submit until it's filled.
7. Submit a duplicate entry (same email twice) and check whether the destination system creates a duplicate record or correctly updates the existing one, per the intended behavior.
8. Test the form on a slow or interrupted connection if possible, and confirm no partial or corrupted submission reaches the destination.
9. Verify any auto-responder or confirmation email fires within the acceptable window from INPUTS (default: 5 minutes if not specified), recording the actual elapsed time, and that its content matches what's expected.
10. Cross-check the automation platform or webhook log, if used, for the exact payload delivered, confirming no field is silently dropped in transit.
11. Compile all findings into a pass/fail table per field and per destination.

## OUTPUT SPEC
A doc-tool or spreadsheet report titled "Form Integration Test — <form name> — <date>" with a pass/fail table (field x destination), a list of validation defects with reproduction steps, and confirmation-email timing. Lands in the dev/ops tracking folder.

## EXAMPLE PROMPT
```
Test the new "Request a Quote" form on our services page. It should send an email notification to sales@ and create a lead record in our CRM. Fields: name, email (required), phone, company size (dropdown), and project details. Confirm the duplicate-email behavior updates the existing lead rather than creating a new one.
```

## QUALITY CHECKS
- Every field in the expected field list from INPUTS has a pass/fail result recorded for validation behavior — fail if any field is untested.
- Data delivered to every listed destination is checked value-by-value against the submitted test data — fail if only "it arrived" is confirmed without value verification.
- At least one invalid submission per validated field was tested and blocked correctly — fail if validation testing is skipped for any field with a stated rule.
- Duplicate-submission behavior is tested and matches the intended behavior stated in INPUTS — fail if this case is untested.
- Confirmation-email/auto-responder elapsed time is recorded and compared against the stated threshold (or the 5-minute default) — fail if timing is reported without that comparison.
