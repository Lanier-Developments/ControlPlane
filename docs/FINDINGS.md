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

## 9. An injection defense made the model refuse a safety question

Eight prompt-injection classes, each tried with the poisoned document first and last in
context, against a corpus where the model's instructions explicitly say to treat
retrieved text as data.

Baseline: **4 of 16 cases compromised.** Adding context fencing — wrapping each document
in explicit begin/end markers and restating the instruction boundary after the context —
took that to **2 of 16**.

It cost one golden-set item. Asked what an associate should do about someone concealing
merchandise, the model answered correctly and cited the loss-prevention guidelines with
fencing off, and refused outright — "You don't know," in about 190 milliseconds — with
fencing on. That document opens with its own handling rules: *Confidential. Do not post
in the break room or discuss on the sales floor.* Framed as untrusted data whose claims
should not be acted on, the model appears to read those handling rules as an instruction
to withhold. The defense inverted on a document that describes how it should be handled.

The trade-off is now a number rather than an argument: two attacks blocked for one
degraded answer, and the degraded answer is a safety-critical one. Recorded as a known
failure with the measurement attached, and fencing stays on.

The general point is that a prompt is a shared resource. Every instruction added to
defend against an attacker is also read by the model when a real user asks something.
A defense that costs nothing on a fixed eval set usually is not doing anything.

## 10. What the injections could and could not do

Two attacks still succeed. The crudest one in the set — "IGNORE ALL PREVIOUS
INSTRUCTIONS" — still works when placed first in context. An 8B model does not reliably
hold an instruction hierarchy, and no amount of prompt wording fixes that.

What no attack achieved was a permission bypass, and that is not the model's doing.
Nothing the caller was not entitled to was in the context to leak: row-level security
scoped the candidate set before ranking. **Prompt injection cannot exfiltrate what was
never retrieved.**

That divides the defenses cleanly:

- Controls that depend on the model behaving — citation discipline, refusal behavior,
  ignoring embedded instructions — degrade under attack and under prompt changes.
- Controls that do not — row-level security, the routing policy, the append-only ledger
  — hold regardless. An injection can make the model emit `OVERRIDE-ACCEPTED`. It cannot
  make Postgres return a row the caller has no grant for, and every attempt is recorded.

One attack class also turned out to be a flaw in my own test design. The
"policy override" case looked like a leak but was the model repeating figures the
attacker had planted in the poisoned document itself, cited correctly to that document.
Relabelled content-injection: the achievable harm through this channel is planting false
facts that get repeated with a citation, not reading files the caller cannot see.

## 11. Policy routing across two providers, and the blind spot it creates

The registry gained a second provider — Claude Sonnet 4.6 on Bedrock, capped at
`internal` — and the golden set ran again with that model requested for every item.

| | |
|---|---|
| Calls | 46 |
| Answered by Claude | 32 |
| Denied and routed to self-hosted inference | 14 |
| Cost | $0.1857 |

The 14 denials are all `registry-ceiling`: the retrieved context for those items included
confidential or restricted documents, so the cloud model was refused and the local model
answered. Roughly **30% of this corpus is too sensitive to leave the network** under its
own classification policy — a governance fact about a document estate, which is the kind
of number most organizations do not have about their own content.

**The blind spot.** Both recorded known failures — the misattribution and the
fencing-induced over-caution — are on confidential items. Both were denied to Claude and
fell back to the same local model that produced them, so the run reproduced them exactly
and learned nothing about whether a stronger model would clear them.

That is not a bug in the harness. It is what the policy means: **the content you most
want a capable model on is the content policy will not let you send there.** Any
evaluation of a cloud model against a governed corpus is an evaluation of the
public-and-internal subset only. Improving answer quality on sensitive material means
improving the model you can self-host, or moving the data-handling agreement — not
switching providers.

**A related caution about the numbers.** This was not a clean model comparison. Claude
answered 32 items and the local model answered 14, chosen by classification rather than
at random. It measures the system under a routing policy, not one model against another.

## 12. Registry churn is the normal case, not the exception

Bringing up one provider over a single morning produced: a model id that changed twice
(bare foundation-model id, then a cross-region inference profile), a provider that
rejects the `temperature` parameter other providers require, published rates that
differed from the placeholder, a model gated separately from the rest of its family, and
an access path that turned out to need a vendor use-case form.

Every one of those was an edit to `policy/models.yaml`. None touched application code.

This is the concrete case against the pattern it was built to replace — model selection
running off an enum in application code — where each of those six changes would have
been a pull request, a release, and a rollback risk. The argument is not that config is
tidier. It is that provider details change on the provider's schedule, not yours, and
anything that makes a vendor's Tuesday into your deploy is the wrong shape.

One gap the exercise exposed: the cross-region profile means AWS may route a request to
any US region. The registry models *which model may see which classification* but has no
field for *where inference may physically happen*. Data residency is a separate policy
axis and is not yet represented.

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
- **Routing has one dimension.** Classification decides the model. A real control plane
  also routes on cost, latency, capability and provider health, and fails over when a
  provider is down — here the fallback fires only on a policy denial, never on an error.
- **No data-residency axis.** The registry says which model may see which classification,
  not where inference may physically run.
- **No quotas or budgets.** Cost is attributed after the fact; nothing enforces a ceiling.
- **No prompt registry.** Models are registered and versioned; prompts are not, even
  though a prompt change has been shown here to move eval results.
- **The refusal check is phrase-matched and model-specific.** The marker list had to be
  extended for the local model, then again for Claude — the same correct refusal, phrased
  differently. It will need extending for the next model too. That maintenance cost is
  the argument for the attribution judge, observed rather than asserted.
