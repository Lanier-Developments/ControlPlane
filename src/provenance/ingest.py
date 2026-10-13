"""Load the corpus, chunk it, embed it, and write it to the owned schema.

ACLs travel with the document into doc_acl at ingest time. That is what makes
enforcement possible later: an ACL discovered at query time is already too late.

Every ingest reconciles: a (doc_id, version) in the database but no longer on disk
is deleted, cascading to its chunks and grants. A document removed at the source —
for retention, for legal, or just removed — must stop being retrievable at the next
ingest, not at the next --reset someone remembers to run. The evidence ledger keeps
its digests of past answers that cited it; the ledger records history, the index
serves the present.

Ingest is incremental: a document whose stored content hash and path both match the
disk is skipped, so a run over an unchanged corpus embeds nothing. Each run appends
a row to ingest_runs with a digest of the whole corpus; `--check` compares that
digest to the disk and names what drifted, and provenance/watch.py re-ingests
whenever they differ — which is what turns reconciliation-at-ingest into a stated
bound on staleness.
"""
import argparse
import hashlib
import sys
import uuid
from pathlib import Path

import frontmatter
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import settings
from .db import owner_conn
from .store import embeddings

REQUIRED = {"doc_id", "title", "department", "classification", "acl", "version"}
CHUNK_NAMESPACE = uuid.UUID("6f1c2a52-6b1e-4f2e-9a57-0d3c8e1b7a44")


def load_corpus(root: Path) -> list[Document]:
    docs = []
    for path in sorted(root.rglob("*.md")):
        post = frontmatter.load(path)
        missing = REQUIRED - post.metadata.keys()
        if missing:
            raise ValueError(f"{path}: missing frontmatter {sorted(missing)}")
        if not post.metadata["acl"]:
            raise ValueError(f"{path}: empty acl. Grant a group explicitly; there is no default.")
        meta = dict(post.metadata)
        meta["acl"] = list(meta["acl"])
        meta["source_path"] = str(path.relative_to(root))
        meta["content_sha"] = hashlib.sha256(path.read_bytes()).hexdigest()
        docs.append(Document(page_content=post.content.strip(), metadata=meta))
    return docs


def corpus_digest(docs: list[Document]) -> str:
    """One hash for the state of the whole corpus: every path and its content hash.

    Any added, removed, changed or moved file changes the digest; identical corpora
    produce identical digests regardless of load order.
    """
    lines = sorted(f"{d.metadata['source_path']}\x00{d.metadata['content_sha']}" for d in docs)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def last_ingested() -> tuple[str, str] | None:
    """(corpus_sha, finished_at) of the most recent ingest run, or None before any."""
    with owner_conn() as conn:
        row = conn.execute(
            "SELECT corpus_sha, finished_at FROM ingest_runs ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
    return (row[0], str(row[1])) if row else None


def chunk(doc: Document) -> list[tuple[str, int, str]]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap
    )
    out = []
    for i, piece in enumerate(splitter.split_text(doc.page_content)):
        key = f"{doc.metadata['doc_id']}:{doc.metadata['version']}:{i}"
        out.append((str(uuid.uuid5(CHUNK_NAMESPACE, key)), i, piece))
    return out


def migrate() -> None:
    sql = Path("db/schema.sql").read_text()
    with owner_conn() as conn:
        conn.execute(sql)
        conn.commit()


