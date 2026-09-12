---
doc_id: ENG-010
title: Data Retention and Deletion Standard
department: engineering
classification: internal
acl: [engineering, legal]
version: 1
effective_date: 2025-07-01
status: active
owner: data-platform
---
# Data Retention and Deletion Standard

Engineering implementation of the records retention schedule. Where this document and the schedule disagree, the schedule governs and this document is wrong and must be corrected.

| Data | Retention | Where enforced |
|---|---|---|
| Order records | 7 years | Warehouse partition drop |
| Payment tokens | 7 years | `payments-api` archival job |
| Customer account (active) | Life of account | n/a |
| Customer account (closed) | 3 years after closure | Deletion job, monthly |
| Web analytics events | 25 months | Warehouse partition drop |
| Application logs | 90 days | Log platform policy |
| Access logs | 2 years | Log platform policy, immutable bucket |
| Loyalty transaction history | 5 years | Deletion job, monthly |

## Deletion requests

Customer deletion requests arrive through the privacy intake and are processed within 30 days. Deletion removes the account and its identifiers; order records are retained in de-identified form because they carry tax and warranty obligations.

De-identification replaces the customer reference with a surrogate and drops name, email, phone, and address. It is not reversible, and there is no recovery path once it runs.

## Backups

Backups are retained 35 days. A deletion request does not rewrite backups. The reconciliation job reapplies deletions after any restore, and this is why a restore is never complete until reconciliation has run.

## Logs

Application logs must not contain customer email addresses, full names, or payment data. The log platform runs a pattern scan and quarantines matching lines, but the scan is a backstop, not a control the service may rely on.
