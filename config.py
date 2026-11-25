from functools import lru_cache
import logging
import os
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

from secret_store import SecretStoreError, decrypt_secret

logger = logging.getLogger(__name__)
ENV_FILE: Path | None = Path(__file__).resolve().parent / ".env"


class Settings(BaseModel):
    """Uygulama yapılandırma ayarları."""

    environment: Literal["development", "production"] = Field(default="development")
    gemini_api_key: str | None = Field(default=None)
    gemini_model: str = Field(default="gemini-3.8-flash", min_length=1)
    gemini_fallback_models: list[str] = Field(
        default_factory=lambda: ["gemini-3.5-flash", "gemini-3.1-flash-lite"]
    )
    gemini_timeout_seconds: float = Field(default=30.0, gt=0)
    api_access_keys: list[str] = Field(default_factory=list)
    rapidapi_proxy_secret: str | None = Field(default=None)
    rate_limit_per_minute: int = Field(default=30, ge=0)
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    log_level: str = Field(default="INFO")


def _parse_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_origins(raw: str | None) -> list[str]:
    origins = _parse_csv(raw)
    return origins or ["*"]


def _read_dotenv_values(env_path: Path | None) -> dict[str, str]:
    if env_path is None or not env_path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key:
            values[key] = val
    return values


def _resolve_api_key(env_values: dict[str, str]) -> str | None:
    def _get(name: str) -> str | None:
        return os.environ.get(name) or env_values.get(name) or None

    direct_key = _get("GEMINI_API_KEY")
    if direct_key:
        return direct_key.strip() or None

    encrypted_key = _get("GEMINI_API_KEY_ENCRYPTED")
    if not encrypted_key:
        return None

    try:
        return decrypt_secret(encrypted_key)
    except SecretStoreError as exc:
        logger.error("GEMINI_API_KEY_ENCRYPTED çözülemedi: %s", exc)
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Ortam değişkenleri ve .env dosyasından önbellekli ayar nesnesi döndürür."""
    env_values = _read_dotenv_values(ENV_FILE)

    def _get_var(name: str, default: str = "") -> str:
        return os.environ.get(name) or env_values.get(name) or default

    raw_env = _get_var("ENVIRONMENT", "development").strip().lower()
    environment: Literal["development", "production"] = (
        "production" if raw_env == "production" else "development"
    )

    try:
        timeout_seconds = float(_get_var("GEMINI_TIMEOUT_SECONDS", "30"))
    except ValueError:
        timeout_seconds = 30.0

    try:
        rate_limit = int(_get_var("RATE_LIMIT_PER_MINUTE", "30"))
    except ValueError:
        rate_limit = 30

    fallback_raw = _get_var("GEMINI_FALLBACK_MODELS", "")
    fallback_models = (
        _parse_csv(fallback_raw)
        if fallback_raw
        else ["gemini-3.5-flash", "gemini-3.1-flash-lite"]
    )

    return Settings(
        environment=environment,
        gemini_api_key=_resolve_api_key(env_values),
        gemini_model=_get_var("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash",
        gemini_fallback_models=fallback_models,
        gemini_timeout_seconds=timeout_seconds if timeout_seconds > 0 else 30.0,
        api_access_keys=_parse_csv(_get_var("API_ACCESS_KEYS")),
        rapidapi_proxy_secret=_get_var("RAPIDAPI_PROXY_SECRET").strip() or None,
        rate_limit_per_minute=rate_limit if rate_limit >= 0 else 30,
        cors_origins=_parse_origins(_get_var("CORS_ORIGINS") or None),
        log_level=_get_var("LOG_LEVEL", "INFO").upper(),
    )
