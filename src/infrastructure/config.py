"""
Application configuration management.

Uses pydantic-settings to load configuration from environment variables and .env files.
All secrets come from the environment — never hardcoded.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    url: SecretStr = Field(
        default="postgresql+asyncpg://trader:password@localhost:5432/trading_engine",
        alias="DATABASE_URL",
    )
    pool_size: int = Field(default=10, alias="DATABASE_POOL_SIZE")
    max_overflow: int = Field(default=20, alias="DATABASE_MAX_OVERFLOW")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class RedisSettings(BaseSettings):
    url: SecretStr = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    pool_size: int = Field(default=10, alias="REDIS_POOL_SIZE")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class KafkaSettings(BaseSettings):
    bootstrap_servers: str = Field(default="localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    consumer_group: str = Field(default="trading-engine", alias="KAFKA_CONSUMER_GROUP")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class AlpacaSettings(BaseSettings):
    api_key: SecretStr = Field(default="", alias="ALPACA_API_KEY")
    secret_key: SecretStr = Field(default="", alias="ALPACA_SECRET_KEY")
    base_url: str = Field(
        default="https://paper-api.alpaca.markets", alias="ALPACA_BASE_URL"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class PolygonSettings(BaseSettings):
    api_key: SecretStr = Field(default="", alias="POLYGON_API_KEY")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class LLMSettings(BaseSettings):
    openai_api_key: SecretStr = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")
    anthropic_api_key: SecretStr = Field(default="", alias="ANTHROPIC_API_KEY")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class ObservabilitySettings(BaseSettings):
    metrics_port: int = Field(default=9090, alias="METRICS_PORT")
    otel_endpoint: str = Field(
        default="http://localhost:4317", alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    otel_service_name: str = Field(default="trading-engine", alias="OTEL_SERVICE_NAME")
    pagerduty_key: SecretStr = Field(default="", alias="PAGERDUTY_INTEGRATION_KEY")
    slack_webhook: SecretStr = Field(default="", alias="SLACK_WEBHOOK_URL")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class SecuritySettings(BaseSettings):
    jwt_secret_key: SecretStr = Field(alias="JWT_SECRET_KEY", default="change-me")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=60, alias="JWT_EXPIRE_MINUTES")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class RiskSettings(BaseSettings):
    max_position_pct: float = Field(default=0.10, alias="RISK_MAX_POSITION_PCT")
    max_portfolio_var_pct: float = Field(default=0.02, alias="RISK_MAX_PORTFOLIO_VAR_PCT")
    max_drawdown_pct: float = Field(default=0.15, alias="RISK_MAX_DRAWDOWN_PCT")
    max_daily_loss_pct: float = Field(default=0.03, alias="RISK_MAX_DAILY_LOSS_PCT")
    correlation_threshold: float = Field(default=0.85, alias="RISK_CORRELATION_THRESHOLD")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class ExecutionSettings(BaseSettings):
    default_order_type: str = Field(default="LIMIT", alias="EXECUTION_DEFAULT_ORDER_TYPE")
    slippage_bps: int = Field(default=5, alias="EXECUTION_SLIPPAGE_BPS")
    max_participation_rate: float = Field(
        default=0.10, alias="EXECUTION_MAX_PARTICIPATION_RATE"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class AppSettings(BaseSettings):
    env: Literal["development", "paper", "shadow", "live"] = Field(
        default="development", alias="APP_ENV"
    )
    port: int = Field(default=8000, alias="APP_PORT")
    secret_key: SecretStr = Field(default="change-me", alias="APP_SECRET_KEY")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Nested settings — loaded from same .env file
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    kafka: KafkaSettings = Field(default_factory=KafkaSettings)
    alpaca: AlpacaSettings = Field(default_factory=AlpacaSettings)
    polygon: PolygonSettings = Field(default_factory=PolygonSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("env")
    @classmethod
    def validate_env(cls, v: str) -> str:
        valid = {"development", "paper", "shadow", "live"}
        if v not in valid:
            raise ValueError(f"APP_ENV must be one of {valid}, got {v!r}")
        return v

    @property
    def is_live(self) -> bool:
        return self.env == "live"

    @property
    def is_paper(self) -> bool:
        return self.env == "paper"

    @property
    def is_development(self) -> bool:
        return self.env == "development"


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return the singleton application settings (cached after first load)."""
    return AppSettings()
