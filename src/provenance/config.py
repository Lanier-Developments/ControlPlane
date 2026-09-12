from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://rag:rag@localhost:5432/rag"
    # Retrieval role. Must be NOBYPASSRLS — never the owner.
    app_database_url: str = "postgresql+psycopg://rag_app:rag_app@localhost:5432/rag"
    personas_file: str = "evals/personas.yaml"
    corpus_dir: str = "corpus/seed"

    ollama_base_url: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text"

    llm_provider: str = "ollama"  # ollama | bedrock
    ollama_chat_model: str = "llama3.1:8b"
    llm_seed: int = 42
    bedrock_model_id: str = ""
    aws_region: str = "us-east-1"

    chunk_size: int = 800
    chunk_overlap: int = 100
    top_k: int = 8

    # Phase 3 retrieval
    retrieval_mode: str = "hybrid"  # vector | hybrid
    fetch_k: int = 20               # candidates per list before fusion
    rrf_k: int = 60                 # RRF damping; larger flattens rank influence
    vector_weight: float = 1.0
    keyword_weight: float = 1.0
    prefer_active: bool = True
    superseded_penalty: float = 0.3

    # Phase 5 gateway
    registry_file: str = "policy/models.yaml"
    routing_policy_file: str = "policy/routing.yaml"
    default_model: str = ""          # empty = registry default_model
    cache_enabled: bool = True
    cache_ttl_seconds: int = 3600
    litellm_base_url: str = "http://localhost:4000/v1"
    litellm_api_key: str = ""


settings = Settings()
