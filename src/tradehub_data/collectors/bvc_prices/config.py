import os
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from tradehub_data.collectors.bvc_prices.constants import (
    DEFAULT_ALLOWED_DOMAINS,
    DEFAULT_BVC_ACCEPT_LANGUAGE,
    DEFAULT_BVC_BASE_URL,
    DEFAULT_BVC_PRICE_JSON_ACCEPT,
    DEFAULT_BVC_PRICE_JSON_PATH,
    DEFAULT_BVC_PRICE_JSON_REFERER,
    DEFAULT_BVC_PRICE_SOURCE_PATHS,
    DEFAULT_BVC_USER_AGENT,
)
from tradehub_data.collectors.bvc_prices.errors import BvcConfigError


def _parse_bool(name: str, value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise BvcConfigError(f"{name} must be a boolean value")


def _parse_csv(value: str | None, default: list[str]) -> list[str]:
    if value is None or not value.strip():
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


class BvcPriceCollectorConfig(BaseModel):
    enabled: bool = True
    base_url: str = DEFAULT_BVC_BASE_URL
    source_paths: list[str] = Field(default_factory=lambda: list(DEFAULT_BVC_PRICE_SOURCE_PATHS))
    timeout_seconds: float = 20
    max_retries: int = 3
    retry_backoff_seconds: float = 2
    sleep_between_requests_ms: int = 500
    user_agent: str = DEFAULT_BVC_USER_AGENT
    allowed_domains: tuple[str, ...] = DEFAULT_ALLOWED_DOMAINS
    verify_ssl: bool = True
    ca_bundle_path: str | None = None
    fail_on_error: bool = False
    json_enabled: bool = True
    json_path: str = DEFAULT_BVC_PRICE_JSON_PATH
    json_page_limit: int = 50
    json_max_pages: int = 5
    json_accept_header: str = DEFAULT_BVC_PRICE_JSON_ACCEPT
    json_referer: str = DEFAULT_BVC_PRICE_JSON_REFERER
    accept_language: str = DEFAULT_BVC_ACCEPT_LANGUAGE

    @classmethod
    def from_env(cls) -> "BvcPriceCollectorConfig":
        base_url = os.getenv("BVC_BASE_URL", DEFAULT_BVC_BASE_URL)
        return cls(
            enabled=_parse_bool("BVC_PRICE_COLLECTOR_ENABLED", os.getenv("BVC_PRICE_COLLECTOR_ENABLED"), True),
            base_url=base_url,
            source_paths=_parse_csv(
                os.getenv("BVC_PRICE_COLLECTOR_SOURCE_URLS") or os.getenv("BVC_PRICE_COLLECTOR_SOURCE_PATHS"),
                list(DEFAULT_BVC_PRICE_SOURCE_PATHS),
            ),
            timeout_seconds=float(os.getenv("BVC_PRICE_COLLECTOR_TIMEOUT_SECONDS", "20")),
            max_retries=int(os.getenv("BVC_PRICE_COLLECTOR_MAX_RETRIES", "3")),
            retry_backoff_seconds=float(os.getenv("BVC_PRICE_COLLECTOR_RETRY_BACKOFF_SECONDS", "2")),
            sleep_between_requests_ms=int(os.getenv("BVC_PRICE_COLLECTOR_SLEEP_BETWEEN_REQUESTS_MS", "500")),
            user_agent=os.getenv("BVC_PRICE_COLLECTOR_USER_AGENT", DEFAULT_BVC_USER_AGENT),
            allowed_domains=tuple(_parse_csv(os.getenv("BVC_PRICE_COLLECTOR_ALLOWED_DOMAINS"), list(DEFAULT_ALLOWED_DOMAINS))),
            verify_ssl=_parse_bool("BVC_PRICE_COLLECTOR_VERIFY_SSL", os.getenv("BVC_PRICE_COLLECTOR_VERIFY_SSL"), True),
            ca_bundle_path=os.getenv("BVC_PRICE_COLLECTOR_CA_BUNDLE_PATH") or None,
            fail_on_error=_parse_bool("BVC_PRICE_COLLECTOR_FAIL_ON_ERROR", os.getenv("BVC_PRICE_COLLECTOR_FAIL_ON_ERROR"), False),
            json_enabled=_parse_bool("BVC_PRICE_COLLECTOR_JSON_ENABLED", os.getenv("BVC_PRICE_COLLECTOR_JSON_ENABLED"), True),
            json_path=os.getenv("BVC_PRICE_COLLECTOR_JSON_PATH", DEFAULT_BVC_PRICE_JSON_PATH),
            json_page_limit=int(os.getenv("BVC_PRICE_COLLECTOR_PAGE_LIMIT", "50")),
            json_max_pages=int(os.getenv("BVC_PRICE_COLLECTOR_MAX_PAGES", "5")),
            json_accept_header=os.getenv("BVC_PRICE_COLLECTOR_JSON_ACCEPT", DEFAULT_BVC_PRICE_JSON_ACCEPT),
            json_referer=os.getenv("BVC_PRICE_COLLECTOR_JSON_REFERER", DEFAULT_BVC_PRICE_JSON_REFERER),
            accept_language=os.getenv("BVC_PRICE_COLLECTOR_ACCEPT_LANGUAGE", DEFAULT_BVC_ACCEPT_LANGUAGE),
        )

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        return value.rstrip("/")

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout_seconds must be positive")
        return value

    @field_validator("max_retries")
    @classmethod
    def validate_retries(cls, value: int) -> int:
        if value < 0 or value > 5:
            raise ValueError("max_retries must be between 0 and 5")
        return value

    @field_validator("retry_backoff_seconds")
    @classmethod
    def validate_backoff(cls, value: float) -> float:
        if value < 0:
            raise ValueError("retry_backoff_seconds must be non-negative")
        return value

    @field_validator("sleep_between_requests_ms")
    @classmethod
    def validate_sleep(cls, value: int) -> int:
        if value < 0:
            raise ValueError("sleep_between_requests_ms must be non-negative")
        return value

    @field_validator("json_page_limit")
    @classmethod
    def validate_json_page_limit(cls, value: int) -> int:
        if value <= 0 or value > 500:
            raise ValueError("json_page_limit must be between 1 and 500")
        return value

    @field_validator("json_max_pages")
    @classmethod
    def validate_json_max_pages(cls, value: int) -> int:
        if value <= 0 or value > 20:
            raise ValueError("json_max_pages must be between 1 and 20")
        return value

    @field_validator("user_agent")
    @classmethod
    def validate_user_agent(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("user_agent must be non-empty")
        return value

    @field_validator("accept_language")
    @classmethod
    def validate_accept_language(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("accept_language must be non-empty")
        return value

    @field_validator("json_path")
    @classmethod
    def validate_json_path(cls, value: str) -> str:
        if value.startswith(("http://", "https://")):
            return value
        if not value.startswith("/"):
            raise ValueError("json_path must be an absolute path or HTTP(S) URL")
        return value

    @model_validator(mode="after")
    def validate_domains(self) -> "BvcPriceCollectorConfig":
        for url in [*self.source_urls, self.json_endpoint_base_url]:
            hostname = urlparse(url).hostname
            if hostname not in self.allowed_domains:
                raise BvcConfigError(f"source URL host is not allowed: {hostname}")
        return self

    @property
    def source_urls(self) -> list[str]:
        urls: list[str] = []
        for source_path in self.source_paths:
            if source_path.startswith(("http://", "https://")):
                urls.append(source_path)
            else:
                urls.append(urljoin(f"{self.base_url}/", source_path.lstrip("/")))
        return urls

    @property
    def json_endpoint_base_url(self) -> str:
        if self.json_path.startswith(("http://", "https://")):
            return self.json_path
        return urljoin(f"{self.base_url}/", self.json_path.lstrip("/"))
