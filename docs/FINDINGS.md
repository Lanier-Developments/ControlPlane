# Findings

What building this actually surfaced. Every number came from a run in the repo; the
commands that produce them are in the README.

The system under test: a governed enterprise RAG pipeline over a synthetic corpus of
70 documents (148 chunks) across six departments, with real access control lists, seven
version-conflict pairs, and documents classified public through restricted. Scored
against 44 golden-set items across five personas.

---

## 1. Naive RAG leaked on every single question

The Phase 1 baseline — embed, retrieve top-k, answer with citations — exposed content
the asking persona had no entitlement to on **10 of 10** golden items. Not an edge case.
The default behavior.

The sharpest instance: a contractor whose only entitlement is `public` asked how to roll
back the payments API after a bad deploy. Retrieval returned the internal on-call
runbook and the model handed back the `deployctl` command with no hesitation.

Nothing about that pipeline was unusual. It is what a competent engineer builds in an
afternoon from a framework tutorial.

## 2. Improving retrieval made a leak worse

This is the finding that changed how the project is built.

An engineer with no compensation access asked for a salary band. In Phase 1 the
confidential document reached the model's context, but the model declined to repeat the
figures — so only the retrieval-level check fired. Judged on the answer alone, that item
looked fine.

After Phase 3 improved retrieval quality — same corpus, same permissions, same model —
the model answered with the numbers.

Nothing about the exposure changed. Retrieval simply got better at surfacing what it had
already been handed. **Every retrieval improvement is an amplifier on whatever exposure
already exists.**

Two consequences:

- Leakage must be scored **at retrieval, not in the answer**. Once an unauthorized chunk
  is in the context it has been exposed — to the model, to the trace backend, and to
  every log that captures a prompt. A model's good manners are not an access control.
- Permissions must be enforced **before** retrieval ranks anything, not as a filter on
  results afterward.

## 3. A three-position ranking miss produced a confident, wrongly-sourced answer

Expanding the corpus from 6 documents to 70 pushed the travel policy's receipt rule from
inside a five-chunk window to just outside it.

Asked for the itemized receipt threshold, the model answered **"$75"** — a figure
appearing nowhere in its context — and attached a real source tag to a real retrieved
document that says nothing about receipts.

The answer looked fully cited. A reader trusting the tag had no signal anything was
wrong. The true answer was $50.

Restoring the source (`TOP_K` 5 → 8) fixed it, but the more useful output was two checks
the gate now runs on every item:

- `citation_unsupported` — a source tag naming a document that was never retrieved.
- `ungrounded_value` — a currency or percentage figure appearing nowhere in the context.

Both are deterministic and cost nothing. Note that the first check alone would **not**
have caught this failure: the tag was real and the document was retrieved. Checking
that a citation exists is not the same as checking that it supports the claim.

## 4. Over-blocking scores as well as correct enforcement

A permissions layer that refuses everything achieves a perfect leak rate.

So denials and entitled reads are tested as pairs on the same fact. One item asks an HR
persona for the engineering budget and must be refused; another asks a finance persona
for the same figure and must answer it. Of 44 items, 9 are denials and 3 are
confidential-but-entitled reads.

The same logic applies to refusals: a refusal that says "you lack permission to see that"
confirms the document exists. For a confidential legal hold, the existence *is* the
disclosure. Refusals state that no available information covers the question.

## 5. Three checks a green gate has to survive

**Enforcement has to be provable below the application.** Retrieval runs as a
`NOBYPASSRLS`, SELECT-only Postgres role under row-level security. `make rls-test`
bypasses the Python entirely and queries as that role: per-persona visible counts,
specific forbidden documents, and that entitlements do not survive the transaction that
set them. Expected counts are computed from the corpus files, not the database, so the
test cannot agree with a bug in ingest by sharing its source of truth.

**The gate refuses to run unless enforcement is on.** A startup assertion checks that
the retrieval role cannot bypass RLS and sees nothing with no groups set. Without it,
pointing one environment variable at the owner role would silently disable every policy
while the suite stayed green.

**The gate measures the model, not the cache.** Response caching is disabled during eval
runs. A cached run reports 44 passes without calling the model once.

## 6. A prompt fix broke something the same day

Tightening the citation rule fixed one item and immediately broke another: the worked
example used a single-value answer, and the model generalized that terse was correct,
then returned only the lower bound of a salary range.

Caught in one run by a gate written before the change. That is the entire argument for
building the eval harness in Phase 2, before any retrieval or prompt tuning — not as
process hygiene but because prompt changes have non-local effects that are invisible
without a fixed scoring set.

## 7. Governance config that can't drift

Model selection runs off a registry file, not an enum in code. Adding, repricing,
deprecating or retiring a model is an edit to `policy/models.yaml`.

This is a direct response to a prior control-plane build where the model list was an
enum in application code: every new provider meant a pull request, a release and a
rollback risk, and what was actually approved lived in code, in a wiki, and in someone's
head — three sources that disagreed. The registry carries a *retired* entry on purpose,
because a retired model has to be refused rather than quietly absent.

Routing rules are data too, evaluated in order with a deny as the final rule, so a case
the policy file failed to anticipate refuses rather than falling through to allow.

Two rules in the file are currently unreachable — the registry's per-model classification
ceiling catches those combinations first. They are real defense in depth against a future
mis-registration, but an untested rule is false assurance, so the policy suite exercises
both against a synthetic registry entry.

## 8. The cost shape is the prompt, not the answer

A full 44-item eval run consumed roughly **67,300 prompt tokens against 529 completion
tokens** — a 127:1 ratio, since `TOP_K=8` full documents go into every prompt.

On self-hosted inference that costs only latency. At the external provider rates in the
registry it would be about $0.21 per suite run, almost entirely prompt. If cloud routing
is ever enabled, that ratio is the cost driver, and it is the concrete argument for a
reranker in front of the model: retrieve 8, send 3.

---

## What is still open

- **Misattribution is not caught by any deterministic check.** On one item the model
  produced correct content from one document and tagged it with another. The tag is real
  and the document was retrieved, and the value is not a figure — so neither new check
  fires. Recorded as a known failure with a reason and a fix phase rather than worked
  around. It needs a claim-to-source judge.
- **Version demotion is not exclusion.** A retired document still reaches context for the
  `public` persona, whose nine visible documents nearly fill a `TOP_K=8` window. Answers
  cite the active version regardless, but the mechanism is a ranking penalty, not a filter.
- **Identity is a stand-in.** Group membership comes from a YAML file rather than an
  identity provider.
- **Deletes upstream are not detected.** A document removed at the source stays indexed
  until the next full ingest.
- **Token counts are estimates** (~4 characters per token), labelled as such. Exact
  accounting needs provider usage fields.
