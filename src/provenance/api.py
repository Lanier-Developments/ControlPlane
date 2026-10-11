"""HTTP API and the demo console.

The console exists because the governance story is hard to see in a JSON blob and
obvious in a side-by-side: switch persona, ask the same question, watch the routing
and the retrieved set change. Nothing on the page is mocked — every field rendered
comes from a real response, so what is being demonstrated is the system rather than
a picture of it.
"""
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .auth import Principal, current_principal, require_admin, validate_config
from .chain import ask
from .db import assert_rls_enforced
from .identity import UnknownPrincipal

app = FastAPI(title="ControlPlane", version="0.7.0")

CONSOLE = Path(__file__).parent / "console.html"

# The console's example questions live here, not in the page, so demo mode can
# enforce them. A client-side list would be a suggestion; this is a control.
EXAMPLES = [
    {"label": "Salary band (confidential)", "persona": "dana",
     "question": "What is the salary band for a Senior Engineer II?"},
    {"label": "Same question, no entitlement", "persona": "sam",
     "question": "What is the salary band for a Senior Engineer II?"},
    {"label": "Rollback command (contractor)", "persona": "riley",
     "question": "How do I roll back the payments API after a bad deploy?"},
    {"label": "Public fact", "persona": "riley",
     "question": "When was Kestrel Ridge Outfitters founded?"},
    {"label": "Version conflict", "persona": "sam",
     "question": "How many PTO days do full-time employees get per year?"},
    {"label": "Authority conflict", "persona": "sam",
     "question": "How long do we retain access and audit logs?"},
    {"label": "Near-duplicate stores", "persona": "marcus",
     "question": "Does Store 27 in Boulder follow the standard store hours?"},
    {"label": "Legal hold (restricted)", "persona": "sam",
     "question": "What is the Cascade Freight legal hold about and who are the custodians?"},
    {"label": "Unanswerable", "persona": "sam",
     "question": "What is the company's 401(k) match?"},
]
CANNED = {e["question"] for e in EXAMPLES}


@app.on_event("startup")
def verify_enforcement() -> None:
    """Refuse to serve if auth is misconfigured or the retrieval role is not constrained."""
    validate_config()
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
    """Personas and their entitlements. The stand-in for an identity provider.

    Persona mode only. With real authentication the table is not a thing callers
    should be able to read, and it is not the source of identity anyway.
    """
    from .config import settings
    from .identity import _table

    if settings.auth_mode != "persona":
        raise HTTPException(status_code=404, detail="Not found.")

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
def evidence_endpoint(limit: int = 10, _admin: Principal | None = Depends(require_admin)) -> dict:
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
def verify_endpoint(_admin: Principal | None = Depends(require_admin)) -> dict:
    """Recompute the hash chain. The button that makes the ledger a control."""
    from .evidence import verify

    return verify()


@app.get("/api/usage")
def usage_endpoint(hours: int = 24, _admin: Principal | None = Depends(require_admin)) -> dict:
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


@app.get("/api/demo")
def demo_status() -> dict:
    """What restrictions this instance is running under, plus its example questions."""
    from .demo import status

    return {**status(), "examples": EXAMPLES}


@app.post("/ask")
def ask_endpoint(
    req: AskRequest,
    request: Request,
    principal: Principal | None = Depends(current_principal),
) -> dict:
    from .demo import check_model, check_question, check_rate

    check_rate(request)
    check_model(req.model or None)
    check_question(req.question, CANNED)
    try:
        if principal is None:
            # Persona mode (development): identity is whatever the body claims.
            return ask(req.question, req.user, requested_model=req.model or None)
        if req.user:
            # Identity comes from the token. A body that tries to name one is either
            # a stale client or a spoofing attempt; neither gets a quiet pass.
            raise HTTPException(
                status_code=400,
                detail="Do not send 'user'. Identity is taken from the bearer token.",
            )
        return ask(
            req.question,
            principal.subject,
            requested_model=req.model or None,
            verified_groups=list(principal.groups),
        )
    except UnknownPrincipal as exc:
        raise HTTPException(status_code=403, detail=f"Unknown principal: {exc}") from None
