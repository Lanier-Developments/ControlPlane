"""Load the corpus, chunk it, embed it, and write it to the owned schema.

ACLs travel with the document into doc_acl at ingest time. That is what makes
enforcement possible later: an ACL discovered at query time is already too late.
"""
import argparse
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
        docs.append(Document(page_content=post.content.strip(), metadata=meta))
    return docs


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


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest the corpus into the owned schema")
    ap.add_argument("--reset", action="store_true", help="delete all documents first")
    ap.add_argument("--migrate", action="store_true", help="apply db/schema.sql and exit")
    args = ap.parse_args()

    if args.migrate:
        migrate()
        print("Schema applied")
        return

    docs = load_corpus(Path(settings.corpus_dir))
    embedder = embeddings()
    total = 0

    with owner_conn() as conn:
        if args.reset:
            conn.execute("TRUNCATE documents CASCADE")

        for doc in docs:
            m = doc.metadata
            conn.execute(
                """
                INSERT INTO documents (doc_id, version, title, department, classification,
                                       status, effective_date, owner, source_path)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (doc_id, version) DO UPDATE SET
                    title=EXCLUDED.title, department=EXCLUDED.department,
                    classification=EXCLUDED.classification, status=EXCLUDED.status,
                    effective_date=EXCLUDED.effective_date, owner=EXCLUDED.owner,
                    source_path=EXCLUDED.source_path, ingested_at=now()
                """,
                (m["doc_id"], m["version"], m["title"], m["department"], m["classification"],
                 m.get("status", "active"), m.get("effective_date"), m.get("owner"),
                 m.get("source_path")),
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
            total += len(pieces)
        conn.commit()

    print(f"Ingested {len(docs)} documents as {total} chunks")


if __name__ == "__main__":
    main()
