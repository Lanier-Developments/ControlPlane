"""Prove the routing policy decides, and decides the same way every time.

Pure policy evaluation — no database, no model, no network. That means it runs in
milliseconds and can be a required check on every pull request, which is the only way
a policy test is worth having.

The cases that matter most are the refusals.
"""
import sys

import math

from .registry import (
    CLASSIFICATION_ORDER,
    Model,
    RegistryError,
    budgets,
    default_model_id,
    evaluate,
    evaluate_budget,
    evaluate_health,
    get_model,
    models,
    resolve,
    steering_health,
    steering_rule,
)

LOCAL = "ollama/llama3.1:8b"
CLOUD = "bedrock/claude-sonnet-4-6"
RETIRED = "ollama/llama2:7b"

# (model, classification, expected_allowed, why)
CASES = [
    (LOCAL, "public", True, "self-hosted inference is approved for everything"),
    (LOCAL, "internal", True, "self-hosted inference is approved for everything"),
    (LOCAL, "confidential", True, "confidential content stays on self-hosted hardware"),
    (LOCAL, "restricted", True, "restricted content stays on self-hosted hardware"),
    (CLOUD, "public", True, "public content may leave the network"),
    (CLOUD, "internal", True, "internal content is approved for the external provider"),
    (CLOUD, "confidential", False, "confidential content must not reach an external provider"),
    (CLOUD, "restricted", False, "restricted content must not reach an external provider"),
    (RETIRED, "public", False, "a retired registry entry is refused, not ignored"),
    (RETIRED, "restricted", False, "retired stays refused at every classification"),
]


