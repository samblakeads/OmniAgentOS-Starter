---
name: Expense Categorizer
category: finance-reporting
summary: Sorts raw bank and credit-card transactions into consistent expense categories and flags anything mis-coded or missing a receipt, so books stay clean for tax time and monthly review.
works_with: [bookkeeping-agent, finance-agent, tax-agent]
version: 1.0
---

## WHEN TO USE
Use this skill after each billing cycle closes, or before handing books to a bookkeeper or accountant, to make sure every transaction has a category and supporting documentation. Do not use it to reclassify transactions already reviewed and locked in a closed accounting period — corrections there go through a journal entry, not recategorization.

## INPUTS
- Raw transaction export from bank and credit-card feeds: date, merchant, amount, memo (CSV or bank-feed sync)
- Chart of accounts / category list currently used in the accounting software (list)
- Receipt or documentation folder linked to the transactions, if available (file links or scans)
- Receipt-required threshold: dollar amount above which a transaction must have an attached receipt (default: $75)

## WORKFLOW
1. Pull the raw transaction feed for the period from every connected bank and card account.
2. Match each transaction's merchant name against the existing vendor-to-category rule table.
3. Apply the matched category; for unmatched merchants, infer a category from the merchant name and transaction memo.
4. Flag any transaction that lands in "Uncategorized" after step 3 for manual review.
5. If a transaction is over the receipt-required threshold from INPUTS (default $75) and has no attached receipt, flag it as missing documentation.
6. Group flagged transactions by vendor to spot recurring mis-codes rather than fixing them one at a time.
7. If the same vendor has been mis-coded three or more times in the review, propose a new permanent rule for that vendor.
8. Reconcile the categorized transaction total against the bank/card statement total for the period.
9. Deliver the categorized ledger plus a short list of open flags (uncategorized, missing receipt, proposed new rules).

## OUTPUT SPEC
A categorized transaction ledger (CSV or accounting-software import format) with columns: date, merchant, amount, assigned category, receipt status. Accompanied by a short flags list (bulleted, under 20 lines) covering uncategorized transactions, missing-receipt transactions, and any proposed new vendor rules.

## EXAMPLE PROMPT
```
We run a 4-person landscaping company. Pull last month's business card and checking transactions and sort them into our existing accounting software categories. Flag anything over $75 that doesn't have a receipt attached, and tell me if any vendor keeps landing in "Uncategorized."
```

## QUALITY CHECKS
- Every categorized transaction's assigned category exists in the current chart of accounts (no invented categories).
- The sum of categorized transaction amounts matches the source statement total for the period, within $0.01.
- No transaction appears in both the "categorized" ledger and the "uncategorized" flag list at the same time.
- Every transaction above the receipt-required threshold from INPUTS has a documented receipt status (attached or flagged missing).
