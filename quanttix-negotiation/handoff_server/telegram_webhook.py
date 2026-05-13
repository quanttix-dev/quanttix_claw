"""
telegram_webhook — POST /telegram/webhook.

Recebe atualizacoes do Telegram (Bot API webhook). Classifica resposta
do contraparte por keyword e grava evento correspondente no backend.

v1 (deliberadamente simples):
  - SIM / ACEITO / FECHADO / OK    -> accept_negotiation
  - NAO / RECUSO / RECUSADO        -> reject_negotiation
  - resto                          -> register_counterproposal (texto livre
                                       vai no `mensagem_agente`; sem termos
                                       numericos extraidos — gestor decide)

v2: substituir classifier por LLM (Gemma via quanttix-planner OpenClaw).
"""

import logging
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from handoff_server.backend_client import BackendClient, BackendClientError
from handoff_server.config import settings
from handoff_server.session_binding import (
    get_binding_by_chat,
    release_binding,
    release_lock,
    touch_binding,
    try_acquire_lock,
)
from handoff_server.telegram_sender import TelegramSender

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Classifier ────────────────────────────────────────────────────


_ACCEPT_KEYWORDS = {
    "sim", "aceito", "aceitamos", "fechado", "fechou", "ok", "okay",
    "concordo", "tudo bem", "tudo ok", "pode fechar", "feito", "acordo",
}
_REJECT_KEYWORDS = {
    "nao", "não", "recuso", "recusado", "negado", "nao quero", "não quero",
    "nao vai dar", "não vai dar", "sem interesse",
}


def classify_reply(text: str) -> str:
    """Retorna 'ACCEPT' | 'REJECT' | 'COUNTER'."""
    normalized = (text or "").strip().lower()
    if not normalized:
        return "COUNTER"
    # Match exato em palavras curtas (sim/nao) vs subset em frases
    if normalized in _ACCEPT_KEYWORDS:
        return "ACCEPT"
    if normalized in _REJECT_KEYWORDS:
        return "REJECT"
    for kw in _ACCEPT_KEYWORDS:
        if kw in normalized:
            return "ACCEPT"
    for kw in _REJECT_KEYWORDS:
        if kw in normalized:
            return "REJECT"
    return "COUNTER"


# ── Schemas ──────────────────────────────────────────────────────


class TelegramUpdate(BaseModel):
    """Subset do Update do Telegram Bot API que nos interessa."""

    update_id: int
    message: Optional[dict] = None  # estrutura completa do Telegram


# ── Endpoint ─────────────────────────────────────────────────────


@router.post(
    "/telegram/webhook",
    summary="Webhook do Telegram Bot — recebe respostas do contraparte",
)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(
        None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
):
    # Valida secret se configurado (recomendado pra producao)
    expected_secret = settings.TELEGRAM_WEBHOOK_SECRET
    if expected_secret:
        if x_telegram_bot_api_secret_token != expected_secret:
            raise HTTPException(401, "secret invalido")

    raw = await request.json()
    update = TelegramUpdate.model_validate(raw)

    message = update.message or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    text = (message.get("text") or "").strip()

    if not chat_id or not text:
        # Ignora updates sem texto (foto, audio, etc.) na v1
        return {"ok": True, "ignored": True, "reason": "sem chat_id/text"}

    binding = get_binding_by_chat(chat_id)
    if not binding:
        logger.info(f"[webhook] chat_id={chat_id} sem binding — ignorando")
        return {"ok": True, "ignored": True, "reason": "sem binding"}

    # Lock para evitar processar 2 msgs do mesmo chat em paralelo
    if not try_acquire_lock(chat_id):
        return {"ok": True, "ignored": True, "reason": "lock ocupado"}

    try:
        touch_binding(chat_id)
        return _process_reply(binding, chat_id, text)
    finally:
        release_lock(chat_id)


def _process_reply(binding: dict, chat_id: str, text: str) -> dict:
    """Classifica reply e grava evento no backend. Retorna json para o Telegram."""
    outcome = classify_reply(text)
    neg_id = binding["negociacao_id"]
    nome = binding.get("counterpart_nome", "")
    idem = f"webhook:{neg_id}:{int(time.time())}"

    client = BackendClient()
    sender = TelegramSender()
    try:
        if outcome == "ACCEPT":
            evento = client.accept_negotiation(
                negociacao_id=neg_id, idempotency_key=idem,
            )
            sender.send_message(
                chat_id,
                f"Perfeito, {nome}! Acordo registrado. "
                f"Em instantes o boleto/comprovante chega para você. 🤝",
            )
            release_binding(chat_id)
            return {"ok": True, "outcome": "ACCEPT", "evento_id": evento["evento_id"]}

        if outcome == "REJECT":
            evento = client.reject_negotiation(
                negociacao_id=neg_id,
                reason="contraparte_desinteressada",
                mensagem_agente=text[:400],
                idempotency_key=idem,
            )
            sender.send_message(
                chat_id,
                f"Tudo bem, {nome}. Agradecemos o retorno. "
                f"Estamos a disposição para futuras oportunidades.",
            )
            release_binding(chat_id)
            return {"ok": True, "outcome": "REJECT", "evento_id": evento["evento_id"]}

        # COUNTER — registra contraproposta sem extrair termos (v1)
        evento = client.register_counterproposal(
            negociacao_id=neg_id,
            mensagem_agente=text[:400],
            idempotency_key=idem,
        )
        sender.send_message(
            chat_id,
            f"Entendido, {nome}. Vou levar internamente sua contraproposta "
            f"e retorno em breve.",
        )
        return {"ok": True, "outcome": "COUNTER", "evento_id": evento["evento_id"]}

    except BackendClientError as exc:
        logger.error(f"[webhook] backend falhou: {exc}")
        return {"ok": False, "outcome": outcome, "error": str(exc)}
    finally:
        client.close()
        sender.close()
