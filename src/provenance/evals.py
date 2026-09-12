"""Golden-set scorer with a ratchet gate.

Tiers
  retrieval  Deterministic, no chat model. Checks what reached the model's context.
  full       Also generates answers and checks facts, forbidden content, citations, refusals.

Checks
  context_leak             a retrieved chunk the persona is not entitled to (retrieval)
  retrieval_miss           an expected source was not retrieved (retrieval)
  answer_missing_fact      a must_include string is absent (full)
  answer_forbidden_content a must_not_include string is present (full)
  citation_missing         an expected source is not cited in the answer (full)
  citation_unsupported     the answer cites a source that was never retrieved (full)
  ungrounded_value         a currency or percentage figure appears nowhere in the context (full)
  no_refusal               nothing should be cited, but the answer didn't decline (full)

  These two catch different lies. citation_unsupported catches a tag naming a document
  that was never in context. ungrounded_value catches the more common and more dangerous
  case: a real tag for a real retrieved document, attached to a figure that document does
  not contain. The tag looks verified; the number was invented. Checking only the tag
  misses it entirely.

  ungrounded_value is deliberately narrow — currency and percentages only. Those are
  deterministic to extract and are what people act on. Prose claims need an LLM judge.

Gate (ratchet)
  evals/baseline.yaml lists known failures per item, each with a reason and a fix phase.
  - A failing check NOT in the baseline is a regression   -> exit 1
  - A baseline check that now PASSES is a stale baseline   -> exit 1 (tighten it)
  The gate can only move toward fewer known failures.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import yaml

RETRIEVAL_CHECKS = {"context_leak", "retrieval_miss"}
REFUSAL_MARKERS = (
    "don't know", "do not know", "not available", "no information", "don't have",
    "do not have", "cannot find", "can't find", "not able to", "unable to",
    "isn't in the", "is not in the", "not in the provided", "not covered",
)
# Matches source tags the model wrote, e.g. [FIN-003 v3] or [HR-001 v2 SUPERSEDED].
TAG_RE = re.compile(r"\[\s*([A-Z]{2,5}-\d{3})\s*v(\d+)[^\]]*\]")
# Currency and percentage figures: the values people act on, and cheap to verify.
FIGURE_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?\s?(?:%|percent)")


def normalize_figure(text: str) -> str:
    return re.sub(r"[\s,]", "", text).replace("percent", "%").lower()


def load_yaml(path: str) -> dict:
    p = Path(path)
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def corpus_acls() -> dict[str, set[str]]:
    """Entitlements read from the corpus files, not from retrieved metadata.

    Deliberately independent: asking a retrieved chunk to report its own ACL would
    let a leak vouch for itself. The scorer's source of truth is the authoring
    format, which is what the ingest path is supposed to honor.
    """
    from pathlib import Path as _Path

    from .config import settings
    from .ingest import load_corpus

    return {
        f"{d.metadata['doc_id']} v{d.metadata['version']}": set(d.metadata["acl"])
        for d in load_corpus(_Path(settings.corpus_dir))
    }


def source_key(meta: dict) -> str:
    return f"{meta['doc_id']} v{meta['version']}"


def cited(answer: str, source: str) -> bool:
    doc_id, version = source.split(" v")
    return re.search(re.escape(doc_id) + r"\W{0,3}v?" + re.escape(version) + r"\b", answer) is not None


def tags_in(answer: str) -> list[str]:
    """Every source tag the answer claims, whether or not it was ever retrieved."""
    return [f"{doc_id} v{version}" for doc_id, version in TAG_RE.findall(answer)]


def score_item(item: dict, groups: list[str], tier: str) -> dict:
    from .chain import generate, retrieve

    docs = retrieve(item["question"], user=item["persona"])
    retrieved: dict[str, dict] = {}
    for d in docs:
        retrieved.setdefault(source_key(d.metadata), d.metadata)

    failed, details = [], {}

    acls = corpus_acls()
    # A retrieved source absent from the corpus is itself a failure, so an unknown
    # key must not be treated as permitted.
    leaks = [k for k in retrieved if not acls.get(k, set()) & set(groups)]
    if leaks:
        failed.append("context_leak")
        details["leaked_sources"] = leaks

    expected = item.get("expected_sources", [])
    missing = [s for s in expected if s not in retrieved]
    if missing:
        failed.append("retrieval_miss")
        details["missing_sources"] = missing

    superseded = [k for k, m in retrieved.items() if m.get("status") == "superseded"]
    if superseded:
        details["superseded_in_context"] = superseded  # informational

    answer = None
    if tier == "full":
        answer = generate(item["question"], docs)
        low = answer.lower()
        absent = [s for s in item.get("must_include", []) if s.lower() not in low]
        if absent:
            failed.append("answer_missing_fact")
            details["missing_facts"] = absent
        forbidden = [s for s in item.get("must_not_include", []) if s.lower() in low]
        if forbidden:
            failed.append("answer_forbidden_content")
            details["forbidden_found"] = forbidden
        uncited = [s for s in expected if not cited(answer, s)]
        if uncited:
            failed.append("citation_missing")
            details["uncited_sources"] = uncited
        fabricated = sorted({t for t in tags_in(answer) if t not in retrieved})
        if fabricated:
            failed.append("citation_unsupported")
            details["fabricated_citations"] = fabricated
        context_figures = {
            normalize_figure(f) for d in docs for f in FIGURE_RE.findall(d.page_content)
        }
        ungrounded = sorted(
            {f for f in FIGURE_RE.findall(answer) if normalize_figure(f) not in context_figures}
        )
        if ungrounded:
            failed.append("ungrounded_value")
            details["ungrounded_figures"] = ungrounded
        if not expected and not any(m in low for m in REFUSAL_MARKERS):
            failed.append("no_refusal")

    return {
        "id": item["id"],
        "persona": item["persona"],
        "category": item["category"],
        "retrieved": list(retrieved),
        "failed": failed,
        "details": details,
        "answer": answer,
    }


def apply_gate(results: list[dict], baseline: dict, tier: str) -> tuple[list, list]:
    known = baseline.get("known_failures", {}) or {}
    in_scope = RETRIEVAL_CHECKS if tier == "retrieval" else None
    regressions, stale = [], []
    for r in results:
        allowed = set(known.get(r["id"], {}).get("checks", []))
        failed = set(r["failed"])
        if in_scope is not None:
            allowed &= in_scope
            failed &= in_scope
        new = sorted(failed - allowed)
        fixed = sorted(allowed - failed)
        if new:
            regressions.append((r["id"], new))
        if fixed:
            stale.append((r["id"], fixed))
    return regressions, stale


def write_baseline(path: str, results: list[dict], tier: str, old: dict) -> None:
    known = old.get("known_failures", {}) or {}
    out = {}
    for r in results:
        prev = known.get(r["id"], {})
        checks = set(r["failed"])
        if tier == "retrieval":  # keep answer-level entries this tier can't evaluate
            checks |= {c for c in prev.get("checks", []) if c not in RETRIEVAL_CHECKS}
        if checks:
            out[r["id"]] = {
                "checks": sorted(checks),
                "reason": prev.get("reason", "TODO: why this fails"),
                "fix_phase": prev.get("fix_phase"),
            }
    header = "# Known failures. Generated by `make baseline`; edit reason and fix_phase by hand.\n"
    Path(path).write_text(header + yaml.safe_dump({"known_failures": out}, sort_keys=True))


def summarize(results, regressions, stale, baseline, tier) -> str:
    known = baseline.get("known_failures", {}) or {}
    n = len(results)
    leak = sum("context_leak" in r["failed"] for r in results)
    fabricated = sum("citation_unsupported" in r["failed"] for r in results)
    expected_total = sum(len(r["details"].get("missing_sources", [])) for r in results)
    lines = [
        f"## Eval gate ({tier} tier)",
        "",
        f"- Items: {n}",
        f"- Context leak rate: {leak}/{n} items exposed content the persona is not entitled to",
        f"- Missing expected sources: {expected_total}",
    ]
    if tier == "full":
        lines.append(f"- Fabricated citations: {fabricated}/{n} items cited a source never retrieved")
        ungrounded = sum("ungrounded_value" in r["failed"] for r in results)
        lines.append(f"- Ungrounded figures: {ungrounded}/{n} items stated a figure not in their context")
    lines += [
        f"- Regressions: {len(regressions)}",
        f"- Stale baseline entries: {len(stale)}",
        "",
        "| Item | Persona | Category | Failed checks | Status |",
        "|---|---|---|---|---|",
    ]
    reg_ids = {i for i, _ in regressions}
    for r in results:
        if r["id"] in reg_ids:
            status = "REGRESSION"
        elif r["failed"]:
            status = f"known (phase {known.get(r['id'], {}).get('fix_phase') or '?'})"
        else:
            status = "pass"
        lines.append(f"| {r['id']} | {r['persona']} | {r['category']} | {', '.join(r['failed']) or '-'} | {status} |")
    if stale:
        lines += ["", "Baseline entries that now pass (run `make baseline` and commit):"]
        lines += [f"- {i}: {', '.join(c)}" for i, c in stale]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Score the golden set and apply the ratchet gate")
    ap.add_argument("--tier", choices=["retrieval", "full"], default="retrieval")
    ap.add_argument("--golden", default="evals/golden.yaml")
    ap.add_argument("--personas", default="evals/personas.yaml")
    ap.add_argument("--baseline", default="evals/baseline.yaml")
    ap.add_argument("--report", default="eval-report.json")
    ap.add_argument("--write-baseline", action="store_true")
    args = ap.parse_args()

    items = yaml.safe_load(Path(args.golden).read_text())
    personas = load_yaml(args.personas)
    from .db import assert_rls_enforced

    assert_rls_enforced()  # a green gate must mean enforcement was actually on
    baseline = load_yaml(args.baseline)

    results = []
    for item in items:
        if item["persona"] not in personas:
            raise ValueError(f"{item['id']}: unknown persona {item['persona']}")
        results.append(score_item(item, personas[item["persona"]], args.tier))

    Path(args.report).write_text(json.dumps(results, indent=2))

    if args.write_baseline:
        write_baseline(args.baseline, results, args.tier, baseline)
        print(f"Wrote {args.baseline}. Fill in reason and fix_phase, then commit.")
        return 0

    regressions, stale = apply_gate(results, baseline, args.tier)
    summary = summarize(results, regressions, stale, baseline, args.tier)
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(summary)
    for item_id, checks in regressions:
        print(f"::error title=Eval regression {item_id}::{', '.join(checks)}")
    for item_id, checks in stale:
        print(f"::error title=Stale baseline {item_id}::now passing: {', '.join(checks)}")
    for r in results:
        if r["failed"] and r["id"] not in {i for i, _ in regressions}:
            print(f"::warning title=Known failure {r['id']}::{', '.join(r['failed'])}")

    return 1 if regressions or stale else 0


if __name__ == "__main__":
    sys.exit(main())
