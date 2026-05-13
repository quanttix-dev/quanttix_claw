"""Configuracao do handoff_server via env vars (Pydantic Settings)."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Settings injetadas via env / .env file."""

    # Loopback auth (compartilhado com quanttix_ai)
    QUANTTIX_HANDOFF_TOKEN: str = "REPLACE_ME_AT_POD_ENV"

    # Telegram bot externo (@Quanttix_Negotiator_bot)
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_WEBHOOK_SECRET: str = ""

    # Backend
    BACKEND_BASE_URL: str = "https://test.quanttix.com.br"
    BACKEND_SVC_EMAIL: str = "svc_quanttix_claw@quanttix.com"
    BACKEND_SVC_PASSWORD: str = ""
    BACKEND_TENANT_ID: str = ""
    BACKEND_EMPRESA_ID: str = ""
    BACKEND_FILIAL_ID: str = ""

    # Redis (compartilhado com quanttix_ai)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # Server bind
    HANDOFF_HOST: str = "127.0.0.1"
    HANDOFF_PORT: int = 18790

    # Telemetria
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
