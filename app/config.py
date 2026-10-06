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

    # Public address of this dashboard (e.g. https://cm.nojanteb.ir). Uploaded images get links on it
    # so Instagram / Pinterest can fetch them. Empty = taken from the request.
    public_url: str = ""
    upload_max_mb: int = 10

    # Key used to encrypt account credentials at rest. Any string works; if empty a random
    # key is generated once and stored in <data_dir>/secret.key — back that file up.
    secret_key: str = ""

    # LLM provider: "ollama" (your own Ollama server, e.g. http://1.2.3.4:11434; api_key optional, sent as Bearer),
    # "openai" (any OpenAI-compatible API: OpenRouter, vLLM...), "anthropic" (official Claude API)
    # or "fake" (deterministic text for tests/dry-runs).
    llm_provider: str = "ollama"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str = ""
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

    # HTTP proxy for publishing requests only (e.g. http://user:pass@1.2.3.4:3128).
    # Needed on servers inside Iran, where Telegram / X / Meta / ... are not reachable directly.
    publish_proxy: str = ""

    # Built-in v2ray client (set from the settings page): a vless/vmess/trojan/ss share link. Xray runs
    # inside the app and publishing goes through it (takes priority over publish_proxy).
    v2ray_enabled: bool = False
    v2ray_link: str = ""
    v2ray_for_llm: bool = False  # also send AI requests through it (e.g. for the Claude API)
    v2ray_http_port: int = 10809
    v2ray_socks_port: int = 10808
    xray_bin: str = ""  # empty = <data_dir>/xray/xray or `xray` on PATH
    xray_download_url: str = ""  # empty = latest release from GitHub
    # Chromium for "<platform>_web" accounts (log in as a user). Empty = `chromium` on PATH.
    chromium_path: str = ""

    # HTTP timeouts for publishers.
    http_timeout_sec: float = 60.0

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def uploads_path(self) -> Path:
        p = self.data_path / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_path / 'nexa.db'}"


settings = Settings()
