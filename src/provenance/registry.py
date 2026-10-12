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
    # Some models reject a temperature parameter. Declared in the registry rather than
    # special-cased in code — the whole point is that model quirks are config.
    no_temperature: bool = False
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


def budgets() -> dict:
    return _policy().get("budgets") or {}


def evaluate_budget(model: Model, model_spend: float, total_spend: float) -> Decision | None:
    """Deny a model whose window spend has reached a configured ceiling.

    Pure: the caller supplies the spend figures (the gateway reads them from the
    ledger), so this decides the same way every time and policy_test can exercise
    it without a database. Returns None when no ceiling applies — a budget is not
    an allow, only a reason to refuse.

    Zero-cost models are exempt even from the total: a free call adds no spend,
    and a budget that blocks free traffic is a rate limit wearing the wrong name.
    """
    cfg = budgets()
    if not cfg or (model.cost_per_1k_input == 0 and model.cost_per_1k_output == 0):
        return None
    hours = cfg.get("window_hours", 24)
    cap = (cfg.get("models") or {}).get(model.id)
    if cap is not None and model_spend >= cap:
        return Decision(
            allowed=False,
            reason=f"{model.id} spent ${model_spend:.2f} of its ${cap:.2f} ceiling in the last {hours}h",
            rule_id="budget-model-exceeded",
            model=model,
        )
    total_cap = cfg.get("total_usd")
    if total_cap is not None and total_spend >= total_cap:
        return Decision(
            allowed=False,
            reason=f"total spend ${total_spend:.2f} reached the ${total_cap:.2f} ceiling in the last {hours}h",
            rule_id="budget-total-exceeded",
            model=model,
        )
    return None


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