def run_ingest(reset: bool = False) -> dict:
    """One reconciling, incremental ingest. Returns a summary; records the run.

    A document whose stored content hash and path both match the disk is skipped
    entirely — no re-embed, no grant rewrite. The run row is written in the same
    transaction as the corpus changes, so the recorded digest can never describe
    an index state that was not committed with it.
    """
    docs = load_corpus(Path(settings.corpus_dir))
    embedder = embeddings()
    embedded = unchanged = chunks_written = 0

    with owner_conn() as conn:
        if reset:
            conn.execute("TRUNCATE documents CASCADE")

        stored = {
            (doc_id, version): (sha, path)
            for doc_id, version, sha, path in conn.execute(
                "SELECT doc_id, version, content_sha, source_path FROM documents"
            ).fetchall()
        }

        for doc in docs:
            m = doc.metadata
            if stored.get((m["doc_id"], m["version"])) == (m["content_sha"], m["source_path"]):
                unchanged += 1
                continue
            embedded += 1
            conn.execute(
                """
                INSERT INTO documents (doc_id, version, title, department, classification,
                                       status, effective_date, owner, source_path, content_sha)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (doc_id, version) DO UPDATE SET
                    title=EXCLUDED.title, department=EXCLUDED.department,
                    classification=EXCLUDED.classification, status=EXCLUDED.status,
                    effective_date=EXCLUDED.effective_date, owner=EXCLUDED.owner,
                    source_path=EXCLUDED.source_path, content_sha=EXCLUDED.content_sha,
                    ingested_at=now()
                """,
                (m["doc_id"], m["version"], m["title"], m["department"], m["classification"],
                 m.get("status", "active"), m.get("effective_date"), m.get("owner"),
                 m.get("source_path"), m["content_sha"]),
            )

            # Rewrite grants rather than adding to them: a revoked group must disappear.
            conn.execute("DELETE FROM doc_acl WHERE doc_id=%s AND version=%s", (m["doc_id"], m["version"]))
            for group in m["acl"]:
                conn.execute(
                    "INSERT INTO doc_acl (doc_id, version, group_name) VALUES (%s,%s,%s)",
                    (m["doc_id"], m["version"], group),
                )

            pieces = chunk(doc)
            # Drop chunks that no longer exist: a shortened document must not leave orphans.
            conn.execute(
                "DELETE FROM chunks WHERE doc_id=%s AND version=%s AND chunk_index >= %s",
                (m["doc_id"], m["version"], len(pieces)),
            )
            vectors = embedder.embed_documents([p[2] for p in pieces])
            for (chunk_id, index, content), vector in zip(pieces, vectors):
                conn.execute(
                    """
                    INSERT INTO chunks (chunk_id, doc_id, version, chunk_index, content, embedding)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        content=EXCLUDED.content, embedding=EXCLUDED.embedding
                    """,
                    (chunk_id, m["doc_id"], m["version"], index, content, str(vector)),
                )
            chunks_written += len(pieces)

        # Reconcile against the disk: what the corpus no longer contains, the index
        # must no longer serve. CASCADE takes the chunks and grants with it.
        ids = [d.metadata["doc_id"] for d in docs]
        versions = [d.metadata["version"] for d in docs]
        removed = conn.execute(
            """
            DELETE FROM documents d
            WHERE NOT EXISTS (
                SELECT 1 FROM unnest(%s::text[], %s::int[]) AS keep(doc_id, version)
                WHERE keep.doc_id = d.doc_id AND keep.version = d.version)
            RETURNING d.doc_id, d.version
            """,
            (ids, versions),
        ).fetchall()

        conn.execute(
            """
            INSERT INTO ingest_runs (run_id, corpus_sha, documents, embedded, unchanged, removed)
            VALUES (%s,%s,%s,%s,%s,%s)
            """,
            (str(uuid.uuid4()), corpus_digest(docs), len(docs), embedded, unchanged, len(removed)),
        )
        conn.commit()

    return {
        "documents": len(docs), "embedded": embedded, "unchanged": unchanged,
        "chunks_written": chunks_written, "removed": sorted(removed),
    }


def check() -> int:
    """Is the index serving what the source contains? 0 yes, 1 no — naming the drift."""
    docs = load_corpus(Path(settings.corpus_dir))
    disk = corpus_digest(docs)
    last = last_ingested()
    if last and last[0] == disk:
        print(f"Index matches the source: {len(docs)} documents, last ingest {last[1]}")
        return 0

    with owner_conn() as conn:
        indexed = dict(conn.execute("SELECT source_path, content_sha FROM documents").fetchall())
    on_disk = {d.metadata["source_path"]: d.metadata["content_sha"] for d in docs}
    added = sorted(set(on_disk) - set(indexed))
    gone = sorted(set(indexed) - set(on_disk))
    changed = sorted(p for p in set(on_disk) & set(indexed) if on_disk[p] != indexed[p])

    print("INDEX IS BEHIND THE SOURCE"
          + ("" if last else " (no ingest run recorded — run `make ingest`)"))
    for label, paths in (("added", added), ("removed", gone), ("changed", changed)):
        for p in paths:
            print(f"  {label}: {p}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest the corpus into the owned schema")
    ap.add_argument("--reset", action="store_true", help="delete all documents first")
    ap.add_argument("--migrate", action="store_true", help="apply db/schema.sql and exit")
    ap.add_argument("--check", action="store_true",
                    help="compare the index to the disk and exit non-zero on drift")
    args = ap.parse_args()

    if args.migrate:
        migrate()
        print("Schema applied")
        return 0
    if args.check:
        return check()

    s = run_ingest(reset=args.reset)
    print(f"Ingested {s['documents']} documents: {s['embedded']} embedded as "
          f"{s['chunks_written']} chunks, {s['unchanged']} unchanged")
    if s["removed"]:
        listed = ", ".join(f"{doc_id} v{version}" for doc_id, version in s["removed"])
        print(f"Removed {len(s['removed'])} no longer in the corpus: {listed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
