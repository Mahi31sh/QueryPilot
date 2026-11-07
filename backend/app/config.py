"""
Centralized application configuration.
Loads all environment variables into a typed, validated settings object
so the rest of the app never touches os.environ directly.
"""
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    database_url: str
    analytics_database_url: str | None = None

    # Auth
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # LLM
    groq_api_key: str
    llm_model: str = "openai/gpt-oss-20b"

    # Embeddings
    embedding_model: str = "all-MiniLM-L6-v2"
    faiss_index_path: str = "./embeddings/vector_store/schema_index"
    # "vector": embed the question and search the FAISS index (falls back to
    # keyword matching if the index or model is unavailable).
    # "keyword": skip the embedding model entirely (low-memory hosts).
    retrieval_mode: Literal["vector", "keyword"] = "vector"

    # App behavior
    environment: str = "development"
    cors_origins: str = "http://localhost:5173"
    max_query_rows: int = 500
    sql_retry_limit: int = 2
    statement_timeout_ms: int = 10000  # per-query execution limit

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]


settings = Settings()