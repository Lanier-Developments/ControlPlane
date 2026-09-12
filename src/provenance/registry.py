"""Model registry and routing policy.

Two ideas carry the whole phase.

1. The registry is data. Adding, repricing, deprecating or retiring a model is an edit
   to policy/models.yaml. No enum, no code change, no release. The prior build this is
   modelled on selected models from an enum in application code, which meant the list of
   approved models lived in code, in a wiki, and in someone's head — three sources that
   disagreed.

2. Policy is data too, and it fails closed. Rules are evaluated in order and the last
   rule is a deny, so a policy file that fails to anticipate a case refuses rather than
   falling through to allow.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .config import settings

# Ordered least to most sensitive. A model's max_classification is a ceiling.
CLASSIFICATION_ORDER = ["public", "internal", "confidential", "restricted"]


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Model:
    id: str
    provider: str
    model: str
    tier: str
    status: str
    max_classification: str
    cost_per_1k_input: float
    cost_per_1k_output: float
    notes: str = ""

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return round(
            prompt_tokens / 1000 * self.cost_per_1k_input
            + completion_tokens / 1000 * self.cost_per_1k_output,
            6,
        )


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    rule_id: str
    model: Model | None = None


@lru_cache
def _registry() -> tuple[dict[str, Model], str]:
    raw = yaml.safe_load(Path(settings.registry_file).read_text())
    models = {}
    for entry in raw["models"]:
        unknown = entry["max_classification"] not in CLASSIFICATION_ORDER
        if unknown:
            raise RegistryError(f"{entry['id']}: unknown max_classification")
        models[entry["id"]] = Model(**entry)
    default = raw["default_model"]
    if default not in models:
        raise RegistryError(f"default_model {default} is not in the registry")
    return models, default


def models() -> dict[str, Model]:
    return _registry()[0]


def default_model_id() -> str:
    return _registry()[1]


def get_model(model_id: str) -> Model:
    try:
        return models()[model_id]
    except KeyError:
        raise RegistryError(f"{model_id} is not in the model registry") from None


@lru_cache
def _policy() -> dict:
    return yaml.safe_load(Path(settings.routing_policy_file).read_text())


def max_classification(classifications: list[str]) -> str:
    """The most sensitive classification present. An empty context is treated as public."""
    ranked = [c for c in classifications if c in CLASSIFICATION_ORDER]
    if not ranked:
        return "public"
    return max(ranked, key=CLASSIFICATION_ORDER.index)


def _matches(match: dict, facts: dict) -> bool:
    for key, want in match.items():
        if key == "groups_any":
            if not set(want) & set(facts.get("groups", [])):
                return False
        elif facts.get(key) != want:
            return False
    return True


def evaluate(model: Model, classification: str, groups: list[str] | None = None) -> Decision:
    """First matching rule decides. The final rule is a deny, so this cannot fall through."""
    facts = {
        "classification": classification,
        "tier": model.tier,
        "status": model.status,
        "groups": groups or [],
    }

    # The registry ceiling is checked before the rules. A rule file cannot grant a model
    # access to data more sensitive than its own entry permits.
    if CLASSIFICATION_ORDER.index(classification) > CLASSIFICATION_ORDER.index(
        model.max_classification
    ):
        return Decision(
            allowed=False,
            reason=f"{model.id} is not approved above {model.max_classification}",
            rule_id="registry-ceiling",
            model=model,
        )

    for rule in _policy()["rules"]:
        if _matches(rule.get("match", {}) or {}, facts):
            return Decision(
                allowed=rule["decision"] == "allow",
                reason=rule["reason"],
                rule_id=rule["id"],
                model=model,
            )

    # Unreachable with a well-formed policy file; still a deny if the file is malformed.
    return Decision(False, "Policy file has no default rule", "no-default-rule", model)


def fallback_model_id() -> str | None:
    return _policy().get("fallback_model")


def resolve(
    classification: str, groups: list[str] | None = None, requested: str | None = None
) -> tuple[Decision, Decision | None]:
    """Decide which model may answer.

    Returns the decision on the requested model and, when that was denied and a
    fallback is configured, the decision on the fallback. Both are returned so the
    call log records the refusal as well as what actually ran — a denial that leaves
    no trace is indistinguishable from a policy that was never applied.
    """
    model = get_model(requested or default_model_id())
    decision = evaluate(model, classification, groups)
    if decision.allowed:
        return decision, None

    fallback_id = fallback_model_id()
    if not fallback_id or fallback_id == model.id:
        return decision, None
    return decision, evaluate(get_model(fallback_id), classification, groups)
