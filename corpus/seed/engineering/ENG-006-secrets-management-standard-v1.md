---
doc_id: ENG-006
title: Secrets Management Standard
department: engineering
classification: internal
acl: [engineering]
version: 1
effective_date: 2024-06-01
status: superseded
superseded_by: ENG-006 v2
owner: security-engineering
---
# Secrets Management Standard

Secrets live in Vault. Environment files must not be committed to repositories.

## Access

Services authenticate to Vault with their workload identity. Engineers on a service team hold standing read access to that service's production secret paths.

## Rotation

| Secret type | Maximum age |
|---|---|
| Database credentials | 180 days |
| Service-to-service tokens | 180 days |
| External API keys | 365 days |

Rotation is manual and tracked by each service team.

## If a secret leaks

Notify security engineering and rotate the secret within 24 hours.
