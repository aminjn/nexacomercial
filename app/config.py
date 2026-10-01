"""Environment settings. Sites, accounts and campaigns live in the database (managed from the dashboard)."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    data_dir: str = "./data"

    # Dashboard protection (HTTP basic auth). Empty password = no auth (local only!).
    admin_user: str = "admin"
    admin_password: str = ""

    # Key used to encrypt account credentials at rest. Any string works; if empty a random
    # key is generated once and stored in <data_dir>/secret.key — back that file up.
    secret_key: str = ""

    # LLM provider: "openai" (any OpenAI-compatible server: Ollama, vLLM, OpenRouter...),
    # "anthropic" (official Claude API) or "fake" (deterministic text for tests/dry-runs).
    llm_provider: str = "openai"
    llm_base_url: str = "http://host.docker.internal:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:14b"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    llm_timeout_sec: float = 600.0

    # Scheduler: every `tick_seconds` due campaigns are processed (at most `max_jobs_per_tick`).
    scheduler_enabled: bool = True
    tick_seconds: int = 60
    max_jobs_per_tick: int = 5
    timezone: str = "Asia/Tehran"
    # Re-verify published backlinks this often.
    linkcheck_hours: int = 24
    # Pause an account automatically after this many consecutive failures.
    account_max_failures: int = 3

    # Generate content but don't call any publishing API. Great for a first run.
    dry_run: bool = False

    # HTTP timeouts for publishers.
    http_timeout_sec: float = 60.0

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_path / 'nexa.db'}"


settings = Settings()
