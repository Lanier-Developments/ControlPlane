---
doc_id: ENG-012
title: Payments API On-Call Runbook
department: engineering
classification: internal
acl: [engineering]
version: 4
effective_date: 2026-03-15
status: active
owner: payments-platform
---
# Payments API On-Call Runbook

## High error rate alert

Trigger: payments-api 5xx error rate above 5% for 5 consecutive minutes.

1. The alert pages the payments primary on-call through PagerDuty.
2. If the page is not acknowledged within 15 minutes, it escalates to the Platform Engineering lead.
3. Check the most recent deploy. If the alert started within 30 minutes of a deploy, roll back with `deployctl rollback payments-api`.
4. Post status in #inc-payments every 30 minutes until resolved.
