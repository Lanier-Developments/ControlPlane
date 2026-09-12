"""Load the corpus, chunk it, embed it, and upsert into pgvector.

Phase 1 captures ACL and classification metadata on every chunk but does not enforce it.
"""
import argparse
import uuid
from pathlib import Path

import frontmatter
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import settings

REQUIRED = {"doc_id", "title", "department", "classification", "acl", "version"}
CHUNK_NAMESPACE = uuid.UUID("6f1c2a52-6b1e-4f2e-9a57-0d3c8e1b7a44")


def _jsonable(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def load_corpus(root: Path) -> list[Document]:
    docs = []
    for path in sorted(root.rglob("*.md")):
        post = frontmatter.load(path)
        missing = REQUIRED - post.metadata.keys()
        if missing:
            raise ValueError(f"{path}: missing frontmatter {sorted(missing)}")
        meta = {k: _jsonable(v) for k, v in post.metadata.items()}
        meta["acl"] = list(meta["acl"])
        meta["source_path"] = str(path.relative_to(root))
        docs.append(Document(page_content=post.content.strip(), metadata=meta))
    return docs


def chunk(docs: list[Document]) -> tuple[list[Document], list[str]]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap
    )
    chunks, ids = [], []
    for doc in docs:
        for i, piece in enumerate(splitter.split_text(doc.page_content)):
            chunks.append(Document(page_content=piece, metadata={**doc.metadata, "chunk_index": i}))
            key = f"{doc.metadata['doc_id']}:{doc.metadata['version']}:{i}"
            ids.append(str(uuid.uuid5(CHUNK_NAMESPACE, key)))  # deterministic: re-ingest upserts
    return chunks, ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest the corpus into pgvector")
    parser.add_argument("--reset", action="store_true", help="drop and recreate the collection first")
    args = parser.parse_args()

    from .store import vector_store

    store = vector_store()
    if args.reset:
        store.delete_collection()
        store.create_collection()

    docs = load_corpus(Path(settings.corpus_dir))
    chunks, ids = chunk(docs)
    store.add_documents(chunks, ids=ids)
    print(f"Ingested {len(docs)} documents as {len(chunks)} chunks into '{settings.collection}'")


if __name__ == "__main__":
    main()
