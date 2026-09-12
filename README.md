# provenance-rag

A governance-first reference implementation of enterprise RAG.

Every answer should be able to prove four things: what it retrieved, why this user was allowed to see it, which approved model produced it, and that the system passed its eval gates before release.

> Working name. Rename freely before the first public push.

## Principles

- **Permissions are enforced before retrieval**, never by filtering results afterward.
- **Measure before optimizing.** The eval gate lands before any retrieval tuning.
- **Fail closed.** If policy can't be evaluated, the answer is "no", with a logged reason.
- **Retrieved text is data, not instructions.**
- **Governance is framework-agnostic.** LangChain/LangGraph run the pipeline; policy, evidence and evals don't depend on them.

## Roadmap

| Phase | Learning objective | Ships |
|---|---|---|
| 1 | LangChain primitives | Naive RAG with citations over a synthetic corpus (**done**) |
| 2 | Evaluation discipline | Golden set scorer + GitHub Actions ratchet gate (**done**) |
| 3 | Retrieval quality | Hybrid (pgvector + full-text) with RRF, version-aware ranking, comparison harness (**done**) |
| 4 | Permission-aware retrieval | Owned schema, ACL propagation, Postgres row-level security, RLS suite (**done**) |
| 5 | Governed model access | LiteLLM gateway, model registry/allowlist, OPA policy, caching, cost tracking |
| 6 | Evidence and observability | Hash-chained evidence ledger, correlation IDs, Langfuse tracing |
| 7 | Agentic retrieval and red team | LangGraph rewrite/grade loop, poisoned-document test suite |

## Quickstart (Phase 1)

```bash
cp .env.example .env              # point OLLAMA_BASE_URL at your Ollama
make up                           # Postgres + pgvector (add: make up-local-llm)
make pull-models                  # nomic-embed-text + llama3.1:8b
python -m venv .venv && . .venv/bin/activate && pip install -e .
make ingest
make ask Q="How many PTO days do full-time employees get?" USER=sam
make api                          # POST http://localhost:8000/ask
```

## Phase 1 known limitations (intentional)

These are the baseline the later phases are measured against:

- **The `user` field is accepted and ignored.** Sam from engineering can retrieve HR salary bands. Expect `context_leak` on most items, not just G04 and G06: with six documents and top-k of 5, nearly every query pulls in something the persona shouldn't see.
- **No version awareness.** Both versions of the PTO policy are indexed; the answer may cite the retired one.
- **LangChain's PGVector manages its own tables.** Phase 4 replaces them with an owned schema so row-level security can be applied.
- **The model is called directly.** No gateway, registry, or policy yet.
- **Re-ingesting a shrunken document leaves stale chunks.** Use `make reset` until Phase 3 adds incremental sync.

## Eval gate (Phase 2)

```bash
make eval          # retrieval tier: deterministic, no chat model
make eval-full     # adds answer checks: facts, forbidden content, citations, refusals
make baseline      # record current failures into evals/baseline.yaml
```

**Leaks are measured at retrieval, not in the answer.** Once an unauthorized chunk reaches the model's context, it has already been exposed: to the model, to traces, and to any log that captures the prompt. A model that politely declines to repeat the salary band still counts as a leak.

**The gate is a ratchet.** `evals/baseline.yaml` records each known failure with a reason and the phase that fixes it.
- A new failure is a regression, and the build goes red.
- A known failure that starts passing also turns the build red until the baseline is tightened.

The list of accepted failures can only shrink. A permanently red gate trains people to ignore it; a ratchet keeps it honest while being truthful about where the system is today.

In CI the retrieval tier blocks merges. The full tier runs on a small local model (`llama3.2:3b`) and is advisory, because a 3B model's misses are noise, not signal. Point it at Bedrock through GitHub OIDC when it should block.

Refusal detection is a phrase heuristic. Replace it with an LLM judge (faithfulness, answer relevance) when the corpus grows.

## Phase 2 baseline findings

Measured on the six-document seed corpus, top-k 5, `nomic-embed-text` embeddings, `llama3.1:8b` for the answer tier.

**Context leak rate: 10 of 10 items.** Every question in the golden set pulled at least one chunk the asking persona had no entitlement to. Not an edge case — the default behavior of a naive pipeline.

**A contractor got a production rollback command (G06).** Riley's entitlements are `public` only. The question was how to roll back the payments API after a bad deploy. Retrieval returned the on-call runbook, and the model repeated the `deployctl` command with no hesitation: `context_leak`, `answer_forbidden_content` and `no_refusal` all fired on one item.

**The same violation can look clean (G04).** Sam, an engineer, asked for the Senior Engineer II salary band. The confidential compensation document reached the model's context exactly as it did in G06 — but this time the model declined to repeat the figures, so only `context_leak` fired. Identical violation, opposite-looking outcome, decided by nothing more durable than which way the model leaned that run.

