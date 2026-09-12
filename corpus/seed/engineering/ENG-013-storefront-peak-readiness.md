---
doc_id: ENG-013
title: Storefront Peak Readiness Plan
department: engineering
classification: internal
acl: [engineering]
version: 1
effective_date: 2026-10-01
status: active
owner: commerce
---
# Storefront Peak Readiness Plan

Peak runs from the day after Thanksgiving through December 24. Stores are closed Thanksgiving and Christmas Day; the storefront is not.

## Capacity

Load testing targets 4 times the prior year's peak minute. Last year's peak minute was 11,200 requests per second at 10:04 AM Eastern on the day after Thanksgiving.

Capacity is pre-scaled rather than autoscaled during peak. Autoscaling reacts too slowly for a traffic step that arrives in under 90 seconds, so `storefront-web` and `order-orchestrator` run at peak capacity from November 26 through December 26 regardless of load.

## Freeze

Tier 1 change freeze runs November 20 through January 2, matching the deployment policy. The freeze includes configuration changes and feature flag flips that alter the request path.

## Degradation plan

If capacity is exceeded, features are shed in this order: personalized recommendations, then recently-viewed, then reviews, then search autocomplete. Checkout is never shed.

The queue page is enabled only by the incident commander and only above 95 percent capacity, because entering the queue costs conversion even when it protects the site.

## Staffing

Every Tier 1 team runs a doubled rotation through peak: two primaries, no secondary. Stipends follow the on-call policy, counted per rotation, which means both primaries receive the primary rate.

Engineers may not take PTO during the freeze window except for approved holiday coverage swaps arranged before November 1.
