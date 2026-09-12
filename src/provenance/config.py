from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://rag:rag@localhost:5432/rag"
    collection: str = "corpus_v1"
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
    top_k: int = 5


settings = Settings()
