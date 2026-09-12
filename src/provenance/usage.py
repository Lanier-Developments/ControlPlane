"""Spend and policy-decision report from the model call log.

Cost attribution is a governance function, not a finance one: the useful questions are
which rules are firing, how often calls are refused, and whether the cache is doing
anything — not just the dollar total.
"""
import argparse
import sys

from .db import owner_conn


def main() -> int:
    ap = argparse.ArgumentParser(description="Model call usage and policy decisions")
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()

    with owner_conn() as conn:
        totals = conn.execute(
            """
            SELECT count(*), count(*) FILTER (WHERE decision = 'deny'),
                   count(*) FILTER (WHERE cache_hit), coalesce(sum(cost_usd), 0),
                   coalesce(sum(prompt_tokens), 0), coalesce(sum(completion_tokens), 0),
                   coalesce(round(avg(latency_ms) FILTER (WHERE NOT cache_hit)), 0)
            FROM model_calls WHERE occurred_at > now() - make_interval(hours => %s)
            """,
            (args.hours,),
        ).fetchone()

        by_rule = conn.execute(
            """
            SELECT rule_id, decision, count(*), coalesce(sum(cost_usd), 0)
            FROM model_calls WHERE occurred_at > now() - make_interval(hours => %s)
            GROUP BY rule_id, decision ORDER BY count(*) DESC
            """,
            (args.hours,),
        ).fetchall()

        by_model = conn.execute(
            """
            SELECT resolved_model, count(*), coalesce(sum(cost_usd), 0)
            FROM model_calls
            WHERE occurred_at > now() - make_interval(hours => %s) AND decision = 'allow'
            GROUP BY resolved_model ORDER BY count(*) DESC
            """,
            (args.hours,),
        ).fetchall()

    calls, denies, hits, cost, ptok, ctok, latency = totals
    if not calls:
        print(f"No model calls in the last {args.hours}h.")
        return 0

    print(f"## Model calls, last {args.hours}h\n")
    print(f"- Calls: {calls}  ({denies} denied, {hits} served from cache)")
    print(f"- Tokens: {ptok:,} in / {ctok:,} out (estimated)")
    print(f"- Cost: ${cost:.4f}")
    print(f"- Mean latency, uncached: {latency} ms")

    print("\n| Rule | Decision | Calls | Cost |\n|---|---|---|---|")
    for rule_id, decision, n, rule_cost in by_rule:
        print(f"| {rule_id} | {decision} | {n} | ${rule_cost:.4f} |")

    print("\n| Model | Calls | Cost |\n|---|---|---|")
    for model_id, n, model_cost in by_model:
        print(f"| {model_id} | {n} | ${model_cost:.4f} |")

    return 0


if __name__ == "__main__":
    sys.exit(main())
