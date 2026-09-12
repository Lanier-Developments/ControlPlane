---
doc_id: ENG-005
title: Code Review Standards
department: engineering
classification: internal
acl: [engineering]
version: 1
effective_date: 2025-04-01
status: active
owner: engineering-leadership
---
# Code Review Standards

## Reviewers

Tier 1 services require two approving reviews. Tier 2 and 3 require one. Changes to authentication, payment handling, or data retention require one reviewer from the owning team and one from outside it, at any tier.

## Author responsibilities

Keep pull requests under 400 changed lines where possible. A large change is not an argument for a lighter review; it is an argument for splitting the change.

State what the change does and how you verified it. "Tested locally" is not a verification statement.

## Reviewer responsibilities

Respond within one business day. A pull request waiting on review longer than two business days may be escalated to the reviewer's manager by the author, and that is a normal thing to do, not an aggressive one.

Distinguish blocking comments from suggestions. Prefix non-blocking comments with `nit:`. A reviewer who blocks on style preferences that are not in the style guide is overstepping.

## What review is for

Correctness, security, and whether the next person can maintain it. Formatting is the linter's job. Architecture belongs in a design note before the code is written, not in review.

## Emergency changes

During a SEV1, a fix may merge with a single review and a follow-up review within 24 hours. The follow-up is tracked as an incident action item and is not optional.
