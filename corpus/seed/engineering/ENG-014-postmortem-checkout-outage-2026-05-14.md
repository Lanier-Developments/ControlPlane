---
doc_id: ENG-014
title: Postmortem — Checkout Outage 2026-05-14
department: engineering
classification: internal
acl: [engineering]
version: 1
effective_date: 2026-05-21
status: active
owner: commerce
---
# Postmortem — Checkout Outage 2026-05-14

SEV1. Checkout unavailable for 47 minutes, 14:22 to 15:09 Eastern. Approximately 2,900 orders were not placed during the window.

## What happened

A pricing rule change introduced a promotion with no end date. `pricing-service` evaluated the open-ended rule against every cart line rather than short-circuiting on the date range, and response times rose from 40ms to 3.2 seconds. `order-orchestrator` held connections waiting on pricing, exhausted its pool, and stopped accepting new checkout requests.

## Detection

The first signal was a customer support volume alert, not a service alert. `order-orchestrator` reported healthy because its health check does not exercise the pricing dependency. Detection took 11 minutes longer than it should have.

## Resolution

The on-call engineer disabled the promotion through the merchandising console at 15:02. Connection pools recovered without a restart by 15:09.

## Contributing factors

The rule editor accepts an empty end date. There is no load test covering the open-ended rule path. The health check is shallow.

## What we are not saying

The merchandiser who created the rule did nothing wrong. A system that turns a valid console entry into a site outage is the defect.

## Action items

1. Reject rules without an end date in the editor. Owner: Merchandising Systems. Due June 12.
2. Short-circuit pricing evaluation on date range before line evaluation. Owner: Merchandising Systems. Due June 5.
3. Deep health check for `order-orchestrator` covering pricing and inventory. Owner: Commerce. Due June 19.
4. Add an open-ended rule case to the load suite. Owner: Commerce. Due June 26.
