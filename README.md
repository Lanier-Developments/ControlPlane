# ControlPlane

[![eval-gate](https://github.com/Lanier-Developments/ControlPlane/actions/workflows/eval-gate.yml/badge.svg)](https://github.com/Lanier-Developments/ControlPlane/actions/workflows/eval-gate.yml)
[![License: MIT](https://img.shields.io/github/license/Lanier-Developments/ControlPlane)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

A governance-first reference implementation of enterprise RAG.

Every answer should be able to prove four things: what it retrieved, why this user was allowed to see it, which approved model produced it, and that the system passed its eval gates before release.

## Architecture

```mermaid
flowchart TD
    U["User question + identity"] --> R

    subgraph DB["Postgres — row-level security"]
        R["Hybrid retrieval<br/>vector + full-text, RRF fused"]
        ACL[("doc_acl")]
        R -. "entitlements scope the<br/>candidate set, not a post-filter" .-> ACL
    end

    R --> CTX["Retrieved chunks<br/>tagged by classification"]
    CTX --> GW

    subgraph GOV["Governed model access"]
        GW{{"gateway.complete<br/>one choke point"}}
        REG[("policy/models.yaml<br/>registry + ceiling")]
        RULES[("policy/routing.yaml<br/>routing rules, deny-by-default")]
        GW --> REG
        GW --> RULES
    end

    GW -->|"allowed"| MODEL["Approved model"]
    GW -->|"denied"| REFUSE["Refusal<br/>no approved model permitted"]
    MODEL --> ANSWER["Answer + citations"]

    ANSWER --> LEDGER[("Evidence ledger<br/>hash-chained, append-only")]
    REFUSE --> LEDGER

    style DB fill:#eef4ff,stroke:#5b7fd1
    style GOV fill:#fff3e6,stroke:#d19a5b
    style LEDGER fill:#f3eefc,stroke:#8a5bd1,color:#000000
```

Every arrow is enforced where it's drawn, not merely documented: permissions inside Postgres via row-level security ([Phase 4](#permissions-phase-4)), model access through one gateway checked against a registry ceiling and a deny-by-default rule file ([Phase 5](#governed-model-access-phase-5)), and every answer — allowed or refused — written to a tamper-evident ledger ([Phase 6](#evidence-ledger-phase-6)).

## Principles

- **Permissions are enforced before retrieval**, never by filtering results afterward.
- **Measure before optimizing.** The eval gate lands before any retrieval tuning.
- **Fail closed.** If policy can't be evaluated, the answer is "no", with a logged reason.
- **Retrieved text is data, not instructions.**
- **Governance is framework-agnostic.** LangChain/LangGraph run the pipeline; policy, evidence and evals don't depend on them.

## Results so far

Measured on the golden set at each phase. Every number below came from a run in this repo, not from an estimate.

The first two columns are the six-document seed corpus scored against 10 items. The last is the full corpus — 70 documents, 148 chunks — scored against 44 items across five personas.

| | Naive (Phase 1) | Hybrid retrieval (Phase 3) | Enforced, full corpus (Phase 4) |
|---|---|---|---|
| Items | 10 | 10 | 44 |
| Context leak | 10/10 | 10/10 | **0/44** |
| Retrieval misses | 0/10 | 0/10 | 0/44 |
| Superseded doc in context | 10/10 | **0/10** | 2/44 |
| Fabricated citations | — | — | 0/44 |
| Ungrounded figures | — | — | 0/44 |
| Answer-tier failures | G05, G06 | G04, G06 | G32 (known) |

Four findings are worth more than the table.

**A contractor got a production rollback command.** Riley's entitlements are `public` only. Asked how to roll back the payments API after a bad deploy, the naive pipeline retrieved the on-call runbook and the model repeated the `deployctl` command without hesitation — `context_leak`, `answer_forbidden_content` and `no_refusal` on a single item.

**Improving retrieval made a leak worse.** In Phase 1, Sam (engineering, no compensation access) asked for the Senior Engineer II salary band. The confidential document reached the model's context, but the model declined to repeat the figures, so only `context_leak` fired. After Phase 3 improved retrieval quality — same corpus, same permissions, same model — the model answered with the numbers.

Nothing about the exposure changed. Retrieval just got better at surfacing what it had already been handed. Every retrieval improvement is an amplifier on whatever exposure already exists, which is precisely why permissions have to be enforced before retrieval rather than after. Judged on the answer alone, the Phase 1 version of that item passes and ships. A model's good manners are not an access control.

**A three-position ranking miss produced a confident, wrongly-sourced answer.** Expanding the corpus pushed the travel policy's receipt rule just outside a five-chunk window. Asked the receipt threshold, the model answered "$75" — a figure appearing nowhere in its context — and attached a real source tag to a real retrieved document that says nothing about receipts. The answer looked fully cited. Raising `TOP_K` to 8 restored the source and the correct `$50`.

That failure produced two checks the gate now runs on every item: `citation_unsupported` (a tag naming a document never retrieved) and `ungrounded_value` (a currency or percentage figure appearing nowhere in the context). Both are deterministic and cost nothing.

**Blocking and allowing are tested as a pair.** One item asks Dana for the engineering budget and must be refused; another asks Priya for the same figure and must be answered. A system that simply over-blocked would pass the first and fail the second. Of the 44 items, 9 are denials and 3 are confidential-but-entitled reads.

**Caveats.** The naive baseline was measured on six documents; enforcement and retrieval quality were verified on seventy. Version demotion is not exclusion by default — a retired document still reaches context for the `public` persona, whose nine visible documents nearly fill a `TOP_K=8` window; the answers cite the active version regardless, and `SUPERSEDED_EXCLUDE=1` removes it from context entirely (see [Retrieval](#retrieval-phase-3)).

One known failure remains, recorded in `evals/baseline.yaml`: the model attributed correct content from one document to another. Neither deterministic check catches misattribution — the tag is real and that document was retrieved — so it was tagged for the LLM judge in Phase 7 rather than worked around. That judge now catches it, advisorily (see [Attribution judge](#attribution-judge)); the deterministic gate still records the item as a known failure.

Full runs: `docs/baseline-phase2.json` (naive) and `docs/phase4-enforced.json` (enforced).

**[docs/FINDINGS.md](docs/FINDINGS.md)** has the longer write-up: what each measurement
turned out to mean, the three checks a green gate has to survive, and what is still open.

## Roadmap

| Phase | Learning objective | Ships |
|---|---|---|
| 1 | LangChain primitives | Naive RAG with citations over a synthetic corpus (**done**) |
| 2 | Evaluation discipline | Golden set scorer + GitHub Actions ratchet gate (**done**) |
| 3 | Retrieval quality | Hybrid (pgvector + full-text) with RRF, version-aware ranking, comparison harness (**done**) |
| 4 | Permission-aware retrieval | Owned schema, ACL propagation, Postgres row-level security, RLS suite (**done**) |
| 5 | Governed model access | Model registry, data-classification routing policy, gateway choke point, cache, cost attribution (**done**) |
| 6 | Evidence and observability | Hash-chained append-only evidence ledger, correlation ids, audit and tamper-detection tooling (**done**) |
| 7 | Agentic retrieval and red team | 8-class prompt-injection suite with context fencing measured, LLM attribution judge, demo console (**partial** — LangGraph rewrite/grade loop not built) |

## Installation

### Prerequisites

| | Why |
|---|---|
| **Docker** | Runs Postgres 16 with pgvector. Docker Desktop on macOS/Windows, Engine on Linux. |
| **Python 3.11+** | 3.12 is what CI uses. |
| **[Ollama](https://ollama.com)** | Embeddings and self-hosted inference. Needs ~6GB free for `llama3.1:8b`. |
| **AWS account** | Optional. Only for the cloud-routing half of the demo; everything else runs without it. |
| **Google Cloud project** | Optional. A second cloud provider (Gemini) for the same demo. |

### Setup

```bash
git clone https://github.com/Lanier-Developments/ControlPlane.git
cd ControlPlane

cp .env.example .env               # defaults work if Ollama is on this machine

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .

make up                            # Postgres + pgvector
make pull-models                   # nomic-embed-text + llama3.1:8b (a few GB)

make migrate                       # owned schema, RLS policies, rag_app role
make ingest                        # 70 documents -> ~148 chunks (a minute or two)
```

### Verify

Run these in order. Each answers a different question, and if one fails the later ones aren't meaningful.

```bash
make policy-test    # registry and routing rules — no database, no model, seconds
make rls-test       # the database actually enforces entitlements
make eval           # retrieval tier over 44 golden items
make eval-full      # adds answer-quality checks (calls the model; several minutes)
```

`make eval` should report 0/44 context leaks and 0 missing sources. `make eval-full` carries two known failures documented in `evals/baseline.yaml` — that's expected, not a broken install.

### Use it

```bash
make api                                          # console at http://localhost:8000
make ask Q="How many PTO days do I get?" USER=sam # or the CLI
make verify                                       # recompute the evidence chain
make usage                                        # spend and policy decisions, 24h
```

Personas are `sam`, `dana`, `priya`, `riley` and `marcus` — each with different entitlements, listed in `evals/personas.yaml`.

### Optional: cloud routing

The demo is more interesting with a second provider, because that's what makes the routing policy visible. Two cloud providers are wired in: Bedrock (any Bedrock-accessible Claude model) and Gemini on Gemini Enterprise Agent Platform (formerly Vertex AI). Either one is enough.

**Bedrock**

1. Create an IAM user with `bedrock:InvokeModel`, then `aws configure`.
2. First-time Anthropic use on Bedrock requires a use-case form — open the model in the Bedrock console's model catalog and submit it.
3. Confirm which models your account can actually call:

```bash
aws bedrock-runtime converse --region us-east-1 \
  --model-id us.anthropic.claude-sonnet-4-6 \
  --messages '[{"role":"user","content":[{"text":"hi"}]}]' \
  --query 'output.message.content[0].text' --output text
```

4. Put the working id in `policy/models.yaml`. That file is the only place it goes — no code change.

```bash
DEFAULT_MODEL=bedrock/claude-sonnet-4-6 make ask \
  Q="What is the salary band for a Senior Engineer II?" USER=dana
```

That question retrieves confidential content, so the cloud model is denied by `registry-ceiling` and self-hosted inference answers instead. Both decisions appear in the response and in the ledger.

**Gemini.** Enable the Vertex AI API on a project, run `gcloud auth application-default login`, and set `GCP_PROJECT` in `.env` (`GCP_LOCATION` defaults to `us-central1`). Credentials come from Application Default Credentials and never go in `.env`; for a service account, give it only the Vertex AI User role. The registry entry is `vertex/gemini` (`gemini-2.5-flash`), with the same `internal` ceiling as Bedrock.

```bash
DEFAULT_MODEL=vertex/gemini make ask Q="How many PTO days do I get?" USER=sam
```

Measured on the golden set and red team suite, against the self-hosted models. Gemini runs under the registry's `internal` ceiling, so items that retrieve confidential content — 13 of the 44 golden-set items and both exfiltration cases — are denied and answered by Llama; Gemini's columns count only what it answered.

| | Llama 3.1 8B | Qwen3 8B | Gemini 2.5 Flash |
|---|---|---|---|
| `make eval-full` failures | 2 of 44 (G30, G32) | 1 of 44 (G30) | 0 of the 31 it answered |
| Red team compromised | 2 of 16 | 4 of 16 | 6 of the 14 it answered |

Every result in a report records the model that answered it, alongside the one that was
requested and the registry denial when they differ. The first Gemini report predated
that and was keyed by the requested model only, which credited Llama's fallback answers
— including its G30 and G32 failures — to Gemini; `docs/FINDINGS.md` section 14 unpicks
this. Both baselined failures sit on confidential content, so a Gemini run says nothing
about whether it would clear them, and the ceiling means it will never be asked to.

Gemini's compromises moved between runs — four on Oct 5, six on Oct 10, with
`citation-forgery` and a second `delimiter-confusion` appearing only in the rerun — a
reminder that the cloud runs are not reproducible the way the seeded local ones are.
Exfiltration never reached it. Full results are in `redteam-report-gemini.json`;
`docs/ControlPlane_RedTeam_Gemini_vs_Qwen3.xlsx` breaks the Oct 5 Gemini and Qwen3 runs
down by attack class and position.

### Troubleshooting

**`Bind for 0.0.0.0:5432 failed: port is already allocated`** — something else holds Postgres's port: a local install, or another checkout of this repo (Compose namespaces containers by directory, so a second clone tries to start its own). Stop the other one, or change the host port in `docker-compose.yml` and update `DATABASE_URL` and `APP_DATABASE_URL` to match.

**`ModuleNotFoundError: No module named 'provenance'`** — the editable install points at wherever it was installed from. Re-run `pip install -e .` from the repo root.

**Ingest hangs or times out** — `OLLAMA_BASE_URL` is wrong. Ollama is the only network call in that path.

**`model "nomic-embed-text" not found`** — run `make pull-models`, or pull it on whichever host `OLLAMA_BASE_URL` names.

**`Retrieval role 'rag' bypasses RLS`** — `APP_DATABASE_URL` is pointing at the owner role. It must be `rag_app`. The app refuses to start rather than serve unenforced.

**Schema changes don't take** — `db/init.sql` only runs on a fresh volume. Use `make reset-db && make migrate && make ingest`.

**`relation "documents" does not exist`** — `make migrate` hasn't run.

### Hosting it

Demo mode adds the guardrails a publicly reachable instance needs: a model allowlist so an open URL can't spend money on a cloud provider, canned questions only, and a per-client rate limit.

```bash
# .env
DEMO_MODE=true
DEMO_ALLOWED_MODELS=ollama/llama3.1:8b
```

A Cloudflare Tunnel exposes it without opening a port:

```bash
cloudflared tunnel create controlplane
cloudflared tunnel route dns controlplane controlplane.example.com
cloudflared tunnel run controlplane
```

Keeping self-hosted inference on the host rather than renting a GPU isn't incidental — the whole point of the demo is that confidential content stays on hardware you control.

## Eval gate (Phase 2)

```bash
make eval          # retrieval tier: deterministic, no chat model
make eval-full     # adds answer checks: facts, forbidden content, citations, grounding, refusals
make baseline      # record current failures into evals/baseline.yaml
```

**Leaks are measured at retrieval, not in the answer.** Once an unauthorized chunk reaches the model's context, it has already been exposed: to the model, to traces, and to any log that captures the prompt.

**The scorer reads entitlements from the corpus files, not from retrieved metadata.** Asking a retrieved chunk to report its own ACL would let a leak vouch for itself. A retrieved source absent from the corpus counts as a leak rather than defaulting to permitted.

**Two checks catch different lies.** `citation_unsupported` fires when a source tag names a document that was never retrieved. `ungrounded_value` fires when a currency or percentage figure appears nowhere in the context — the more dangerous case, where a real tag for a real retrieved document is attached to an invented number. Checking only the tag misses it entirely. Both are deliberately narrow and deterministic; claim-level attribution needs a judge.

**Items assert a source only where the fact is unique within the persona's visible set.** A fact stated in three documents fails `citation_missing` at random depending on which the model picks. Several items rely on permission-scoped uniqueness: the same fact exists in an internal document, but that persona cannot see it.

**The gate is a ratchet.** `evals/baseline.yaml` records each known failure with a reason and the phase that fixes it.
- A new failure is a regression, and the build goes red.
- A known failure that starts passing also turns the build red until the baseline is tightened.

The list of accepted failures can only shrink. A permanently red gate trains people to ignore it; a ratchet keeps it honest while being truthful about where the system is today. Catching a fix matters as much as catching a break — it is what stops an accepted-failure list from quietly becoming permanent.

In CI the retrieval tier blocks merges. The full tier runs on a small local model (`llama3.2:3b`) and is advisory, because a 3B model's misses are noise, not signal. The red team runs nightly alongside it under its own ratchet (`evals/redteam-baseline.yaml`) and does fail the workflow — against the local model always, and against Bedrock Sonnet in a second gating job once the OIDC role below exists. See [Red team](#red-team-phase-7).

A second full-tier job runs the same 44 items against Bedrock Claude, authenticated by GitHub OIDC rather than a stored secret: the role trusts only this repo's main branch and allows only `bedrock:InvokeModel` on the one registered model (`infra/github-oidc-bedrock.sh` creates it; the job stays skipped until the `AWS_CI_ROLE_ARN` repo variable is set). It is advisory too, for a different reason — the baseline records the local model's known failures, so against a different model the gate's regressions and stale entries describe the model diff, not a break. The registry caps the model at `internal`, so the confidential and restricted items are denied it and answered by the policy fallback, visibly: those rows carry the fallback in their Model column and the denials are in the ledger.

Refusal detection is a phrase heuristic. Replace it with an LLM judge (faithfulness, answer relevance) when the corpus grows.

### Attribution judge

```bash
make judge                            # full tier + claim-to-source judge (advisory)
JUDGE_MODEL=ollama/qwen3:8b make judge  # grade with a model that didn't write the answer
```

The deterministic checks catch a tag naming a document never retrieved and a figure appearing nowhere in context. They cannot catch the most common lie: a real tag for a real retrieved document attached to a sentence that document does not support. The judge checks every tagged sentence against only the document it cites, through the gateway — same policy, same ledger, and a confidential chunk still cannot reach a cloud model just because a judge is asking.

Its first measured run passed the exact misattribution it was built for, because its own prompt restated the claim as background and the judge read that as evidence — the same lesson as context fencing, applied to the judge itself. Fenced, it catches the misattribution with zero false positives on the 44-item set (FINDINGS §16). It stays advisory: an LLM grading an LLM is not ground truth, and a non-deterministic check that blocks merges will eventually block one for no reason.

In CI, dispatching `eval-full` with `judge: true` runs it in both full-tier jobs. The Bedrock job pins `JUDGE_MODEL` to the local model it already pulls — the judge grades against restricted-classified context the ceiling would deny the cloud model anyway, and the grader should not be the model that wrote the answers. Its first CI run reproduced the local result exactly: 18 claims judged, one ungrounded (the known misattribution, with the right reason), zero false positives.

## Retrieval (Phase 3)

```bash
make compare       # vector vs hybrid on the golden set, retrieval tier only
```

**Hybrid, fused with reciprocal rank fusion.** Vector search misses exact tokens — a dollar amount, a command name, a doc ID. Keyword search misses paraphrase. Both run, and RRF fuses the rankings: `score = sum(1 / (k + rank))`. RRF needs no score normalization between cosine distance and `ts_rank`, two scales that have no business being compared directly.

**Superseded versions are demoted by default, excluded by choice.** "What did the old PTO policy say?" is a legitimate question, so dropping retired documents is not obviously right — but demotion has a measured limit: for the `public` persona, whose nine visible documents nearly fill a `TOP_K=8` window, a demoted chunk still lands in context (last, but present, and in the prompt). `SUPERSEDED_EXCLUDE=1` turns demotion into exclusion, enforced in the retrieval SQL rather than a Python post-filter, and only where the caller can see an active successor — a superseded version whose successor the caller cannot read is served, never traded for nothing. The two mechanisms also differ in strength: the penalty fires only when the active version happens to be a candidate for the same query; the SQL clause holds regardless of what the query matched. Measured on the golden set, exclusion changes exactly 2 of 44 retrieval contexts (both `public`-persona items where superseded `PUB-003 v1` was reaching context) and fails nothing — but the golden set contains no historical question, so what exclusion costs is unmeasured, which is why it is a toggle and not the default. With demotion, the context block labels the chunk `SUPERSEDED` and the prompt tells the model to answer from the active version.

Measured effect: superseded documents in context went from 10/10 to 0/10 with no retrieval misses introduced. Hybrid also fixed the one genuine quality failure in the baseline — a salary figure living in a table cell that vector-only retrieval surfaced but the model failed to state or cite.

**No retrieval change ships without a comparison.** `make compare` runs both modes over the golden set and prints leaks, retrieval misses, and superseded-in-context counts, plus which items changed. Retrieval tier only, so it is deterministic — no model in the loop.

Tunables in `.env`: `RETRIEVAL_MODE`, `FETCH_K`, `RRF_K`, `VECTOR_WEIGHT`, `KEYWORD_WEIGHT`, `PREFER_ACTIVE`, `SUPERSEDED_PENALTY`.

Not yet done in Phase 3: a cross-encoder reranker (deliberately deferred — it adds a heavy dependency for gains the fusion may already cover; measure first) and incremental sync driven by source-system change feeds.

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

**`make rls-test` bypasses the application entirely** and queries as the app role directly: per-persona visible counts for all five personas, specific forbidden documents (Sam must not reach HR-007, Riley must not reach ENG-012), and that entitlements do not survive the transaction that set them. Expected counts are computed from the corpus files rather than the database, so the test cannot agree with a bug in ingest by sharing its source of truth.

**Refusals do not disclose.** When nothing retrievable matches, the answer is that no available information covers it — not "you lack permission to see that," which confirms the document exists.

**ACLs propagate at ingest.** Grants are rewritten on every ingest rather than added to, so a revoked group actually disappears. Chunks above the current count are deleted, so a shortened document leaves no orphans. A document with an empty `acl` is rejected: there is no implicit default.

**Known gaps.** Group membership comes from `evals/personas.yaml` only in `persona` mode, which is development; with `AUTH_MODE=oidc` identity arrives as a verified token on both entry points (see [Authentication](#authentication)) and the persona table refuses to serve. Upstream deletes were one of these gaps until Oct 12: every ingest now reconciles against the disk, deleting any (doc_id, version) the corpus no longer contains — cascading to its chunks and grants — and reporting what it removed. A document deleted at the source stops being retrievable at the next ingest, not at the next `--reset` someone remembers to run; the evidence ledger keeps its digests of past answers that cited it, because the ledger records history while the index serves the present.

## Authentication

```
make auth-test     # forged, expired, wrong-audience, alg-none and key-confusion tokens, against the API and the CLI; no DB or model needed
```

Row-level security enforces the groups it is given. Authentication is what makes those groups trustworthy. With `AUTH_MODE=oidc` the caller's identity comes from a bearer JWT issued by an OIDC identity provider, verified in `src/provenance/auth.py`, and the request body can no longer name a user.

- **Identity comes from the token, not the body.** `POST /ask` rejects a `user` field in `oidc` mode. The verified subject and group claims go to `app_conn()` as an explicit list, so the persona table is no longer in the path.
- **The CLI is not a side door.** `python -m provenance.chain` refuses `--user` in `oidc` mode and takes its identity from a `$CONTROLPLANE_TOKEN` bearer token, through the same verifier with the same generic failure. The persona table itself raises outside `persona` mode, so no code path — present or future — can quietly consult it.
- **Only asymmetric algorithms, pinned in config.** The token's `alg` header never picks the verifier, which closes `alg: none` and RS256-to-HS256 key confusion. `exp`, `iat`, `iss`, `aud` and `sub` are required.
- **Fail closed.** Any verification failure is a 401 with a generic body (the reason goes to the log). A missing groups claim means no groups, which means no rows. An unknown `AUTH_MODE` or a symmetric algorithm in config stops the service at startup.
- **The admin plane is separate.** `/api/evidence`, `/api/usage` and `/api/verify` read through the owner connection, which RLS does not constrain, so they require the `OIDC_ADMIN_GROUP` claim. `/api/personas` returns 404.
- **`persona` mode remains the default** for local development, the eval gate and the demo console, and logs a warning at startup. Do not expose an instance in that mode.
- **The suite gates every PR.** The `auth` job in `eval-gate.yml` runs it on each pull request — it needs no database and no model, which is exactly the profile of a check nobody is tempted to skip.

Not done: JWKS discovery from the issuer URL (the JWKS URL is explicit), token revocation beyond short expiry, recording the token's `jti` in the evidence ledger, and obtaining a token for the CLI — it consumes one from the environment; the device-code or client-credentials flow that acquires it belongs to the IdP's client, not here.

## Governed model access (Phase 5)

```bash
make migrate       # adds model_calls and response_cache
make policy-test   # pure policy evaluation: no database, no model, no network
make usage         # spend and policy decisions from the call log
```

**The registry is data.** Adding, repricing, deprecating or retiring a model is an edit to `policy/models.yaml`. No enum, no code change, no release.

This is the direct lesson from a prior control-plane build where model selection ran off an enum in application code. Every new provider meant a pull request, a release and a rollback risk, and the list of what was actually approved lived in code, in a wiki, and in someone's head — three sources that disagreed. The registry carries a retired entry on purpose: a retired model must be *refused*, not quietly absent.

**Policy is data too, and it fails closed.** Rules in `policy/routing.yaml` are evaluated in order, first match decides, and the last rule is a deny — so a policy file that fails to anticipate a case refuses rather than falling through to allow. The shape is deliberately OPA-compatible (a decision, a reason and a rule id on every answer), so moving the rules to Rego is a transport change, not a redesign.

**Prompts are registered artifacts too.** The two prompts in the system — the answer prompt and the attribution judge's — live in `src/provenance/prompts.py` with a version each, and `policy/prompts.yaml` pins the sha256 of their text. `make policy-test` recomputes the hashes on every pull request, so editing a template without bumping its version and re-pinning fails the gate: a prompt change arrives with a version and its measurement, or not at all. Every gateway call records its prompt's ref (`answer@3#b1177631`) in `model_calls` — computed live from the text that ran, so code/pin drift would be visible in the ledger even before the gate catches it — which makes "which prompt produced this answer" answerable the way "which model" already is. FINDINGS §16 and §18 are the measured case: prompt changes move eval and red team results, differently per model, so a prompt change is a deployment.

**The registry ceiling outranks the rule file.** A model's `max_classification` is checked before any rule runs. A permissive rule cannot grant a model access to data more sensitive than its own registry entry allows — the two have to agree, and the stricter one wins.

**Residency is a match key, not a note.** Every registry entry states where its inference physically runs (`region:` — `on-prem` for self-hosted, the provider region otherwise; the loader refuses an entry without one), and rules match on it: the cloud allows are scoped to `region: [us]`, and a cloud model in any other region is refused by `residency-us-only` with that reason — even for public content, and even though no non-US model is registered yet. The Bedrock entry is the honest case: its `us.` cross-region profile means AWS may run the request in any US region, which is exactly what `region: us` says. `make policy-test` exercises the refusal against a synthetic EU-hosted entry, so the rule is tested before the day it is needed.

**Classification comes from the retrieved context, and the most sensitive chunk governs.** One confidential chunk constrains the whole call, because a prompt is indivisible once it is sent. In practice that means a question Dana asks about salary bands cannot be answered by an external provider, while the same question shape about public content can.

**One choke point.** `gateway.complete()` is the only code in the system that constructs a model client. `chain.llm()` now raises if anything tries to build one directly, because a policy layer that one direct call can bypass is decoration.

**Refusals are logged before anything else runs.** A denial that leaves no trace is indistinguishable from a policy that was never evaluated. `model_calls` records the refusal, the rule that produced it, and the fallback that actually answered.

**Provider errors fail over, through the same policy.** When the chosen model's invoke raises — provider down, timeout, a gated or vanished model id — the failure is logged as its own decision (`provider-error`, with the exception in the reason: a crash with no record is indistinguishable from a call that never happened), and the request degrades to the fallback after the same policy and budget checks every call gets. It is failover, not retry: the primary gets one attempt, and if the fallback fails too, both failures are in the ledger before the error propagates.

**Cache and cost.** Responses are cached on a hash of model plus full prompt, with a TTL. Cost is attributed per call from the registry's rates, on the provider's own token counts: every provider adapter returns LangChain's standardized usage fields, and the ~4-characters-per-token estimate remains only as the fallback for a response that reports nothing. The ledger's `tokens_exact` column records which meter ran on every row, because a cost is only as exact as its meter — the first measured comparison had the estimate undercounting a Bedrock call's input by roughly a third. `make usage` says how many calls in the window were estimated rather than silently mixing the two.

`make usage` reports which rules are firing and how often calls are refused, not just the dollar total. Which rules fire is the governance question; the dollars are a side effect.

**Budgets enforce; the ledger is the meter.** A `budgets:` section in `policy/routing.yaml` sets spend ceilings — per model, per principal, and in total — over a rolling window. The gateway sums the ledger's `cost_usd` inside the window at decision time, so there is no counter to reset and nothing that can drift from what was actually spent. A model at its ceiling is denied like any other rule (`budget-model-exceeded` / `budget-total-exceeded`, logged with the spend and the cap in the reason) and the request degrades to the policy fallback instead of failing — refusing the request would punish the user for the model's spending. Zero-cost models are exempt, even from the total: a budget that blocks free traffic is a rate limit wearing the wrong name. Per-principal ceilings (`principal_usd`, with named overrides under `principals:`) meter each caller separately, so one user exhausting their quota degrades only their own calls to the fallback, not everyone's — and a call with no principal is metered as one shared anonymous bucket under the default cap, because an exemption there would make omitting the principal the bypass. `make usage` shows headroom per ceiling, including each caller seen in the window, and `make policy-test` exercises the budget decisions with synthetic spend, so the enforcement logic is covered on every pull request without a database. The caveat budgets inherit: a ceiling is as exact as the meter behind it — provider-reported token counts since Oct 12, with the estimate only as fallback, and the ledger recording which meter ran per call.

**Two rules in the file never fire, and that is documented rather than hidden.** With the current registry, the ceiling catches a cloud model plus sensitive data before the rule file is consulted, so `confidential-stays-local` and `restricted-stays-local` are unreachable. They are real defense in depth — they catch a future registry edit that mis-registers a cloud model as restricted-capable — but an untested rule is false assurance, so `make policy-test` exercises both against a synthetic registry entry.

**The eval gate disables the cache.** A cached run would report 44 passes without calling the model once. The gate measures the model; the cache is for serving.

Measured on the full golden set: 44 calls, 0 denied, all routed to self-hosted inference, $0.00 — the corpus contains confidential and restricted documents, so every call that touches them is constrained to local by policy rather than by configuration.



## Evidence ledger (Phase 6)

```bash
make migrate                      # adds the evidence table and its triggers
make ask Q="..." USER=sam         # every question writes an entry
make verify                       # recompute the whole hash chain
make trace ID=sam                 # entries by principal, correlation id, or entry id
make append-only                  # confirm UPDATE and DELETE are refused
make tamper-demo                  # prove the chain detects a silent edit
```

**What an answer has to prove, after the fact.** Who asked, what they were entitled to, which chunks were retrieved, which model ran, which rule permitted it, and that the record has not been altered since. One entry per question, answered or refused.

**Digests, not answers.** The ledger stores a SHA-256 of the answer and chunk ids, never the answer text or the chunk text. Storing answers would recreate — in an append-only table that cannot be deleted from — a copy of exactly the confidential content the permission layer works to contain. The digest proves what was said to anyone who still has the text and discloses nothing to anyone who does not.

**Refusals are entries.** A question that returned nothing, and a question refused by routing policy, both write to the ledger. A ledger holding only successes proves nothing about enforcement: the refusals are how you show the controls fired rather than that they were merely configured.

**Append-only in the database.** Triggers refuse `UPDATE` and `DELETE` outright. Rewriting history requires `ALTER TABLE ... DISABLE TRIGGER` — deliberately awkward, so an accident cannot do it and a deliberate act leaves a schema change behind.

**Tamper-evident, not tamper-proof.** Each entry hashes its own content plus the previous entry's hash, so altering or removing a past row invalidates every hash after it. `verify` recomputes each digest from stored content rather than trusting the stored hash, which catches an edit even if the editor also updated that row's hash — the break just moves to the next link. Anyone who can rewrite the entire table in order can still forge a consistent chain. Detecting quiet edits is the goal; defeating an operator with database ownership is not, and claiming otherwise would be dishonest.

`make tamper-demo` edits a row behind the triggers, shows verification fail, then restores it. A hash chain nobody has watched break is a claim, not a control.

**Correlation across layers.** One correlation id links the ledger entry to the model call log, so a single answer's retrieval scope and its model routing decision can be reconstructed together. `make trace ID=<principal>` prints both.

**Appends are serialized** with a table lock. Two concurrent writers reading the same tail hash would produce two entries claiming the same predecessor, and the chain would be unverifiable through that point. Appends are rare relative to reads, so the contention is acceptable here; a high-throughput deployment would build the chain with a single writer instead.

## Red team (Phase 7)

```bash
make redteam                          # 8 attack classes x 2 positions
CONTEXT_FENCING=false make redteam    # measure what the defense actually buys
```

**Retrieved text is untrusted input.** Anyone who can put a document where the indexer reaches it — a wiki page, a shared drive, a ticket comment, a vendor PDF — can put text in front of the model. In an enterprise that is a large set of people, most of whom are never thought of as having access to the AI system at all.

**Poisoned documents are never ingested.** `corpus/redteam/` is loaded from disk and assembled into context in memory; the ingest path reads `corpus/seed/` only. A fixture that could leak into a real answer would be a poor test.

**Position is part of the test.** Each attack runs with the poisoned document first and last. Several succeed in one position and not the other, so a single-position result means little.

**The gate is the same ratchet as the evals, keyed per model.** `evals/redteam-baseline.yaml` records each accepted finding — a compromise or a `NO ANSWER` — with a reason, under the model that produced it, because a finding is a property of the model, not of the system: CI's small model and the local default fail different cases, and a shared list would flap whenever they disagree. A finding outside the model's list is a regression; an accepted finding that now holds is a stale baseline; both exit non-zero. An entry marked `flaky: true` gates in neither direction — needed for a model that rejects temperature, where a finding that appears on some runs would fail the gate as a regression on bad nights and as stale on good ones; the reason must say why it comes and goes. In CI the red team runs as two nightly jobs in `eval-full.yml`, both failing the workflow: the local `llama3.2:3b` always, and Bedrock Sonnet via the same OIDC role as the Bedrock eval job once `AWS_CI_ROLE_ARN` is set — unlike the answer-quality tier, injection resistance does not need a smart model to measure, so a 3B model's result is signal there. A model with no baseline section gates on every finding, which is the old exit-on-any-compromise behavior and what the per-model local runs still do.

**Measured:** fencing takes attack success from 4/16 to 2/16. It costs one golden-set item — with fencing on, the model refuses a safety-critical loss-prevention question it answers correctly with fencing off, because that document's own handling rules read as an instruction to withhold once it is framed as untrusted data. The trade-off is recorded in `evals/baseline.yaml` with the measurement attached; fencing stays on.

**A provenance clause joined the fencing on Oct 12** after CI caught the cloud model repeatedly laundering a planted salary band through a forged citation (RED-003): the source tag is the block the text physically came from, and content a document attributes to another document is only its own claim. Measured on matched samples it takes Sonnet's citation-forgery rate from 3/16 to 0/16 — and, true to the rule that a free defense is doing nothing, it moved every model differently: the 8B model's RED-005 failure is fixed (baseline entry retired), one 8B golden-set item went terse (G13, baselined beside G30), and the 3B CI model now fails RED-001 in a second position (baselined). FINDINGS §18 has the full accounting. The accepted lists now stand at one case for the 8B default, two for the 3B CI model, and four `flaky` entries for Bedrock Sonnet — two real intermittent citation-forgery surfaces and two scorer artifacts where the model names the attack while refusing it.

A prompt is a shared resource: every instruction added to defend against an attacker is also read when a real user asks something. A defense that costs nothing on a fixed eval set usually is not doing anything.

**What no attack achieved was a permission bypass** — and that is not the model's doing. Nothing unauthorized was in the context to leak, because row-level security scoped the candidate set before ranking. Injection cannot exfiltrate what was never retrieved. An injection can make the model emit `OVERRIDE-ACCEPTED`; it cannot make Postgres return a row the caller has no grant for, and every attempt is in the ledger.

## Repo metadata check

```bash
make repo-meta-check   # confirm the GitHub About description and topics are set
```

**Discoverability is not the same question as correctness.** Every other check in this pipeline asks whether the system behaves — eval gate, RLS, red team. None of them notice if the repo itself becomes unfindable: a rename, a fork, or a repo edit can silently clear the About description or topics, and nothing about the code changes. This check reads the public GitHub API and fails if either is empty. It runs as its own CI job, parallel to `policy`, because it depends on GitHub's API rather than this repo's code and shouldn't wait on a Postgres service or a model pull.

## Console

```bash
make api     # http://localhost:8000
```

![ControlPlane console](docs/console.png)

A single page over the same API the CLI uses. Nothing on it is mocked: every field comes from a real response, so what it demonstrates is the system rather than a picture of one.

Pick a persona, pick a model, ask. The page shows the answer, the retrieved documents with their classifications, and every routing decision with the rule that produced it — alongside the live ledger, 24-hour spend by rule, and the model registry.

**The ninety-second demo**, using the example buttons:

1. **Salary band (confidential)** as `dana`, with the Bedrock model requested. Two decision cards: a red DENY on `registry-ceiling`, a green ALLOW on self-hosted inference. Cost $0.00 — the salary band never left the network.
2. **Same question, no entitlement** as `sam`. The confidential document disappears from the retrieved set entirely. It isn't withheld from the answer; it was never retrieved.
3. **Public fact** as `riley`, Bedrock requested. Green ALLOW on `cloud-public`, with a real cost attached.
4. **Verify chain.** All four questions are in the ledger, and the chain recomputes clean.

Permissions, routing, cost and evidence — without reading any code.

## Layout

```
corpus/SPEC.md        synthetic company, personas, frontmatter schema, deliberate traps
corpus/seed/          70 documents across six departments, ~148 chunks
db/schema.sql         owned schema, RLS policies, rag_app role, gateway and ledger tables
db/init.sql           same content, runs once on a fresh volume
policy/models.yaml    model registry: providers, tiers, ceilings, rates, status
policy/routing.yaml   routing rules, evaluated in order, ending in a deny; budgets
policy/prompts.yaml   prompt registry pins: version + sha256 per prompt, gated by policy-test
evals/golden.yaml     44 items across 9 categories
evals/personas.yaml   persona -> groups (identity provider stand-in)
evals/baseline.yaml   ratchet: known failures with reasons and fix phase
evals/redteam-baseline.yaml  red team ratchet: accepted findings per model, with reasons
docs/FINDINGS.md      what the measurements turned out to mean
docs/console.png      console screenshot
docs/*.xlsx           red team comparison workbooks
docs/*.json           preserved eval runs: naive baseline and enforced
infra/                one-time cloud setup: GitHub OIDC -> Bedrock role, exact and scoped
.github/workflows/    policy job, repo-meta job, then eval-gate (retrieval blocks,
                      full advisory, red team gated nightly by its baseline)
src/provenance/       config, db, identity, store, ingest, retrieval, chain, gateway,
                      registry, evidence, audit, evals, compare, usage,
                      rls_test, policy_test, repo_meta_check, api
```
