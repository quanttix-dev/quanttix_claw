"""
handoff_endpoint — POST /negotiation/start.

Recebe pedido do `quanttix_ai` (loopback bearer token), abre a negociacao
no backend, envia a 1a mensagem ao contraparte via Telegram e cria
binding chat_id <-> negociacao_id no Redis.

Fluxo:
  1. valida bearer token
  2. POST backend /negotiation/propose (cria Negotiation + evento PROPOSTA)
  3. cria binding Redis
  4. envia 1a mensagem Telegram
  5. retorna {ok, negociacao_id, evento_id}
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from handoff_server.backend_client import BackendClient, BackendClientError
from handoff_server.config import settings
from handoff_server.session_binding import create_binding
from handoff_server.telegram_sender import TelegramSender

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Schemas ────────────────────────────────────────────────────────


class HandoffInstructions(BaseModel):
    """Sugestoes do orquestrador (quanttix_ai) para a 1a mensagem."""

    tone: Literal["cordial", "direto"] = "cordial"
    max_desconto_pct_authorized: Optional[Decimal] = None
    expiracao_em_horas: int = 48
    mensagem_inicial_sugerida: str = ""


class HandoffRequest(BaseModel):
    """Body do POST /negotiation/start."""

    title_id: str
    tipo_titulo: Literal["AR", "AP"]
    valor_titulo: Decimal = Field(ge=Decimal("0"))
    counterpart_doc: str
    counterpart_nome: str
    channel: Literal["telegram"] = "telegram"
    chat_id: str
    instructions: HandoffInstructions = Field(default_factory=HandoffInstructions)
    # Sugestao de desconto do quanttix_ai (Chat LLM ja analisou)
    suggested_discount_pct: Optional[Decimal] = None


class HandoffResponse(BaseModel):
    ok: bool
    negociacao_id: str
    evento_id: str
    message_sent: bool
    telegram_message_id: Optional[int] = None
    error: str = ""


# ── Helpers ────────────────────────────────────────────────────────


def _validate_bearer(authorization: Optional[str]) -> None:
    expected = settings.QUANTTIX_HANDOFF_TOKEN
    if not expected or expected == "REPLACE_ME_AT_POD_ENV":
        raise HTTPException(
            status_code=500,
            detail="QUANTTIX_HANDOFF_TOKEN nao configurado no handoff_server",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization Bearer ausente")
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="bearer token invalido")


def _format_initial_message(req: HandoffRequest) -> str:
    """Texto enviado ao contraparte na 1a mensagem."""
    if req.instructions.mensagem_inicial_sugerida:
        return req.instructions.mensagem_inicial_sugerida

    valor_fmt = _fmt_brl(req.valor_titulo)
    if req.tipo_titulo == "AR":
        opener = (
            f"Olá, {req.counterpart_nome}.\n\n"
            f"Estou em contato em nome da Quanttix sobre o título {req.title_id}, "
            f"no valor de {valor_fmt}."
        )
        if req.suggested_discount_pct:
            valor_com_desconto = req.valor_titulo * (
                Decimal("1") - req.suggested_discount_pct / Decimal("100")
            )
            opener += (
                f"\n\nGostaríamos de oferecer condições especiais para "
                f"regularização: desconto de "
                f"{req.suggested_discount_pct}% por pagamento imediato, "
                f"deixando o valor em {_fmt_brl(valor_com_desconto)}."
            )
        opener += "\n\nVocê tem interesse em conversar sobre essa condição?"
        return opener

    # AP
    opener = (
        f"Olá, time financeiro da {req.counterpart_nome}.\n\n"
        f"Aqui é da Quanttix tratando do título {req.title_id} "
        f"({valor_fmt})."
    )
    if req.suggested_discount_pct:
        opener += (
            f"\n\nGostaríamos de propor antecipação do pagamento com "
            f"desconto de {req.suggested_discount_pct}%."
        )
    opener += "\n\nPodemos conversar sobre essa condição?"
    return opener


def _fmt_brl(value: Decimal) -> str:
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return f"R$ {value}"


# ── Endpoint ───────────────────────────────────────────────────────


@router.post(
    "/negotiation/start",
    response_model=HandoffResponse,
    summary="Inicia negociacao a partir do quanttix_ai (loopback)",
)
def start_negotiation(
    req: HandoffRequest,
    authorization: Optional[str] = Header(None),
):
    _validate_bearer(authorization)
    logger.info(
        "[handoff] start title=%s tipo=%s counterpart=%s chat=%s",
        req.title_id, req.tipo_titulo, req.counterpart_doc, req.chat_id,
    )

    client = BackendClient()
    sender = TelegramSender()

    try:
        # 1. Cria negociacao no backend (PROPOSTA inicial)
        idem = f"handoff:{req.title_id}:{req.chat_id}"
        try:
            evento = client.propose_negotiation(
                title_id=req.title_id,
                counterpart_doc=req.counterpart_doc,
                tipo_titulo=req.tipo_titulo,
                valor_titulo=req.valor_titulo,
                desconto_pct=req.suggested_discount_pct,
                mensagem_agente="proposta inicial — handoff do quanttix_ai",
                idempotency_key=idem,
            )
        except BackendClientError as exc:
            logger.error(f"[handoff] propose backend falhou: {exc}")
            return HandoffResponse(
                ok=False, negociacao_id="", evento_id="", message_sent=False,
                error=f"backend propose falhou: {exc}",
            )

        negociacao_id = evento["negociacao_id"]
        evento_id = evento["evento_id"]

        # 2. Cria binding Redis (chat_id ↔ negociacao_id)
        binding_ok = create_binding(
            chat_id=req.chat_id,
            negociacao_id=negociacao_id,
            counterpart_doc=req.counterpart_doc,
            counterpart_nome=req.counterpart_nome,
            tipo_titulo=req.tipo_titulo,
            title_id=req.title_id,
        )
        if not binding_ok:
            logger.warning("[handoff] binding Redis falhou — segue sem binding")

        # 3. Envia 1a mensagem Telegram
        text = _format_initial_message(req)
        tg_result = sender.send_message(chat_id=req.chat_id, text=text)

        return HandoffResponse(
            ok=True,
            negociacao_id=negociacao_id,
            evento_id=evento_id,
            message_sent=tg_result.ok,
            telegram_message_id=tg_result.message_id,
            error=tg_result.error_description if not tg_result.ok else "",
        )
    finally:
        client.close()
        sender.close()
