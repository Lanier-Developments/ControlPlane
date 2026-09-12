---
doc_id: ENG-002
title: Incident Severity and Response
department: engineering
classification: internal
acl: [engineering]
version: 1
effective_date: 2024-03-01
status: superseded
superseded_by: ENG-002 v2
owner: platform-engineering
---
# Incident Severity and Response

| Severity | Definition | Page | Postmortem |
|---|---|---|---|
| P1 | Site down | Immediate | Required within 10 business days |
| P2 | Major feature unavailable | Immediate | Optional |
| P3 | Degraded performance | Business hours | None |

## Roles

For P1 the on-call engineer leads the response and debugs. A manager posts updates to stakeholders.

Only a manager or staff engineer may declare a P1.

## Channels

Incidents are coordinated in `#engineering-alerts`.

## Postmortems

Postmortems identify the root cause and the individual or team responsible for the change that caused it.
