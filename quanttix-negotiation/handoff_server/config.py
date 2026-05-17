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

    # LLM classifier (Chat LLM via OpenAI-compat) — quando vazio, claw
    # cai para keyword classifier. Default aponta para o llama.cpp do
    # Qwen3-32B que ja roda no POD (Quanttix llm_api porta 8083).
    LLM_CLASSIFIER_URL: str = "http://127.0.0.1:8083"
    LLM_CLASSIFIER_MODEL: str = "qwen3-32b"

    # Follow-up proativo (heartbeat)
    HEARTBEAT_ENABLED: bool = True
    HEARTBEAT_INTERVAL_SECONDS: int = 1800      # 30 min — varre bindings e retry queue
    FOLLOWUP_IDLE_MIN_HOURS: int = 48           # so reaborda apos 48h de silencio
    FOLLOWUP_IDLE_MAX_HOURS: int = 144          # nao reaborda se ja passou 6 dias (perto do TTL)

    # Limite de contrapropostas que o devedor pode fazer antes de escalar.
    # 1 = aceita a primeira contraproposta como rodada exploratoria, mas
    # se ele voltar com outra mudanca, escala para humano (sem ficar em
    # loop infinito de negociacao).
    MAX_DEBTOR_COUNTERS: int = 1

    # Robustez do ACCEPT: retry inline + fila Redis para confirmar depois.
    ACCEPT_INLINE_RETRY_ATTEMPTS: int = 2       # tentativas alem da 1a
    ACCEPT_INLINE_RETRY_BASE_MS: int = 250      # backoff base (250ms, 1s, 4s)

    # Telemetria
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
