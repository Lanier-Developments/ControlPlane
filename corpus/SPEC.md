# Synthetic corpus specification

The corpus is a fictional company built to exercise the problems enterprise RAG actually fails on: permissions, stale versions, tables, gaps, and hostile content. Everything is invented. No real people, companies, or salary data.

## Company

**Kestrel Ridge Outfitters** is an outdoor gear retailer founded in 2009, headquartered in Bend, Oregon, with 42 stores, an online storefront, and about 1,800 employees.

## Groups

| Group | Who |
|---|---|
| `public` | Anyone, including contractors and candidates |
| `all-employees` | Every W2 employee |
| `engineering` | Software, platform, and data engineers |
| `finance` | Controller's office and FP&A |
| `hr-comp` | Total rewards and People leadership |
| `legal` | Legal and compliance |
| `store-ops` | Store managers and regional ops |

## Personas

Used by the golden set and, from Phase 4, by the permission layer.

| Persona | Groups | Tests |
|---|---|---|
| `sam` | public, all-employees, engineering | Must not see HR compensation or legal matters |
| `dana` | public, all-employees, hr-comp | Allowed to see salary bands |
| `priya` | public, all-employees, finance | Finance documents, not engineering runbooks |
| `riley` | public | Contractor: public documents only |

## Frontmatter schema

Required: `doc_id`, `title`, `department`, `classification`, `acl`, `version`.
Recommended: `effective_date`, `status` (active | superseded | draft), `supersedes`, `superseded_by`, `owner`.

Classification levels: `public` < `internal` < `confidential` < `restricted`. The `acl` list is authoritative for access. Classification feeds model-routing policy in Phase 5 (for example, restricted content never goes to an external provider).

## Target inventory (full corpus)

About 60 documents: HR 12, Finance 10, Engineering 14, Legal 6, Store Ops 10, Public 8.

## Deliberate traps

Each trap maps to the phase that fixes it.

1. **Version conflicts (Phase 3/4).** At least 5 documents exist in two versions with contradicting facts, and the old version is still indexed. Example: HR-001 v1 grants 15 PTO days, v2 grants 20.
2. **Tempting confidential neighbors (Phase 4).** Confidential documents that are semantically close to common questions, like salary bands next to the career ladder or a legal hold notice next to the records retention policy.
3. **Table-heavy documents (Phase 3).** Expense limits, band tables, and store hours where the answer lives in a single table cell.
4. **Near-duplicates (Phase 3).** Two or three documents that are 90% identical, like regional variants of the same store procedure.
5. **Deliberate gaps (Phase 2).** Plausible questions the corpus cannot answer, like the 401(k) match or parental leave. The correct behavior is "I don't know."
6. **PII-bearing documents (Phase 3).** Fictional names, phone numbers, and employee IDs in incident reports, to exercise redaction at ingest.
7. **Poisoned documents (Phase 7).** Documents with embedded instructions aimed at the model. These live in `corpus/redteam/` and are never ingested into the main collection.

## Generation prompt

Use this with Claude to expand the seed. Generate one department per session and review every document by hand before committing.

```
You are generating documents for a synthetic enterprise RAG test corpus for a fictional
company, Kestrel Ridge Outfitters (outdoor gear retailer, founded 2009, HQ Bend, Oregon,
42 stores, ~1,800 employees). Everything must be fictional.

Generate {N} markdown documents for the {DEPARTMENT} department. Each document must start
with YAML frontmatter using this schema: doc_id, title, department, classification
(public|internal|confidential|restricted), acl (list of groups from: public, all-employees,
engineering, finance, hr-comp, legal, store-ops), version, effective_date, status, owner.

Requirements:
- 150-600 words each, written like real internal documentation.
- Specific, checkable facts (numbers, thresholds, names of fictional systems).
- Include at least one table-heavy document.
- Include one document in two versions with at least two contradicting facts;
  mark the older one status: superseded.
- Include one confidential document that is topically close to a common employee question.
- Do not reuse facts already defined in the existing corpus: {PASTE EXISTING FACT LIST}

After the documents, output a fact list: one line per checkable fact, with doc_id and version.
```

The fact list feeds new golden set items and prevents cross-department contradictions you didn't intend.
