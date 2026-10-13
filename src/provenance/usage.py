"""Spend and policy-decision report from the model call log.

Cost attribution is a governance function, not a finance one: the useful questions are
which rules are firing, how often calls are refused, and whether the cache is doing
anything — not just the dollar total.
"""
import argparse
import sys

from .db import owner_conn
from .registry import budgets, evaluate_health, get_model, principal_cap, steering_health


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
                   coalesce(round(avg(latency_ms) FILTER (WHERE NOT cache_hit)), 0),
                   count(*) FILTER (WHERE tokens_exact IS NOT true
                                    AND decision = 'allow' AND NOT cache_hit)
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

    calls, denies, hits, cost, ptok, ctok, latency, estimated = totals
    if not calls:
        print(f"No model calls in the last {args.hours}h.")
        return 0

    # Which meter produced the counts is part of the number. Rows from before the
    # tokens_exact column count as estimated, which they were.
    meter = ("provider-reported" if not estimated
             else f"{estimated} of {calls - denies - hits} generated calls estimated")
    print(f"## Model calls, last {args.hours}h\n")
    print(f"- Calls: {calls}  ({denies} denied, {hits} served from cache)")
    print(f"- Tokens: {ptok:,} in / {ctok:,} out ({meter})")
    print(f"- Cost: ${cost:.4f}")
    print(f"- Mean latency, uncached: {latency} ms")

    print("\n| Rule | Decision | Calls | Cost |\n|---|---|---|---|")
    for rule_id, decision, n, rule_cost in by_rule:
        print(f"| {rule_id} | {decision} | {n} | ${rule_cost:.4f} |")

    print("\n| Model | Calls | Cost |\n|---|---|---|")
    for model_id, n, model_cost in by_model:
        print(f"| {model_id} | {n} | ${model_cost:.4f} |")

    # Steering: what took the requests nobody pinned. The rule id travels in the
    # requested_model column as (steered:<id>), so the ledger answers this directly.
    with owner_conn() as conn:
        steered = conn.execute(
            """
            SELECT substring(requested_model from 10 for length(requested_model) - 10),
                   resolved_model, decision, count(*), coalesce(sum(cost_usd), 0)
            FROM model_calls
            WHERE occurred_at > now() - make_interval(hours => %s)
              AND requested_model LIKE '(steered:%%'
            GROUP BY 1, 2, 3 ORDER BY 4 DESC
            """,
            (args.hours,),
        ).fetchall()
    if steered:
        print(f"\n## Steering, last {args.hours}h\n")
        print("| Rule | Model | Decision | Calls | Cost |\n|---|---|---|---|---|")
        for rule, model_id, decision, n, s_cost in steered:
            print(f"| {rule} | {model_id} | {decision} | {n} | ${s_cost:.4f} |")

    # Provider health, over the health window rather than --hours, through the same
    # evaluate_health the gateway calls: enforcement and report must read the same
    # number or the report is decoration. Attempts are real invokes — successes plus
    # provider-error rows; cache hits and denials moved no provider traffic.
    hcfg = steering_health()
    if hcfg and hcfg.get("max_error_rate") is not None:
        window = hcfg.get("window_minutes", 30)
        with owner_conn() as conn:
            attempts = conn.execute(
                """
                SELECT resolved_model,
                       count(*) FILTER (WHERE rule_id = 'provider-error'),
                       count(*),
                       max(occurred_at) FILTER (WHERE rule_id = 'provider-error')
                       + make_interval(mins => %s)
                FROM model_calls
                WHERE occurred_at > now() - make_interval(mins => %s)
                  AND ((decision = 'allow' AND NOT cache_hit) OR rule_id = 'provider-error')
                GROUP BY resolved_model ORDER BY 2 DESC, 3 DESC
                """,
                (window, window),
            ).fetchall()
        if attempts:
            print(f"\n## Provider health, rolling {window}m\n")
            print("| Model | Failed/attempts | State |\n|---|---|---|")
            for model_id, errors, calls, lifts_by in attempts:
                try:
                    skip = evaluate_health(get_model(model_id), errors, calls)
                except Exception:
                    continue  # a model no longer in the registry cannot be steered to
                state = (f"SKIPPED by steering — lifts by {lifts_by:%H:%M:%S} at the latest"
                         if skip else "ok")
                print(f"| {model_id} | {errors}/{calls} | {state} |")

    # Budget headroom, over the budget window rather than --hours: enforcement and
    # report must read the same number or the report is decoration.
    cfg = budgets()
    if cfg:
        hours = cfg.get("window_hours", 24)
        with owner_conn() as conn:
            spend = dict(conn.execute(
                """
                SELECT resolved_model, coalesce(sum(cost_usd), 0) FROM model_calls
                WHERE occurred_at > now() - make_interval(hours => %s)
                GROUP BY resolved_model
                """,
                (hours,),
            ).fetchall())
            by_principal = conn.execute(
                """
                SELECT principal, coalesce(sum(cost_usd), 0) FROM model_calls
                WHERE occurred_at > now() - make_interval(hours => %s)
                GROUP BY principal ORDER BY 2 DESC
                """,
                (hours,),
            ).fetchall()
        total = float(sum(spend.values()))
        print(f"\n## Budgets, rolling {hours}h\n")
        rows = [("total", total, cfg.get("total_usd"))]
        rows += [(m, float(spend.get(m, 0)), cap) for m, cap in (cfg.get("models") or {}).items()]
        # Callers seen in the window, each against their own ceiling. A NULL
        # principal is the shared anonymous bucket, metered like anyone else.
        rows += [(p or "(anonymous)", float(s), principal_cap(p)) for p, s in by_principal]
        print("| Scope | Spent | Ceiling | |\n|---|---|---|---|")
        for scope, spent, cap in rows:
            if cap is None:
                continue
            state = "EXCEEDED — paid calls degrade to the fallback" if spent >= cap else "ok"
            print(f"| {scope} | ${spent:.4f} | ${cap:.2f} | {state} |")

    return 0


if __name__ == "__main__":
    sys.exit(main())
