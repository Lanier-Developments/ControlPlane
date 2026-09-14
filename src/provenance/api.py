"""HTTP API and the demo console.

The console exists because the governance story is hard to see in a JSON blob and
obvious in a side-by-side: switch persona, ask the same question, watch the routing
and the retrieved set change. Nothing on the page is mocked — every field rendered
comes from a real response, so what is being demonstrated is the system rather than
a picture of it.
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .chain import ask
from .db import assert_rls_enforced
from .identity import UnknownPrincipal

app = FastAPI(title="ControlPlane", version="0.7.0")

CONSOLE = Path(__file__).parent / "console.html"


@app.on_event("startup")
def verify_enforcement() -> None:
    """Refuse to serve if the retrieval role is not actually constrained."""
    assert_rls_enforced()


class AskRequest(BaseModel):
    question: str
    user: str | None = None
    model: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def console() -> str:
    return CONSOLE.read_text()


@app.get("/api/personas")
def personas() -> dict:
    """Personas and their entitlements. The stand-in for an identity provider."""
    from .identity import _table

    return {"personas": [{"id": k, "groups": v} for k, v in _table().items()]}


@app.get("/api/models")
def models_endpoint() -> dict:
    """The registry, as the console shows it. Adding a model here is a config edit."""
    from .registry import default_model_id, models

    return {
        "default": default_model_id(),
        "models": [
            {
                "id": m.id, "provider": m.provider, "tier": m.tier, "status": m.status,
                "max_classification": m.max_classification,
                "cost_per_1k_input": m.cost_per_1k_input,
                "cost_per_1k_output": m.cost_per_1k_output,
            }
            for m in models().values()
        ],
    }


@app.get("/api/policy")
def policy_endpoint() -> dict:
    """The routing rules, in evaluation order, ending in a deny."""
    from .registry import _policy

    p = _policy()
    return {"rules": p["rules"], "fallback_model": p.get("fallback_model")}


@app.get("/api/evidence")
def evidence_endpoint(limit: int = 10) -> dict:
    """Recent ledger entries. Digests only — the ledger never stores answer text."""
    from .db import owner_conn

    with owner_conn() as conn:
        rows = conn.execute(
            """
            SELECT seq, occurred_at, principal, question, classification, decision,
                   model_id, rule_id, answer_sha256, cost_usd, entry_hash
            FROM evidence ORDER BY seq DESC LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return {
        "entries": [
            {
                "seq": r[0], "occurred_at": r[1].isoformat(), "principal": r[2],
                "question": r[3], "classification": r[4], "decision": r[5],
                "model": r[6], "rule_id": r[7], "answer_sha256": r[8],
                "cost_usd": float(r[9]) if r[9] is not None else None,
                "entry_hash": r[10],
            }
            for r in rows
        ]
    }


@app.get("/api/verify")
def verify_endpoint() -> dict:
    """Recompute the hash chain. The button that makes the ledger a control."""
    from .evidence import verify

    return verify()


@app.get("/api/usage")
def usage_endpoint(hours: int = 24) -> dict:
    """Spend and policy decisions, grouped by the rule that produced them."""
    from .db import owner_conn

    with owner_conn() as conn:
        totals = conn.execute(
            """
            SELECT count(*), count(*) FILTER (WHERE decision = 'deny'),
                   coalesce(sum(cost_usd), 0)
            FROM model_calls WHERE occurred_at > now() - make_interval(hours => %s)
            """,
            (hours,),
        ).fetchone()
        by_rule = conn.execute(
            """
            SELECT rule_id, decision, count(*), coalesce(sum(cost_usd), 0)
            FROM model_calls WHERE occurred_at > now() - make_interval(hours => %s)
            GROUP BY rule_id, decision ORDER BY count(*) DESC
            """,
            (hours,),
        ).fetchall()

    return {
        "calls": totals[0], "denied": totals[1], "cost_usd": float(totals[2]),
        "by_rule": [
            {"rule_id": r[0], "decision": r[1], "calls": r[2], "cost_usd": float(r[3])}
            for r in by_rule
        ],
    }


@app.post("/ask")
def ask_endpoint(req: AskRequest) -> dict:
    try:
        return ask(req.question, req.user, requested_model=req.model or None)
    except UnknownPrincipal as exc:
        raise HTTPException(status_code=403, detail=f"Unknown principal: {exc}") from None
