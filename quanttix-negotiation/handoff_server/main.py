"""
main — entrypoint do handoff_server (FastAPI + uvicorn).

Roda na porta loopback `HANDOFF_PORT` (default 18790), nao conflita com
gateway OpenClaw (porta 18789).

Start:
  uvicorn handoff_server.main:app --host 127.0.0.1 --port 18790
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from handoff_server import heartbeat
from handoff_server.config import settings
from handoff_server.handoff_endpoint import router as handoff_router
from handoff_server.operations import get_metrics
from handoff_server.telegram_webhook import router as telegram_router

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    heartbeat.start()
    yield
    await heartbeat.stop()


app = FastAPI(
    title="Quanttix Handoff Server",
    description=(
        "Recebe handoffs do quanttix_ai, conduz Telegram com a contraparte, "
        "classifica respostas via LLM (Qwen3-32B) e mantem heartbeat com "
        "retry de boleto e follow-up proativo em janela CDC."
    ),
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {
        "ok": True,
        "port": settings.HANDOFF_PORT,
        "host": settings.HANDOFF_HOST,
    }


@app.get("/metrics")
def metrics():
    """Snapshot de counters operacionais (acumulados)."""
    return {"ok": True, "metrics": get_metrics()}


app.include_router(handoff_router)
app.include_router(telegram_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "handoff_server.main:app",
        host=settings.HANDOFF_HOST,
        port=settings.HANDOFF_PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )
