"""Central configuration, read from environment variables (and an optional .env file).

Secrets (MISTRAL_API_KEY, OCQA_SECRET_KEY) are held as SecretStr so they never show up
in reprs or logs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Mistral -------------------------------------------------------------------------
    mistral_api_key: SecretStr | None = Field(default=None, alias="MISTRAL_API_KEY")
    mistral_base_url: str = Field(default="https://api.mistral.ai/v1", alias="MISTRAL_BASE_URL")
    mistral_model_planner: str = Field(default="mistral-large-latest", alias="MISTRAL_MODEL_PLANNER")
    mistral_model_code: str = Field(default="codestral-latest", alias="MISTRAL_MODEL_CODE")
    mistral_model_fast: str = Field(default="mistral-small-latest", alias="MISTRAL_MODEL_FAST")
    mistral_model_vision: str = Field(default="mistral-large-latest", alias="MISTRAL_MODEL_VISION")
    mistral_rps: float = Field(default=1.0, alias="MISTRAL_RPS")
    mistral_tpm: int = Field(default=400_000, alias="MISTRAL_TPM")
    mistral_max_retries: int = Field(default=5, alias="MISTRAL_MAX_RETRIES")
    mistral_timeout_s: float = Field(default=90.0, alias="MISTRAL_TIMEOUT_S")
    max_llm_calls_per_run: int = Field(default=60, alias="MAX_LLM_CALLS_PER_RUN")
    max_llm_calls_per_day: int = Field(default=500, alias="MAX_LLM_CALLS_PER_DAY")
    llm_temperature: float = Field(default=0.1, alias="LLM_TEMPERATURE")
    enable_vision_check: bool = Field(default=False, alias="ENABLE_VISION_CHECK")

    # --- App -----------------------------------------------------------------------------
    database_url: str = Field(default="", alias="DATABASE_URL")
    data_dir: Path = Field(default=REPO_ROOT / "data", alias="OCQA_DATA_DIR")
    secret_key: SecretStr | None = Field(default=None, alias="OCQA_SECRET_KEY")
    cors_origins: str = Field(default="http://localhost:3000,http://127.0.0.1:3000", alias="CORS_ORIGINS")
    start_worker: bool = Field(default=True, alias="OCQA_START_WORKER")

    # --- Security ------------------------------------------------------------------------
    allow_local_targets: bool = Field(default=False, alias="ALLOW_LOCAL_TARGETS")
    extra_allowed_domains: str = Field(default="", alias="EXTRA_ALLOWED_DOMAINS")

    # --- Crawl / run defaults --------------------------------------------------------------
    default_max_pages: int = Field(default=15, alias="DEFAULT_MAX_PAGES")
    default_max_depth: int = Field(default=3, alias="DEFAULT_MAX_DEPTH")
    request_delay_ms: int = Field(default=250, alias="REQUEST_DELAY_MS")
    max_journeys: int = Field(default=6, alias="MAX_JOURNEYS")
    test_timeout_s: int = Field(default=60, alias="TEST_TIMEOUT_S")
    perf_runs_per_page: int = Field(default=3, alias="PERF_RUNS_PER_PAGE")

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'oneclickqa.db').as_posix()}"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def has_llm_key(self) -> bool:
        return bool(self.mistral_api_key and self.mistral_api_key.get_secret_value().strip())

    @property
    def extra_domains(self) -> list[str]:
        return [d.strip().lower() for d in self.extra_allowed_domains.split(",") if d.strip()]

    def public_view(self) -> dict[str, object]:
        """Settings that are safe to show in the UI (never secrets)."""
        return {
            "llm_configured": self.has_llm_key,
            "models": {
                "planner": self.mistral_model_planner,
                "code": self.mistral_model_code,
                "fast": self.mistral_model_fast,
                "vision": self.mistral_model_vision,
            },
            "rps": self.mistral_rps,
            "tpm": self.mistral_tpm,
            "max_llm_calls_per_run": self.max_llm_calls_per_run,
            "max_llm_calls_per_day": self.max_llm_calls_per_day,
            "vision_check_enabled": self.enable_vision_check,
            "allow_local_targets": self.allow_local_targets,
            "default_max_pages": self.default_max_pages,
            "default_max_depth": self.default_max_depth,
        }


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.artifacts_dir.mkdir(parents=True, exist_ok=True)
    return s
