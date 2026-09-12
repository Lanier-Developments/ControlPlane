---
doc_id: ENG-001
title: Service Catalog
department: engineering
classification: internal
acl: [engineering]
version: 3
effective_date: 2026-02-01
status: active
owner: platform-engineering
---
# Service Catalog

Every production service is registered here with an owning team and a tier. Tier determines on-call expectations and change controls.

| Service | Owner | Tier | Language |
|---|---|---|---|
| storefront-web | Commerce | 1 | TypeScript |
| payments-api | Payments Platform | 1 | Go |
| inventory-service | Supply Chain Systems | 1 | Java |
| order-orchestrator | Commerce | 1 | Go |
| pricing-service | Merchandising Systems | 2 | Java |
| loyalty-service | Commerce | 2 | Python |
| store-sync | Retail Systems | 2 | Python |
| catalog-search | Merchandising Systems | 2 | TypeScript |
| returns-portal | Commerce | 3 | TypeScript |
| reporting-etl | Data Platform | 3 | Python |
| wren-connector | Corporate Systems | 3 | Python |

## Tiers

**Tier 1.** Customer-facing revenue path. 24/7 on-call, 99.95 percent availability target, change freeze during peak. Two reviewers on every pull request.

**Tier 2.** Degrades the experience but does not stop revenue. Business-hours on-call with after-hours escalation. 99.9 percent target.

**Tier 3.** Internal or deferrable. No dedicated on-call; issues are triaged the next business day. No formal availability target.

## Registering a service

A new service requires an entry here before it can receive production traffic. The entry must name an owning team, not an individual. Services without an owning team are decommissioned after 90 days.
