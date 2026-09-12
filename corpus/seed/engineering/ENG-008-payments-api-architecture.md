---
doc_id: ENG-008
title: Payments API Architecture
department: engineering
classification: internal
acl: [engineering]
version: 2
effective_date: 2025-09-15
status: active
owner: payments-platform
---
# Payments API Architecture

`payments-api` authorizes and captures card payments for the storefront and for store registers. It is Tier 1.

## Boundaries

The service never stores a primary account number. The card processor returns a token at authorization; that token is what `payments-api` persists and what appears in every downstream system, including reporting.

Refunds reference the original authorization token. A refund without a matching authorization is rejected, which is why a store cannot process a cash-basis refund through this path.

## Flow

1. `order-orchestrator` requests an authorization with an idempotency key derived from the order ID.
2. `payments-api` calls the processor, records the result, and returns the token.
3. Capture happens at fulfillment, not at order placement, so an unshipped order holds an authorization rather than a charge.
4. Authorizations expire after 7 days. `order-orchestrator` re-authorizes expired holds before fulfillment.

## Idempotency

Every mutating endpoint requires an idempotency key. Retries with the same key return the original result rather than creating a second charge. Keys are retained for 30 days.

## Failure behavior

If the processor is unreachable, authorization fails closed: the order is not placed. The service does not queue authorizations for later, because a deferred authorization can succeed against a card the customer believes was declined.

Store registers fall back to the standalone terminal, which settles separately and reconciles the next day.