def main() -> int:
    failures = 0

    print("Registry:")
    for model in models().values():
        print(f"  {model.id:28} {model.tier:6} {model.region:8} {model.status:10} "
              f"max={model.max_classification:12} "
              f"${model.cost_per_1k_input}/1k in  ${model.cost_per_1k_output}/1k out")
    print(f"  default: {default_model_id()}\n")

    print("Policy decisions:")
    for model_id, classification, expected, why in CASES:
        decision = evaluate(get_model(model_id), classification)
        ok = decision.allowed == expected
        failures += not ok
        verb = "allow" if decision.allowed else "DENY "
        print(f"  {'PASS' if ok else 'FAIL'}  {verb} {model_id:28} {classification:13}"
              f" [{decision.rule_id}] — {why}")

    # The registry ceiling must hold even if a rule would permit the combination.
    ceiling = evaluate(get_model(CLOUD), "restricted")
    if ceiling.allowed or ceiling.rule_id != "registry-ceiling":
        failures += 1
        print(f"  FAIL  registry ceiling did not fire first (got {ceiling.rule_id})")
    else:
        print(f"  PASS  registry ceiling outranks the rule file [{ceiling.rule_id}]")

    # Defense in depth: with the current registry the ceiling catches cloud + sensitive
    # data before the rule file is consulted, so the classification rules never fire.
    # An untested rule is false assurance, so exercise them against a synthetic entry
    # that a future registry edit could plausibly create.
    print("\nClassification rules (synthetic registry entry):")
    permissive_cloud = Model(
        id="cloud/over-permissive", provider="bedrock", model="x", tier="cloud",
        status="approved", max_classification="restricted", region="us",
        cost_per_1k_input=0.0, cost_per_1k_output=0.0,
        notes="Test fixture: a cloud model someone mis-registered as restricted-capable",
    )
    for classification, rule in [
        ("confidential", "confidential-stays-local"),
        ("restricted", "restricted-stays-local"),
    ]:
        decision = evaluate(permissive_cloud, classification)
        ok = not decision.allowed and decision.rule_id == rule
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  DENY  cloud {classification:13}"
              f" [{decision.rule_id}] — rule file catches a mis-registered model")

    # Residency: where inference runs is a match key, so a cloud model in an
    # unapproved region is refused even for public content — with the residency
    # reason, not the generic default. Synthetic entry: no non-US cloud model is
    # registered, and the rule must already be ready for the day one is.
    print("\nResidency (synthetic registry entry):")
    eu_cloud = Model(
        id="cloud/eu-hosted", provider="bedrock", model="x", tier="cloud",
        status="approved", max_classification="internal", region="eu-central",
        cost_per_1k_input=0.001, cost_per_1k_output=0.001,
        notes="Test fixture: an approved provider configured in a non-US region",
    )
    for classification in ("public", "internal"):
        decision = evaluate(eu_cloud, classification)
        ok = not decision.allowed and decision.rule_id == "residency-us-only"
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  DENY  eu-central cloud {classification:9}"
              f" [{decision.rule_id}] — residency denies what classification would allow")

    # A denied request should fall back to an approved model rather than failing,
    # and the refusal must still be visible in the returned decisions.
    denied, fallback = resolve("confidential", requested=CLOUD)
    if denied.allowed or not (fallback and fallback.allowed):
        failures += 1
        print("  FAIL  confidential request did not fall back to an approved model")
    else:
        print(f"  PASS  confidential falls back {CLOUD} -> {fallback.model.id}, "
              f"refusal recorded [{denied.rule_id}]")

    # An unknown model must raise rather than resolve to a default.
    try:
        get_model("openai/gpt-does-not-exist")
        failures += 1
        print("  FAIL  unknown model did not raise")
    except RegistryError:
        print("  PASS  unknown model raises rather than silently defaulting")

    # Budgets are policy too, and evaluate_budget is pure: spend figures come from the
    # caller, so the decision is testable here without the ledger. The committed policy
    # must actually configure ceilings — a budget section nobody wrote enforces nothing.
    print("\nBudgets:")
    cfg = budgets()
    paid = get_model(CLOUD)
    free = get_model(LOCAL)
    cap = (cfg.get("models") or {}).get(paid.id)
    p_cap = cfg.get("principal_usd")
    override = (cfg.get("principals") or {}).get("sam")
    # (model, model_spend, total_spend, principal, principal_spend, expected rule, why)
    budget_cases = [
        (paid, 0.0, 0.0, None, 0.0, None, "spend inside every ceiling passes"),
        (paid, cap, 0.0, None, 0.0, "budget-model-exceeded",
         "a model at its own ceiling is refused"),
        (paid, 0.0, cfg.get("total_usd"), None, 0.0, "budget-total-exceeded",
         "any paid call is refused once the total is reached"),
        (paid, 0.0, 0.0, "dana", p_cap, "budget-principal-exceeded",
         "a caller at the per-principal ceiling is refused"),
        (paid, 0.0, 0.0, "sam", p_cap, None,
         "a named override raises one caller's ceiling"),
        (paid, 0.0, 0.0, "sam", override, "budget-principal-exceeded",
         "the override is a ceiling too, not an exemption"),
        (paid, 0.0, 0.0, None, p_cap, "budget-principal-exceeded",
         "anonymous calls share one bucket under the default cap — no bypass by omission"),
        (free, 10**6, 10**6, "dana", 10**6, None,
         "a zero-cost model is exempt — budgets cap spend, not traffic"),
    ]
    if not cfg or cap is None or cfg.get("total_usd") is None or p_cap is None or override is None:
        failures += 1
        print("  FAIL  routing policy configures no budget ceilings")
    else:
        for model, model_spend, total_spend, who, p_spend, want_rule, why in budget_cases:
            d = evaluate_budget(model, model_spend, total_spend, who, p_spend)
            got_rule = d.rule_id if d else None
            ok = got_rule == want_rule and (d is None or not d.allowed)
            failures += not ok
            verb = "allow" if d is None else "DENY "
            print(f"  {'PASS' if ok else 'FAIL'}  {verb} {model.id:28}"
                  f" [{got_rule or '-'}] — {why}")

    # Steering is policy too: which model takes a request nobody pinned. The matched
    # rule's candidates run through the same evaluate() the gateway uses, so this
    # states exactly what an unpinned request of each classification gets today and
    # fails when a policy edit changes that silently.
    print("\nSteering (unpinned requests):")

    def first_allowed(rule, classification):
        for mid in (rule or {}).get("prefer", []):
            if evaluate(get_model(mid), classification).allowed:
                return mid
        return None

    steer_expect = {
        "public": ("steer-quality-cloud", CLOUD),
        "internal": ("steer-quality-cloud", CLOUD),
        "confidential": ("steer-local", LOCAL),
        "restricted": ("steer-local", LOCAL),
    }
    for classification, (want_rule, want_model) in steer_expect.items():
        rule = steering_rule(classification)
        got_rule = rule["id"] if rule else None
        try:
            picked = first_allowed(rule, classification)
        except RegistryError as exc:
            picked = None
            print(f"  FAIL  steering nominates a model not in the registry: {exc}")
        ok = got_rule == want_rule and picked == want_model
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {classification:13} -> {picked} [{got_rule}]")

    # Health is policy too, and evaluate_health is pure like evaluate_budget: the
    # attempt counts come from the caller (the gateway sums the ledger), so the skip
    # decision is testable here. Cases derive from the committed thresholds, so a
    # config edit moves the inputs with it rather than silently invalidating them.
    print("\nSteering health (synthetic attempt counts):")
    hcfg = steering_health()
    if not hcfg or hcfg.get("max_error_rate") is None:
        failures += 1
        print("  FAIL  steering configures no health threshold")
    else:
        rate, mc = hcfg["max_error_rate"], hcfg.get("min_calls", 2)
        trip = max(1, math.ceil(rate * mc))
        health_cases = [
            (0, 10 * mc, None, "a clean window is healthy"),
            (max(mc - 1, 0), max(mc - 1, 0), None,
             "below min_calls there is no judgment — one failure is an incident, not a pattern"),
            (trip, mc, "steering-unhealthy",
             "the configured error rate at min_calls trips the skip"),
            (mc, mc, "steering-unhealthy", "every attempt failing is skipped"),
            (trip, 100 * mc, None, "errors diluted by later successes recover"),
            (0, 0, None, "no attempts, no judgment"),
        ]
        for errors, calls, want, why in health_cases:
            d = evaluate_health(get_model(CLOUD), errors, calls)
            got = d.rule_id if d else None
            ok = got == want and (d is None or not d.allowed)
            failures += not ok
            verb = "allow" if d is None else "SKIP "
            print(f"  {'PASS' if ok else 'FAIL'}  {verb} {errors}/{calls} attempts failed"
                  f" [{got or '-'}] — {why}")

    # Defense in depth, same pattern as the classification rules: if a future edit
    # widens steer-quality-cloud to sensitive content, the nominated cloud model is
    # still refused by the rules and the local candidate wins — steering prefers,
    # policy permits.
    cloud_rule = steering_rule("public")
    for classification in ("confidential", "restricted"):
        picked = first_allowed(cloud_rule, classification)
        ok = picked == LOCAL
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {classification:13} under {cloud_rule['id']}"
              f" -> {picked} — a widened steering match cannot override the rules")

    # Every registry entry must declare a classification the ordering knows about.
    for model in models().values():
        if model.max_classification not in CLASSIFICATION_ORDER:
            failures += 1
            print(f"  FAIL  {model.id} has an unknown max_classification")

    # Prompts are registered artifacts too: the text in prompts.py must match the
    # version and hash pinned in policy/prompts.yaml, both directions. An edited
    # template without a version bump is drift, and drift fails the gate — FINDINGS
    # sections 16 and 18 are the measured case for this.
    from . import prompts as prompt_registry
    print("\nPrompt registry:")
    problems = prompt_registry.verify()
    for p in prompt_registry.PROMPTS.values():
        pinned_ok = not any(msg.startswith(p.id) for msg in problems)
        print(f"  {'PASS' if pinned_ok else 'FAIL'}  {p.ref}")
    for msg in problems:
        failures += 1
        print(f"  FAIL  {msg}")

    print("\n" + ("All policy checks passed" if not failures else f"{failures} POLICY CHECK(S) FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
