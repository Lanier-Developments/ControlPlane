---
doc_id: ENG-006
title: Secrets Management Standard
department: engineering
classification: internal
acl: [engineering]
version: 2
effective_date: 2026-01-05
status: active
supersedes: ENG-006 v1
owner: security-engineering
---
# Secrets Management Standard

Secrets live in Vault. Nothing else is an acceptable location: not environment files in the repository, not the CI configuration, not a password manager shared between engineers, not a message pinned in a channel.

## Access

Services authenticate to Vault with their workload identity and receive only the paths their role grants. Humans authenticate through SSO with a maximum 8 hour session.

Standing human access to production secret paths was removed in this version. Access is requested through the break-glass workflow, granted for 4 hours, and logged with the requester, the reason, and the incident or ticket reference.

## Rotation

| Secret type | Maximum age |
|---|---|
| Database credentials | 90 days |
| Service-to-service tokens | 30 days |
| External API keys | 180 days |
| Signing keys | 365 days |

Rotation is automated for database credentials and service tokens. External API keys are rotated manually with a calendar reminder owned by the service team.

## If a secret leaks

Rotate first, investigate second. A secret committed to a repository is considered compromised even in a private repository and even if the commit is amended away, because the object remains reachable.

Report to security engineering within 1 hour of discovery. There is no penalty for reporting your own mistake; there is one for not reporting it.
