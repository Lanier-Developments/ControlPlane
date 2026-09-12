---
doc_id: ENG-003
title: Deployment and Release Process
department: engineering
classification: internal
acl: [engineering]
version: 1
effective_date: 2025-10-01
status: active
owner: platform-engineering
---
# Deployment and Release Process

All services deploy through `deployctl`, which wraps the pipeline. Nobody deploys by pushing images directly to a cluster.

## Pipeline stages

1. Build and unit tests on every commit.
2. Integration tests against ephemeral dependencies.
3. Deploy to staging automatically on merge to `main`.
4. Smoke suite against staging.
5. Production deploy, manually triggered for Tier 1 services and automatic for Tier 2 and 3.

## Production deploys

Tier 1 deploys roll out in three waves: 5 percent of pods, then 50 percent, then the remainder, with a 10 minute soak between waves. Error rate above 2 percent during a soak halts the rollout automatically.

Roll back with `deployctl rollback <service>`. Rollback is always safe to run; it restores the previous image and configuration together.

## Change freeze

Tier 1 services are frozen from November 20 through January 2, covering the peak retail period. Freeze exceptions require the service owner and the VP of Engineering, and are limited to fixes for SEV1 and SEV2 incidents.

## Database migrations

Migrations deploy separately from application code and must be backward compatible with the previous release. Expand, migrate, contract: add the new column, dual-write, backfill, then drop the old column in a later release. A migration that requires simultaneous code deployment is rejected in review.
