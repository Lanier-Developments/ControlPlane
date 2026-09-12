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
| 3 | Retrieval quality | Hybrid (pgvector + full-text), reranking, chunking experiments |
| 4 | Permission-aware retrieval | Owned schema, ACL propagation, Postgres row-level security, leakage suite |
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
