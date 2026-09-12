from fastapi import FastAPI
from pydantic import BaseModel

from .chain import ask

app = FastAPI(title="provenance-rag", version="0.1.0")


class AskRequest(BaseModel):
    question: str
    user: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "phase": 1}


@app.post("/ask")
def ask_endpoint(req: AskRequest) -> dict:
    return ask(req.question, req.user)
