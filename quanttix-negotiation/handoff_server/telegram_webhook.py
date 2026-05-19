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
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from handoff_server.backend_client import BackendClient, BackendClientError
from handoff_server.boleto_pdf_generator import (
    BoletoFicticioInput,
    gerar_identificadores_cnab,
    gerar_pdf,
)
from handoff_server.config import settings
from handoff_server.llm_classifier import classify_reply_llm
from handoff_server.operations import (
    enqueue_boleto_retry,
    enqueue_confirmation_retry,
    incr_metric,
    next_boleto_simulado_seq,
)
from handoff_server.session_binding import (
    get_binding_by_chat,
    increment_counter_count,
    mark_update_seen,
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


# ── Boleto ficticio (gerado quando backend devolve boleto=None) ────


def _emit_boleto_ficticio(
    *,
    sender: TelegramSender,
    binding: dict,
    chat_id: str,
    neg_id: str,
) -> Optional[dict]:
    """Gera e envia o boleto ficticio do handoff_server via Telegram.

    Retorna um dict no mesmo formato que o backend devolveria quando
    emite um boleto real (compativel com _format_acceptance_message),
    ou None se nao foi possivel gerar (valores ausentes no binding ou
    falha no envio Telegram).
    """
    # 1. Recupera valores do binding (persistidos no handoff inicial).
    valor_titulo_raw = binding.get("valor_titulo") or ""
    desconto_pct_raw = binding.get("desconto_pct") or ""
    try:
        valor_nominal = Decimal(valor_titulo_raw)
        desconto_pct = Decimal(desconto_pct_raw)
    except (InvalidOperation, ValueError):
        logger.warning(
            "[ficticio] binding sem valor/desconto neg=%s — pulando ficticio",
            neg_id,
        )
        return None

    valor_final = (
        valor_nominal - (valor_nominal * desconto_pct / Decimal("100"))
    ).quantize(Decimal("0.01"))

    # 2. Identificadores CNAB com sequencia atomica Redis.
    seq = next_boleto_simulado_seq()
    dt_emissao = date.today()
    dt_vencimento = dt_emissao + timedelta(days=15)
    nosso_numero, linha_digitavel, codigo_barras = gerar_identificadores_cnab(
        seq_global=seq,
        valor_final=valor_final,
        dt_vencimento=dt_vencimento,
    )

    # 3. Monta input e gera PDF em memoria.
    data = BoletoFicticioInput(
        title_id_erp=binding.get("title_id", ""),
        valor_nominal=valor_nominal,
        desconto_pct=desconto_pct,
        valor_final=valor_final,
        dt_emissao=dt_emissao,
        dt_vencimento=dt_vencimento,
        counterpart_nome=binding.get("counterpart_nome", ""),
        counterpart_doc=binding.get("counterpart_doc", ""),
        nosso_numero=nosso_numero,
        linha_digitavel=linha_digitavel,
        codigo_barras=codigo_barras,
    )
    try:
        pdf_bytes = gerar_pdf(data)
    except Exception as exc:
        logger.exception(
            "[ficticio] geracao PDF falhou neg=%s: %s", neg_id, exc,
        )
        return None

    # 4. Envia via Telegram (multipart). Caption discreto — o detalhe
    # com linha digitavel vai na mensagem texto subsequente.
    filename = f"boleto_{binding.get('title_id', 'titulo')}.pdf"
    caption = "Boleto da negociação em anexo (ambiente de simulação)."
    result = sender.send_document(
        chat_id, document=pdf_bytes, filename=filename, caption=caption,
    )
    if not result.ok:
        logger.error(
            "[ficticio] send_document falhou neg=%s: %s",
            neg_id, result.error_description,
        )
        return None

    incr_metric("boleto_ficticio_emitted_count")
    logger.info(
        "[ficticio] enviado neg=%s nosso_numero=%s message_id=%s",
        neg_id, nosso_numero, result.message_id,
    )

    # 5. Devolve dict no formato do backend para reusar o formatador.
    return {
        "id": f"sim-{neg_id}",
        "nosso_numero": nosso_numero,
        "linha_digitavel": linha_digitavel,
        "codigo_barras": codigo_barras,
        "amount": str(valor_final),
        "due_date": dt_vencimento.strftime("%d/%m/%Y"),
        "is_simulated": True,
    }


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

    # Dedup contra reentrega do Telegram (mesmo update_id chegando >1x).
    # SETNX no Redis com TTL 24h; em queda do Redis degrada para best-effort.
    if not mark_update_seen(update.update_id):
        logger.info(
            "[webhook] update_id=%s ja processado — ignorando reentrega",
            update.update_id,
        )
        incr_metric("webhook_duplicate_count")
        return {"ok": True, "ignored": True, "reason": "duplicate update_id"}

    message = update.message or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    text = (message.get("text") or "").strip()

    if not chat_id or not text:
        # Ignora updates sem texto (foto, audio, etc.) na v1
        return {"ok": True, "ignored": True, "reason": "sem chat_id/text"}

    binding = get_binding_by_chat(chat_id)
    if not binding:
        logger.info(
            "[webhook] chat_id=%s update_id=%s sem binding — ignorando",
            chat_id, update.update_id,
        )
        incr_metric("webhook_no_binding_count")
        return {"ok": True, "ignored": True, "reason": "sem binding"}

    # Lock para evitar processar 2 msgs do mesmo chat em paralelo
    if not try_acquire_lock(chat_id):
        return {"ok": True, "ignored": True, "reason": "lock ocupado"}

    try:
        touch_binding(chat_id)
        return _process_reply(binding, chat_id, text, update_id=update.update_id)
    finally:
        release_lock(chat_id)


def _process_reply(binding: dict, chat_id: str, text: str, *, update_id: int) -> dict:
    """Classifica reply e grava evento no backend. Retorna json para o Telegram."""
    neg_id = binding["negociacao_id"]
    nome = binding.get("counterpart_nome", "")
    tipo_titulo = binding.get("tipo_titulo", "")
    title_id = binding.get("title_id", "")
    title_uuid = binding.get("title_uuid", "") or ""
    # update_id e estritamente unico por mensagem do Telegram —
    # garante idempotencia real ao reprocessar o mesmo evento.
    idem = f"tg:{update_id}"

    # Classificacao: tenta LLM primeiro (Qwen3-32B em loopback). Em qualquer
    # falha (timeout, LLM off, JSON quebrado), fallback para keyword.
    extracted_terms: dict = {}
    classification = classify_reply_llm(
        text,
        last_proposal_summary=f"titulo {title_id} ({tipo_titulo})",
    )
    if classification:
        outcome = classification.outcome
        extracted_terms = classification.extracted_terms or {}
        logger.info(
            "[webhook] LLM classify neg=%s outcome=%s conf=%.2f terms=%s",
            neg_id, outcome, classification.confidence,
            list(extracted_terms.keys()),
        )
    else:
        outcome = classify_reply(text)
        logger.info(
            "[webhook] keyword classify (LLM indisponivel) neg=%s outcome=%s",
            neg_id, outcome,
        )

    client = BackendClient()
    sender = TelegramSender()
    try:
        if outcome == "ACCEPT":
            # Chamada atomica: o backend faz accept + emit_boleto numa unica
            # transacao (so emite boleto em AR + com title_uuid_ref). Falha
            # do boleto NAO faz rollback do accept — fica como retry.
            #
            # Robustez: tentamos inline com backoff exponencial. Se todas
            # falharem, enfileiramos para o heartbeat tentar de novo e
            # avisamos o cliente que estamos confirmando.
            bundle, accept_error = _accept_and_issue_with_retry(
                client, neg_id=neg_id, idem=idem,
            )
            if bundle is None:
                # Backend persistentemente indisponivel — enfileira e dispara
                # mensagem de "aguarde". Cliente NAO fica em silencio.
                enqueue_confirmation_retry(
                    negociacao_id=neg_id, chat_id=chat_id,
                    counterpart_nome=nome, title_id=title_id,
                    title_uuid=title_uuid, tipo_titulo=tipo_titulo,
                    idempotency_key=idem, error=str(accept_error),
                )
                sender.send_message(
                    chat_id,
                    f"Recebi seu aceite, {nome}! Estou confirmando aqui internamente "
                    f"e em instantes te envio os dados para finalizar. "
                    f"Aguarde so um momento, ja te retorno.",
                )
                # NAO libera binding — heartbeat precisa dele para confirmar depois.
                logger.error(
                    "[webhook] accept enfileirado para retry neg=%s err=%s",
                    neg_id, accept_error,
                )
                return {
                    "ok": True, "outcome": "ACCEPT_QUEUED",
                    "evento_id": "", "boleto_id": "",
                    "boleto_error": str(accept_error),
                }

            evento = bundle.get("evento", {})
            boleto = bundle.get("boleto")
            boleto_error = bundle.get("boleto_error", "")

            if boleto:
                logger.info(
                    "[webhook] accept+boleto neg=%s nosso_numero=%s",
                    neg_id, boleto.get("nosso_numero"),
                )
            else:
                # Backend nao emitiu boleto (Protheus 404 / DataEng off / AP).
                # Em AR: fallback para boleto ficticio do handoff_server
                # ate o DataEng (vendor=AIRFLOW_SIM) entrar em producao.
                if boleto_error:
                    logger.warning(
                        "[webhook] accept ok mas boleto falhou neg=%s: %s",
                        neg_id, boleto_error,
                    )
                    enqueue_boleto_retry(
                        negociacao_id=neg_id, title_uuid=title_uuid,
                        chat_id=chat_id, counterpart_nome=nome,
                        title_id=title_id, error=boleto_error,
                    )
                if tipo_titulo == "AR":
                    boleto = _emit_boleto_ficticio(
                        sender=sender,
                        binding=binding,
                        chat_id=chat_id,
                        neg_id=neg_id,
                    )

            sender.send_message(
                chat_id,
                _format_acceptance_message(
                    nome=nome,
                    title_id=title_id,
                    tipo_titulo=tipo_titulo,
                    boleto=boleto,
                ),
            )
            release_binding(chat_id)
            incr_metric("accept_count")
            if boleto and not boleto.get("is_simulated"):
                incr_metric("boleto_emitted_count")
            return {
                "ok": True,
                "outcome": "ACCEPT",
                "evento_id": evento.get("evento_id", ""),
                "boleto_id": (boleto or {}).get("id", ""),
                "boleto_error": boleto_error,
            }

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
            incr_metric("reject_count")
            return {"ok": True, "outcome": "REJECT", "evento_id": evento["evento_id"]}

        # COUNTER — politica: aceitamos no maximo MAX_DEBTOR_COUNTERS
        # contrapropostas do devedor. Apos isso, escalamos para humano
        # (sem ficar em loop de "vou levar ao financeiro" eterno).
        new_counter_count = increment_counter_count(chat_id)
        if new_counter_count > settings.MAX_DEBTOR_COUNTERS:
            # Excedeu — escala em vez de registrar mais uma rodada
            try:
                evento = client.escalate_negotiation(
                    negociacao_id=neg_id,
                    reason="max_debtor_counters",
                    context={
                        "counter_count": new_counter_count,
                        "max_allowed": settings.MAX_DEBTOR_COUNTERS,
                        "ultima_mensagem": text[:400],
                        "extracted_terms": extracted_terms or {},
                    },
                    mensagem_agente=text[:400],
                    idempotency_key=idem,
                )
            except BackendClientError as exc:
                logger.error("[webhook] escalate falhou neg=%s: %s", neg_id, exc)
                return {"ok": False, "outcome": "ESCALATE", "error": str(exc)}

            sender.send_message(
                chat_id,
                f"Entendido, {nome}. Como sua contraproposta requer uma "
                f"avaliacao especifica, vou encaminhar para o nosso financeiro. "
                f"Voce recebera uma resposta final em breve.",
            )
            release_binding(chat_id)
            incr_metric("escalated_count")
            return {
                "ok": True, "outcome": "ESCALATE",
                "evento_id": evento.get("evento_id", ""),
                "reason": "max_debtor_counters",
            }

        # Dentro do limite — registra contraproposta normal
        evento = client.register_counterproposal(
            negociacao_id=neg_id,
            desconto_pct=extracted_terms.get("desconto_pct") if extracted_terms else None,
            valor_acordado=extracted_terms.get("valor_acordado") if extracted_terms else None,
            num_parcelas=extracted_terms.get("num_parcelas") if extracted_terms else None,
            novo_vencimento=extracted_terms.get("novo_vencimento") if extracted_terms else None,
            mensagem_agente=text[:400],
            idempotency_key=idem,
        )
        sender.send_message(
            chat_id,
            f"Entendido, {nome}. Vou levar internamente sua contraproposta "
            f"e retorno em breve.",
        )
        incr_metric("counter_count")
        return {"ok": True, "outcome": "COUNTER", "evento_id": evento["evento_id"]}

    except BackendClientError as exc:
        logger.error(f"[webhook] backend falhou: {exc}")
        return {"ok": False, "outcome": outcome, "error": str(exc)}
    finally:
        client.close()
        sender.close()


def _accept_and_issue_with_retry(
    client: BackendClient, *, neg_id: str, idem: str,
) -> tuple[Optional[dict], Optional[Exception]]:
    """
    Tenta `accept_and_issue` com backoff exponencial. A chamada e
    idempotente no backend (Idempotency-Key), entao retry e seguro
    mesmo se a chamada anterior tiver passado parcialmente.

    Retorna (bundle, None) em sucesso; (None, exception) apos esgotar
    todas as tentativas.
    """
    attempts = settings.ACCEPT_INLINE_RETRY_ATTEMPTS + 1   # +1 da tentativa inicial
    base_ms = settings.ACCEPT_INLINE_RETRY_BASE_MS
    last_exc: Optional[Exception] = None

    for i in range(attempts):
        try:
            return client.accept_and_issue(
                negociacao_id=neg_id, idempotency_key=idem,
            ), None
        except BackendClientError as exc:
            last_exc = exc
            if i < attempts - 1:
                # Backoff: 250ms, 1s, 4s (base=250)
                sleep_ms = base_ms * (4 ** i)
                logger.warning(
                    "[webhook] accept_and_issue tentativa %d/%d falhou neg=%s "
                    "retry em %dms: %s",
                    i + 1, attempts, neg_id, sleep_ms, exc,
                )
                incr_metric("accept_inline_retry_count")
                time.sleep(sleep_ms / 1000.0)
            else:
                logger.error(
                    "[webhook] accept_and_issue esgotou %d tentativas neg=%s: %s",
                    attempts, neg_id, exc,
                )
    return None, last_exc


def _format_acceptance_message(
    *,
    nome: str,
    title_id: str,
    tipo_titulo: str,
    boleto: Optional[dict],
) -> str:
    """
    Mensagem enviada apos ACCEPT.
      - AR + boleto emitido: detalhes + linha digitavel
      - AR sem boleto (falha) ou AP: confirmacao generica
    """
    if boleto:
        amount = boleto.get("amount", "")
        due_date = boleto.get("due_date", "")
        nosso_numero = boleto.get("nosso_numero", "")
        linha_digitavel = boleto.get("linha_digitavel", "")
        return (
            f"Negociação confirmada, {nome}!\n\n"
            f"Boleto gerado com sucesso:\n"
            f"- Título: {title_id}\n"
            f"- Nosso Número: {nosso_numero}\n"
            f"- Valor: {_fmt_brl(amount)}\n"
            f"- Vencimento: {due_date}\n\n"
            f"Linha digitável:\n{linha_digitavel}\n\n"
            f"Copie a linha digitável acima para efetuar o pagamento. "
            f"Agradecemos pela negociação!"
        )

    if tipo_titulo == "AP":
        return (
            f"Perfeito, {nome}! Acordo registrado para o título {title_id}.\n\n"
            f"Nosso financeiro entrará em contato em breve com os dados "
            f"para o pagamento e os próximos passos."
        )

    # AR sem boleto (title_uuid ausente ou falha de emissao)
    return (
        f"Perfeito, {nome}! Negociação confirmada para o título {title_id}.\n\n"
        f"Estamos gerando o boleto e enviaremos em instantes. "
        f"Caso não receba, entre em contato com nosso financeiro."
    )


def _fmt_brl(value) -> str:
    """Formata Decimal/float/str como R$ no padrao BR (1.234,56)."""
    try:
        f = float(value)
        return (
            f"R$ {f:,.2f}"
            .replace(",", "X").replace(".", ",").replace("X", ".")
        )
    except (ValueError, TypeError):
        return f"R$ {value}"
