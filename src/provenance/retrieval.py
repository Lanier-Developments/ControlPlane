"""Phase 4 retrieval: permission-scoped hybrid search.

Both candidate lists run through a row-level-security connection scoped to the caller's
groups. Chunks the caller cannot see are not filtered out of the results — they are
never in the result set. There is no post-filter to forget to apply.

Fusion is reciprocal rank fusion: score = sum over lists of 1/(k + rank). It needs no
score normalization between two incomparable scales (cosine distance and ts_rank).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.documents import Document

from .config import settings
from .db import app_conn
from .identity import groups_for
from .store import embeddings

SELECT_COLS = """
    c.chunk_id, c.doc_id, c.version, c.chunk_index, c.content,
    d.title, d.department, d.classification, d.status
"""

VECTOR_SQL = f"""
SELECT {SELECT_COLS}
FROM chunks c JOIN documents d ON d.doc_id = c.doc_id AND d.version = c.version
ORDER BY c.embedding <=> %(embedding)s::vector
LIMIT %(limit)s
"""

KEYWORD_SQL = f"""
SELECT {SELECT_COLS}
FROM chunks c JOIN documents d ON d.doc_id = c.doc_id AND d.version = c.version
WHERE to_tsvector('english', c.content) @@ websearch_to_tsquery('english', %(query)s)
ORDER BY ts_rank_cd(to_tsvector('english', c.content), websearch_to_tsquery('english', %(query)s)) DESC
LIMIT %(limit)s
"""

FIELDS = ["chunk_id", "doc_id", "version", "chunk_index", "content",
          "title", "department", "classification", "status"]


def _to_documents(rows) -> list[Document]:
    docs = []
    for row in rows:
        meta = dict(zip(FIELDS, row))
        content = meta.pop("content")
        meta["chunk_id"] = str(meta["chunk_id"])
        docs.append(Document(page_content=content, metadata=meta))
    return docs


def chunk_key(doc: Document) -> tuple:
    m = doc.metadata
    return (m["doc_id"], m["version"], m.get("chunk_index", 0))


@dataclass
class Fused:
    doc: Document
    score: float = 0.0
    ranks: dict[str, int] = field(default_factory=dict)


def reciprocal_rank_fusion(
    lists: dict[str, list[Document]], k: int, weights: dict[str, float] | None = None
) -> list[Fused]:
    weights = weights or {}
    fused: dict[tuple, Fused] = {}
    for name, docs in lists.items():
        weight = weights.get(name, 1.0)
        for rank, doc in enumerate(docs, start=1):
            entry = fused.setdefault(chunk_key(doc), Fused(doc=doc))
            entry.score += weight / (k + rank)
            entry.ranks[name] = rank
    return sorted(fused.values(), key=lambda f: f.score, reverse=True)


def apply_version_policy(fused: list[Fused]) -> list[Fused]:
    """Demote superseded documents instead of dropping them.

    A retired policy is still legitimately retrievable ("what did the old policy say?"),
    so filtering would be wrong. The penalty applies only when an active version of the
    same doc_id is also a candidate, so a retired document that is the only version
    available is never demoted below unrelated noise.
    """
    if not settings.prefer_active:
        return fused
    active = {f.doc.metadata["doc_id"] for f in fused if f.doc.metadata.get("status") != "superseded"}
    for f in fused:
        m = f.doc.metadata
        if m.get("status") == "superseded" and m["doc_id"] in active:
            f.score *= settings.superseded_penalty
            f.ranks["superseded_demoted"] = 1
    return sorted(fused, key=lambda f: f.score, reverse=True)


def search(question: str, user: str | None = None) -> list[Document]:
    """Retrieve top-k chunks the caller is entitled to. Enforcement is in the database."""
    groups = groups_for(user)
    vector = embeddings().embed_query(question)
    fetch_k = settings.fetch_k

    with app_conn(groups) as conn:
        vector_docs = _to_documents(
            conn.execute(VECTOR_SQL, {"embedding": str(vector), "limit": fetch_k}).fetchall()
        )
        if settings.retrieval_mode == "vector":
            return vector_docs[: settings.top_k]
        keyword_docs = _to_documents(
            conn.execute(KEYWORD_SQL, {"query": question, "limit": fetch_k}).fetchall()
        )

    fused = reciprocal_rank_fusion(
        {"vector": vector_docs, "keyword": keyword_docs},
        k=settings.rrf_k,
        weights={"vector": settings.vector_weight, "keyword": settings.keyword_weight},
    )
    fused = apply_version_policy(fused)

    out = []
    for f in fused[: settings.top_k]:
        doc = f.doc
        doc.metadata = {**doc.metadata, "_retrieval": {"score": round(f.score, 6), **f.ranks}}
        out.append(doc)
    return out
