from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .chain import ask
from .db import assert_rls_enforced
from .identity import UnknownPrincipal

app = FastAPI(title="provenance-rag", version="0.4.0")


@app.on_event("startup")
def verify_enforcement() -> None:
    """Refuse to serve if the retrieval role is not actually constrained."""
    assert_rls_enforced()


class AskRequest(BaseModel):
    question: str
    user: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "phase": 4}


@app.post("/ask")
def ask_endpoint(req: AskRequest) -> dict:
    try:
        return ask(req.question, req.user)
    except UnknownPrincipal as exc:
        raise HTTPException(status_code=403, detail=f"Unknown principal: {exc}") from None