That asymmetry is the whole argument for scoring leakage at retrieval. Judged on the answer alone, G04 passes and ships. The chunk was still exposed to the model, to any trace backend, and to every log that captures a prompt. A model's good manners are not an access control.

**Retrieval itself was fine.** Zero missing expected sources: the right documents were always found. The failure is that everything else was found too.

**One genuine quality failure (G05).** Dana is entitled to the salary bands, retrieval returned them, and the model still failed to state the numbers or cite the source — the figures live in a table cell. That one is retrieval and answer quality, not permissions, and it is tagged for Phase 3.

The full run is preserved at `docs/baseline-phase2.json`.

## Retrieval (Phase 3)

```bash
make compare       # vector vs hybrid on the golden set, retrieval tier only
```

**Hybrid, fused with reciprocal rank fusion.** Vector search misses exact tokens — a dollar amount, a command name, a doc ID. Keyword search misses paraphrase. Both run, and RRF fuses the rankings: `score = sum(1 / (k + rank))`. RRF needs no score normalization between cosine distance and `ts_rank`, two scales that have no business being compared directly.

**Superseded versions are demoted, not filtered.** "What did the old PTO policy say?" is a legitimate question, so dropping retired documents would be wrong. The penalty applies only when an active version of the same `doc_id` is also in the candidate set, so a retired document that is the only version available is never pushed below unrelated noise. The context block also labels it `SUPERSEDED` and the prompt tells the model to answer from the active version.

**No retrieval change ships without a comparison.** `make compare` runs both modes over the golden set and prints leaks, retrieval misses, and superseded-in-context counts, plus which items changed. Retrieval tier only, so it is deterministic — no model in the loop.

Tunables in `.env`: `RETRIEVAL_MODE`, `FETCH_K`, `RRF_K`, `VECTOR_WEIGHT`, `KEYWORD_WEIGHT`, `PREFER_ACTIVE`, `SUPERSEDED_PENALTY`.

Not yet done in Phase 3: a cross-encoder reranker (deliberately deferred — it adds a heavy dependency for gains the fusion may already cover; measure first) and incremental sync with delete handling.

## Permissions (Phase 4)

```bash
make reset-db && make migrate && make ingest   # fresh volume, owned schema
make rls-test                                  # prove the database enforces
make eval                                      # leak count should be 0
```

**Enforcement is in the database, not the application.** Retrieval runs as `rag_app`, a `NOBYPASSRLS`, SELECT-only role. Row-level security policies on `chunks` and `documents` grant visibility only through `doc_acl`. Chunks the caller may not see are not filtered out of the results — they are never in the result set, so there is no post-filter to forget to apply, and a bug in the Python layer cannot widen what a query returns.

**Entitlements are transaction-scoped.** Each request opens a transaction and issues `SET LOCAL app.groups`. The scope dies with the transaction, so a pooled connection can never carry one caller's entitlements into the next caller's query.

**Fail closed by construction.** `current_setting('app.groups', true)` returns NULL when unset, which matches no rows. An unauthenticated caller sees nothing because of how the policy is written, not because of a Python check that could be skipped. An unknown principal raises rather than defaulting to a group.

**The gate refuses to run unless enforcement is on.** `assert_rls_enforced()` checks at startup and before every eval run that the retrieval role does not bypass RLS and sees nothing with no groups set. It guards the failure that would otherwise be silent — someone pointing `APP_DATABASE_URL` at the owner role, which is exempt from every policy. A green eval gate has to mean enforcement was actually applied.

**`make rls-test` bypasses the application entirely** and queries as the app role directly: per-persona visible counts, specific forbidden documents (Sam must not reach HR-007, Riley must not reach ENG-012), and that entitlements do not survive the transaction that set them. Expected counts are computed from the corpus files rather than the database, so the test cannot agree with a bug in ingest by sharing its source of truth.

**Refusals do not disclose.** When nothing retrievable matches, the answer is that no available information covers it — not "you lack permission to see that," which confirms the document exists.

**ACLs propagate at ingest.** Grants are rewritten on every ingest rather than added to, so a revoked group actually disappears. Chunks above the current count are deleted, so a shortened document leaves no orphans. A document with an empty `acl` is rejected: there is no implicit default.

## Layout

```
corpus/SPEC.md        synthetic company, personas, frontmatter schema, deliberate traps
corpus/seed/          six hand-written docs so Phase 1 runs immediately
db/init.sql           pgvector extension
evals/golden.yaml     golden set
evals/personas.yaml   persona -> groups
evals/baseline.yaml   ratchet: known failures with reasons
.github/workflows/    eval-gate (retrieval blocks, full advisory)
src/provenance/       config, store, ingest, chain, api
```
