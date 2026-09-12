"""Compare retrieval configurations against the golden set.

Phase 3's discipline: no retrieval change ships without a measured comparison.
Answer-tier checks are excluded here; this compares retrieval only, so it is
deterministic and fast.

    python -m provenance.compare vector hybrid
"""
import argparse
import json

import yaml

from . import evals
from .config import settings


def run(mode: str, items: list[dict], personas: dict) -> dict:
    settings.retrieval_mode = mode
    results = [evals.score_item(i, personas[i["persona"]], "retrieval") for i in items]
    return {
        "mode": mode,
        "leaks": sum("context_leak" in r["failed"] for r in results),
        "empty_results": sum(not r["retrieved"] for r in results),
        "misses": sum("retrieval_miss" in r["failed"] for r in results),
        "superseded_in_context": sum(
            bool(r["details"].get("superseded_in_context")) for r in results
        ),
        "results": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("modes", nargs="*", default=["vector", "hybrid"])
    ap.add_argument("--golden", default="evals/golden.yaml")
    ap.add_argument("--personas", default="evals/personas.yaml")
    ap.add_argument("--out", default="retrieval-comparison.json")
    args = ap.parse_args()

    items = yaml.safe_load(open(args.golden))
    personas = yaml.safe_load(open(args.personas))
    runs = [run(m, items, personas) for m in (args.modes or ["vector", "hybrid"])]

    n = len(items)
    print(f"| Mode | Leaks | Retrieval misses | Superseded in context |")
    print(f"|---|---|---|---|")
    for r in runs:
        print(f"| {r['mode']} | {r['leaks']}/{n} | {r['misses']}/{n} | {r['superseded_in_context']}/{n} |")

    base, *rest = runs
    for r in rest:
        moved = [
            (b["id"], sorted(set(b["failed"]) ^ set(c["failed"])))
            for b, c in zip(base["results"], r["results"])
            if set(b["failed"]) != set(c["failed"])
        ]
        if moved:
            print(f"\nChanged from {base['mode']} to {r['mode']}:")
            for item_id, checks in moved:
                print(f"  {item_id}: {', '.join(checks)}")

    json.dump(runs, open(args.out, "w"), indent=2)
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
