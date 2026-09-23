"""Runtime configuration.

Two layers:

* :class:`Settings` - secrets and paths, read from environment variables / ``.env``.
* :class:`AppConfig` - tunables (models, limits, concurrency), read from ``config/settings.yaml``.

Relative paths are resolved against the project root (the directory containing
``pyproject.toml``) so the app behaves the same regardless of the working directory.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import AliasChoices, BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _find_project_root() -> Path:
    """Return the repository root for a source checkout, else the working directory."""
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").exists():
        return candidate
    return Path.cwd()


PROJECT_ROOT = _find_project_root()


class Settings(BaseSettings):
    """Secrets and filesystem locations, loaded from the environment and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: SecretStr | None = None
    sec_api_key: SecretStr | None = None
    earningscall_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("EARNINGSCALL_API_KEY", "EARNING_CALL_API"),
    )
    data_dir: Path = Path("data")
    config_file: Path = Path("config/settings.yaml")
    log_level: str = "INFO"

    def model_post_init(self, __context: object) -> None:
        """Anchor relative paths at the project root."""
        if not self.data_dir.is_absolute():
            self.data_dir = PROJECT_ROOT / self.data_dir
        if not self.config_file.is_absolute():
            self.config_file = PROJECT_ROOT / self.config_file

    @property
    def cache_dir(self) -> Path:
        """Directory for embedding caches."""
        return self.data_dir / ".cache"

    @property
    def prompt_store_path(self) -> Path:
        """JSON file holding user-edited prompts and their history."""
        return self.data_dir / ".settings" / "report_prompts.json"

    def missing_keys(self) -> list[str]:
        """Names of API keys that are not configured."""
        keys = {
            "OPENAI_API_KEY": self.openai_api_key,
            "SEC_API_KEY": self.sec_api_key,
            "EARNINGSCALL_API_KEY": self.earningscall_api_key,
        }
        return [name for name, value in keys.items() if not value or not value.get_secret_value()]


class ModelConfig(BaseModel):
    """LLM and embedding model selection."""

    extraction: str = "gpt-4o-mini"
    report: str = "gpt-5.4"
    formatting: str = "gpt-5.4"
    embedding: str = "text-embedding-3-small"
    reasoning_effort: str | None = "high"


class DocumentLimits(BaseModel):
    """How many documents of each type to keep per company."""

    annual_reports: int = Field(default=5, ge=0, le=20)
    quarterly_reports: int = Field(default=8, ge=0, le=40)
    proxy_statements: int = Field(default=1, ge=0, le=10)
    earnings_calls: int = Field(default=8, ge=0, le=40)


class ConcurrencyConfig(BaseModel):
    """Thread-pool sizes."""

    collection_workers: int = Field(default=4, ge=1, le=32)
    report_workers: int = Field(default=2, ge=1, le=16)
    llm_workers: int = Field(default=5, ge=1, le=32)
    pdf_workers: int = Field(default=8, ge=1, le=64)


class RetrievalConfig(BaseModel):
    """Chunking and embedding-cache options."""

    chunk_size: int = Field(default=4000, ge=500)
    chunk_overlap: int = Field(default=400, ge=0)
    use_cache: bool = True


class ReportConfig(BaseModel):
    """Report export options."""

    brand_name: str = "Involabs Financial Agent"
    letterhead_image: Path | None = None


class ModelPrice(BaseModel):
    """USD per one million tokens."""

    input: float = 0.0
    output: float = 0.0


class AppConfig(BaseModel):
    """Non-secret application configuration."""

    models: ModelConfig = ModelConfig()
    documents: DocumentLimits = DocumentLimits()
    concurrency: ConcurrencyConfig = ConcurrencyConfig()
    retrieval: RetrievalConfig = RetrievalConfig()
    report: ReportConfig = ReportConfig()
    pricing: dict[str, ModelPrice] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> AppConfig:
        """Load configuration from ``path``; fall back to defaults if the file is absent."""
        if not path.exists():
            logger.info("Config file %s not found; using defaults", path)
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        config = cls.model_validate(data)
        image = config.report.letterhead_image
        if image is not None and not image.is_absolute():
            config.report.letterhead_image = PROJECT_ROOT / image
        return config


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide :class:`Settings` singleton."""
    return Settings()


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Process-wide :class:`AppConfig` singleton."""
    return AppConfig.from_yaml(get_settings().config_file)
