"""Prove the routing policy decides, and decides the same way every time.

Pure policy evaluation — no database, no model, no network. That means it runs in
milliseconds and can be a required check on every pull request, which is the only way
a policy test is worth having.

The cases that matter most are the refusals.
"""
import sys

from .registry import (
    CLASSIFICATION_ORDER,
    Model,
    RegistryError,
    default_model_id,
    evaluate,
    get_model,
    models,
    resolve,
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
        print(f"  {model.id:28} {model.tier:6} {model.status:10} "
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
        status="approved", max_classification="restricted",
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

    # Every registry entry must declare a classification the ordering knows about.
    for model in models().values():
        if model.max_classification not in CLASSIFICATION_ORDER:
            failures += 1
            print(f"  FAIL  {model.id} has an unknown max_classification")

    print("\n" + ("All policy checks passed" if not failures else f"{failures} POLICY CHECK(S) FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
