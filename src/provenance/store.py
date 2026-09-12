from functools import lru_cache

from langchain_ollama import OllamaEmbeddings
from langchain_postgres import PGVector

from .config import settings


@lru_cache
def embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(model=settings.embed_model, base_url=settings.ollama_base_url)


@lru_cache
def vector_store() -> PGVector:
    return PGVector(
        embeddings=embeddings(),
        collection_name=settings.collection,
        connection=settings.database_url,
        use_jsonb=True,
    )
