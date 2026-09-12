---
doc_id: ENG-002
title: Incident Severity and Response
department: engineering
classification: internal
acl: [engineering]
version: 2
effective_date: 2026-01-10
status: active
supersedes: ENG-002 v1
owner: platform-engineering
---
# Incident Severity and Response

| Severity | Definition | Page | Status updates | Postmortem |
|---|---|---|---|---|
| SEV1 | Revenue stopped or customer data at risk | Immediate, incident commander assigned | Every 30 minutes | Required within 5 business days |
| SEV2 | Major feature unavailable, workaround exists | Immediate to owning team | Every 60 minutes | Required within 10 business days |
| SEV3 | Degraded performance, limited customer impact | Business hours | At resolution | Optional |
| SEV4 | Internal only, no customer impact | Ticket | None | None |

## Roles

For SEV1 the incident commander runs the response and does not debug. A separate communications lead posts updates. The commander may be any trained engineer; seniority does not determine who takes the role.

Anyone may declare an incident. Declaring and being wrong carries no penalty. Not declaring because you were unsure is the failure mode this policy is written against.

## Channels

Each incident gets a dedicated channel named `#inc-<service>-<date>`. The payments team maintains a standing `#inc-payments` channel for its runbook procedures.

## Postmortems

Postmortems are blameless and name systems, not people. Every postmortem produces action items with named owners and due dates. Action items from SEV1 incidents are reviewed weekly until closed.

Severity is set at declaration and may be raised or lowered as understanding improves. Lowering severity requires the commander's agreement.
