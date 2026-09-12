---
doc_id: ENG-009
title: Inventory Service Runbook
department: engineering
classification: internal
acl: [engineering]
version: 2
effective_date: 2026-02-20
status: active
owner: supply-chain-systems
---
# Inventory Service Runbook

`inventory-service` is the source of truth for on-hand quantity across 42 stores and two distribution centers. Tier 1.

## Alert: stale store counts

Trigger: a store has not reported counts in 90 minutes.

1. Check `store-sync` health first. A stale count is usually a sync problem, not an inventory problem.
2. If `store-sync` is healthy, check the store's network status in the facilities dashboard.
3. A single stale store does not warrant a page during business hours. Three or more stale stores is a SEV2.

## Alert: negative on-hand

Trigger: any SKU at any location below zero.

Negative on-hand almost always means a sale was recorded against inventory that was already committed to an online order. Do not correct the number directly in the database. Run the reconciliation job for that location, which reconstructs the position from the movement ledger.

Direct correction masks the defect and the number drifts again within days.

## Alert: reservation backlog

Trigger: reservation queue depth above 5,000 for 10 minutes.

Scale the reservation workers first. If depth continues rising after scaling, the downstream `order-orchestrator` is likely failing to confirm reservations; check its error rate before adding more workers.

## What not to do

Never replay the movement ledger against a live position. The replay job is designed to run against a rebuilt snapshot, and running it live double-counts every movement in the window.
