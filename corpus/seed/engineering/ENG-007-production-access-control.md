---
doc_id: ENG-007
title: Production Access Control
department: engineering
classification: confidential
acl: [engineering, legal]
version: 1
effective_date: 2025-12-01
status: active
owner: security-engineering
---
# Production Access Control

Confidential. Describes the controls auditors test; do not circulate outside engineering and legal.

## Principle

No standing human access to production data. Access is time-bound, approved, and logged. The control is the audit trail, not the intention.

## Access paths

| Path | Approval | Duration | Logged to |
|---|---|---|---|
| Read-only query (redacted views) | Manager | 8 hours | Access log |
| Read-only query (unredacted) | Director and Legal | 4 hours | Access log and legal register |
| Write or schema change | Director | 2 hours | Access log, change record |
| Break-glass during incident | Incident commander | 4 hours | Access log, postmortem |

Break-glass grants are reviewed weekly. A grant without a matching incident record is treated as a control failure and investigated.

## Customer data

Order and account data is accessible through redacted views by default. Payment instrument data is never accessible to engineers in any path; support workflows use tokenized references.

Exporting production data to a local machine is prohibited. Analysis happens in the analysis environment, which cannot reach the public internet.

## Quarterly review

Every account with any production access is reviewed quarterly by the service owner. Accounts not confirmed within 10 business days of the review opening are disabled automatically, including service accounts.

Departed employees are removed within 24 hours through the offboarding workflow, not at the next review.
