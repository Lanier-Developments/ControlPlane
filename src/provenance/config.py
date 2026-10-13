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

    llm_provider: str = "ollama"  # ollama | bedrock | vertex
    ollama_chat_model: str = "llama3.1:8b"
    # Bounds on local generation. qwen3:8b on RED-001 (poisoned document last)
    # reproducibly generated for over an hour without returning — twice — while the
    # client waited on a connection with no timeout. A normal answer is a few hundred
    # tokens; these caps end a degenerate one instead of hanging the suite.
    ollama_num_predict: int = 4096
    ollama_timeout_seconds: int = 900
    llm_seed: int = 42
    bedrock_model_id: str = ""
    aws_region: str = "us-east-1"
    # Gemini Enterprise Agent Platform (formerly Vertex AI). Auth comes from
    # Application Default Credentials (gcloud auth application-default login, or
    # GOOGLE_APPLICATION_CREDENTIALS for a service account), never from this file.
    gcp_project: str = ""
    gcp_location: str = "us-central1"

    # How often the corpus watcher polls (provenance/watch.py): the stated bound on
    # how long a source change can go undetected.
    watch_interval_seconds: float = 2.0

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
    # Demotion or exclusion: with this on, a superseded chunk is dropped in the
    # retrieval SQL whenever the caller can see an active version of the same
    # document — it never reaches context, rather than reaching it demoted. Off,
    # the penalty above applies instead. The trade: exclusion closes the measured
    # case of stale text in a small persona's context window; demotion keeps
    # "what did the old policy say?" answerable. Pick per deployment.
    superseded_exclude: bool = False

    # Phase 5 gateway
    registry_file: str = "policy/models.yaml"
    routing_policy_file: str = "policy/routing.yaml"
    default_model: str = ""          # empty = registry default_model
    cache_enabled: bool = True
    cache_ttl_seconds: int = 3600
    # Phase 7: wrap each context document in explicit data fences and restate the
    # instruction boundary after the context. Toggleable so its effect is measurable.
    context_fencing: bool = True
    # Registry id of the model the attribution judge asks; empty = the default model,
    # which means the judged model grades itself. Independence is the reason to set
    # it, not accuracy — the measured G32 miss was the judge prompt, not the grader
    # (docs/FINDINGS.md).
    judge_model: str = ""
    litellm_base_url: str = "http://localhost:4000/v1"
    litellm_api_key: str = ""

    # Authentication. "persona" trusts the request body (development, evals, demo
    # console). "oidc" derives identity from a verified bearer JWT.
    auth_mode: str = "persona"
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_algorithms: str = "RS256"   # comma-separated, asymmetric only
    oidc_groups_claim: str = "groups"
    oidc_admin_group: str = "cp-admin"
    oidc_leeway_seconds: int = 30

    # Demo mode: for a publicly reachable instance.
    # Off by default so local development is unrestricted.
    demo_mode: bool = False
    demo_allowed_models: str = "ollama/llama3.1:8b"  # comma-separated registry ids
    demo_rate_per_minute: int = 6
    demo_canned_only: bool = True


settings = Settings()
