from functools import lru_cache

from langchain_ollama import OllamaEmbeddings

from .config import settings


@lru_cache
def embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(model=settings.embed_model, base_url=settings.ollama_base_url)
