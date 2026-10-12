"""The gateway: the only place in the system that calls a model.

Nothing else imports a provider client. That is the whole point — a policy layer that
can be bypassed by one direct call is decoration. Every call through here is evaluated
against policy, cached, priced, and logged, including the ones that are refused.

Providers are resolved from the registry entry, so a new provider is a registry edit
plus an adapter function, never a change to calling code.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field

from .config import settings
from .db import owner_conn
from .registry import (
    Decision,
    Model,
    budgets,
    evaluate,
    evaluate_budget,
    fallback_model_id,
    get_model,
    max_classification,
    resolve,
)


class PolicyDenied(Exception):
    """Raised when no approved model may see this content."""


@dataclass
class Completion:
    text: str
    model_id: str
    cache_hit: bool
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    correlation_id: str
    decisions: list[Decision] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """Rough token count: ~4 characters per token.

    Deliberately approximate and labelled as such. Exact accounting needs provider
    usage fields, which Phase 6 records from the response. An estimate that everyone
    knows is an estimate beats a precise-looking number nobody checked.
    """
    return max(1, len(text) // 4)


def cache_key(model_id: str, prompt: str) -> str:
    return hashlib.sha256(f"{model_id}\x00{prompt}".encode()).hexdigest()


def _cache_get(key: str) -> str | None:
    if not settings.cache_enabled:
        return None
    with owner_conn() as conn:
        row = conn.execute(
            """
            UPDATE response_cache SET hits = hits + 1
            WHERE cache_key = %s AND created_at > now() - make_interval(secs => %s)
            RETURNING response
            """,
            (key, settings.cache_ttl_seconds),
        ).fetchone()
        conn.commit()
    return row[0] if row else None


def _cache_put(key: str, model_id: str, response: str) -> None:
    if not settings.cache_enabled:
        return
    with owner_conn() as conn:
        conn.execute(
            """
            INSERT INTO response_cache (cache_key, model_id, response) VALUES (%s, %s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET response = EXCLUDED.response,
                                                  created_at = now(), hits = 0
            """,
            (key, model_id, response),
        )
        conn.commit()


def _log_call(**kw) -> None:
    with owner_conn() as conn:
        conn.execute(
            """
            INSERT INTO model_calls (call_id, correlation_id, principal, requested_model,
                                     resolved_model, classification, decision, reason,
                                     rule_id, cache_hit, prompt_tokens, completion_tokens,
                                     cost_usd, latency_ms)
            VALUES (%(call_id)s, %(correlation_id)s, %(principal)s, %(requested_model)s,
                    %(resolved_model)s, %(classification)s, %(decision)s, %(reason)s,
                    %(rule_id)s, %(cache_hit)s, %(prompt_tokens)s, %(completion_tokens)s,
                    %(cost_usd)s, %(latency_ms)s)
            """,
            kw,
        )
        conn.commit()


def _budget_denial(model: Model) -> Decision | None:
    """The ledger is the source of truth for spend, so enforcement reads it too.

    One query, and only for models that cost money under a configured budget —
    the all-local path never touches this. Spend inside the window is summed at
    decision time rather than kept as a counter, so there is nothing to reset
    and nothing that can drift from what the ledger says was spent.
    """
    if not budgets() or (model.cost_per_1k_input == 0 and model.cost_per_1k_output == 0):
        return None
    hours = budgets().get("window_hours", 24)
    with owner_conn() as conn:
        total, for_model = conn.execute(
            """
            SELECT coalesce(sum(cost_usd), 0),
                   coalesce(sum(cost_usd) FILTER (WHERE resolved_model = %s), 0)
            FROM model_calls
            WHERE occurred_at > now() - make_interval(hours => %s)
            """,
            (model.id, hours),
        ).fetchone()
    return evaluate_budget(model, float(for_model), float(total))


def _invoke(model: Model, prompt: str, system: str | None) -> str:
    """Provider dispatch. The only code that touches a model client."""
    from langchain_core.messages import HumanMessage, SystemMessage

    messages = ([SystemMessage(content=system)] if system else []) + [HumanMessage(content=prompt)]

    if model.provider == "ollama":
        from langchain_ollama import ChatOllama

        client = ChatOllama(
            model=model.model,
            base_url=settings.ollama_base_url,
            temperature=0,
            seed=settings.llm_seed,
            num_predict=settings.ollama_num_predict,
            client_kwargs={"timeout": settings.ollama_timeout_seconds},
        )
    elif model.provider == "bedrock":
        from langchain_aws import ChatBedrockConverse

        model_id = model.model or settings.bedrock_model_id
        if not model_id:
            raise RuntimeError(f"{model.id}: no model id. Set BEDROCK_MODEL_ID.")
        # Some models reject temperature outright (Claude Sonnet 5 among them) and
        # langchain warns then drops it. Note the consequence rather than burying it
        # in a warning: those runs are not reproducible the way the local ones are,
        # so an eval diff against them can move without anything having changed.
        kwargs = {} if model.no_temperature else {"temperature": 0}
        client = ChatBedrockConverse(
            model=model_id, region_name=settings.aws_region, **kwargs
        )
    elif model.provider == "vertex":
        from langchain_google_genai import ChatGoogleGenerativeAI

        kwargs = {} if model.no_temperature else {"temperature": 0}
        client = ChatGoogleGenerativeAI(
            model=model.model,
            vertexai=True,
            project=settings.gcp_project,
            location=settings.gcp_location,
            **kwargs,
        )
    elif model.provider == "litellm":
        # LiteLLM as a drop-in proxy: same registry entry, different transport.
        from langchain_openai import ChatOpenAI

        client = ChatOpenAI(
            model=model.model,
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key or "unused",
            temperature=0,
        )
    else:
        raise RuntimeError(f"{model.id}: unknown provider {model.provider}")

    content = client.invoke(messages).content
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def complete(
    prompt: str,
    *,
    system: str | None = None,
    classifications: list[str] | None = None,
    groups: list[str] | None = None,
    principal: str | None = None,
    requested_model: str | None = None,
    correlation_id: str | None = None,
) -> Completion:
    """Run a model call under policy.

    `classifications` are the classifications of everything in the context. The most
    sensitive one governs: a single confidential chunk constrains the whole call,
    because the prompt is indivisible once it is sent.
    """
    correlation_id = correlation_id or str(uuid.uuid4())
    classification = max_classification(classifications or [])
    requested_id = requested_model or settings.default_model or None

    decision, fallback = resolve(classification, groups, requested_id)
    decisions = [d for d in (decision, fallback) if d]
    chosen = fallback if (fallback and fallback.allowed) else decision

    def log(model_id, cache_hit, pt, ct, cost, latency, dec):
        _log_call(
            call_id=str(uuid.uuid4()),
            correlation_id=correlation_id,
            principal=principal,
            requested_model=requested_id or "(default)",
            resolved_model=model_id,
            classification=classification,
            decision="allow" if dec.allowed else "deny",
            reason=dec.reason,
            rule_id=dec.rule_id,
            cache_hit=cache_hit,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_usd=cost,
            latency_ms=latency,
        )

    # Record the refusal before anything else runs. A denial with no record is
    # indistinguishable from a policy that was never evaluated.
    if not decision.allowed:
        log(decision.model.id if decision.model else None, False, None, None, None, None, decision)

    if not chosen.allowed:
        raise PolicyDenied(
            f"No approved model may process {classification} content: {chosen.reason}"
        )

    # Budget, after policy: a model the rules allow may still be refused because its
    # window spend reached a ceiling. The denial is logged and the request degrades
    # to the fallback — same shape as a classification denial, and for the same
    # reason: refusing the request punishes the user for the model's spending.
    over = _budget_denial(chosen.model)
    if over:
        decisions.append(over)
        log(over.model.id, False, None, None, None, None, over)
        chosen = None
        fb_id = fallback_model_id()
        if fb_id and fb_id != over.model.id:
            fb = evaluate(get_model(fb_id), classification, groups)
            if fb.allowed and not _budget_denial(fb.model):
                decisions.append(fb)
                chosen = fb
        if chosen is None:
            raise PolicyDenied(f"Budget exhausted and no fallback available: {over.reason}")

    model = chosen.model
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    key = cache_key(model.id, full_prompt)

    cached = _cache_get(key)
    if cached is not None:
        log(model.id, True, 0, 0, 0.0, 0, chosen)
        return Completion(
            text=cached, model_id=model.id, cache_hit=True, cost_usd=0.0,
            prompt_tokens=0, completion_tokens=0, latency_ms=0,
            correlation_id=correlation_id, decisions=decisions,
        )

    started = time.monotonic()
    text = _invoke(model, prompt, system)
    latency_ms = int((time.monotonic() - started) * 1000)

    prompt_tokens = estimate_tokens(full_prompt)
    completion_tokens = estimate_tokens(text)
    cost = model.cost(prompt_tokens, completion_tokens)

    _cache_put(key, model.id, text)
    log(model.id, False, prompt_tokens, completion_tokens, cost, latency_ms, chosen)

    return Completion(
        text=text, model_id=model.id, cache_hit=False, cost_usd=cost,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        latency_ms=latency_ms, correlation_id=correlation_id, decisions=decisions,
    )
