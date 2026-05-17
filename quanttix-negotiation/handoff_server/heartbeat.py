"""
heartbeat — loop background do handoff_server.

A cada `HEARTBEAT_INTERVAL_SECONDS` (default 30 min):

  1. Drena fila de retry de boleto:
     - chama BackendClient.emit_boleto novamente para cada item
     - sucesso: envia linha digitavel ao contraparte via Telegram + mark_retry_success
     - falha: mark_retry_failed (item volta ao fim da fila, attempts++)

  2. Follow-up proativo:
     - varre bindings em estado idle entre FOLLOWUP_IDLE_MIN_HOURS e _MAX_HOURS
     - so envia se estiver em janela CDC (08:00-22:00 BRT, dias uteis)
     - so envia se nao mandou follow-up nas ultimas 24h para esse chat
     - registra mensagem_agente no backend como contraproposta (mantem timeline)

Integrado ao FastAPI lifespan: start no startup, stop graceful no shutdown.
"""

import asyncio
import logging
from typing import Optional

from handoff_server.backend_client import BackendClient, BackendClientError
from handoff_server.config import settings
from handoff_server.operations import (
    BoletoRetryItem,
    ConfirmationRetryItem,
    followup_recently_sent,
    incr_metric,
    is_business_hours_brt,
    mark_confirmation_failed,
    mark_confirmation_success,
    mark_followup_sent,
    mark_retry_failed,
    mark_retry_success,
    pop_pending_confirmations,
    pop_pending_retries,
    scan_stale_bindings,
)
from handoff_server.session_binding import release_binding
from handoff_server.telegram_sender import TelegramSender

logger = logging.getLogger(__name__)


_task: Optional[asyncio.Task] = None
_stop: Optional[asyncio.Event] = None


# ── Lifecycle ────────────────────────────────────────────────────────────


def start() -> None:
    """Inicia o loop do heartbeat. Idempotente."""
    global _task, _stop
    if not settings.HEARTBEAT_ENABLED:
        logger.info("[heartbeat] HEARTBEAT_ENABLED=False — nao iniciando")
        return
    if _task and not _task.done():
        return
    _stop = asyncio.Event()
    _task = asyncio.create_task(_loop(), name="handoff-heartbeat")
    logger.info(
        "[heartbeat] iniciado (interval=%ds, idle_min=%dh, idle_max=%dh)",
        settings.HEARTBEAT_INTERVAL_SECONDS,
        settings.FOLLOWUP_IDLE_MIN_HOURS,
        settings.FOLLOWUP_IDLE_MAX_HOURS,
    )


async def stop() -> None:
    """Para o loop gracefully (max 5s)."""
    global _task, _stop
    if _stop:
        _stop.set()
    if _task:
        try:
            await asyncio.wait_for(_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _task.cancel()
        _task = None
    logger.info("[heartbeat] parado")


# ── Loop principal ───────────────────────────────────────────────────────


async def _loop() -> None:
    assert _stop is not None
    interval = settings.HEARTBEAT_INTERVAL_SECONDS

    # Primeiro tick: depois de um intervalo curto pra nao competir com startup
    try:
        await asyncio.wait_for(_stop.wait(), timeout=60.0)
        return
    except asyncio.TimeoutError:
        pass

    while not _stop.is_set():
        try:
            await _tick()
        except Exception:
            logger.exception("[heartbeat] erro no tick")

        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def _tick() -> None:
    """Uma rodada do heartbeat — retry de confirmacao + retry boleto + follow-up."""
    # IO sincrono delegado a thread, para nao travar o loop asyncio.
    # Ordem importa: confirmacoes pendentes antes (cliente esta esperando),
    # retry de boleto depois (assincrono), follow-up por ultimo.
    await asyncio.to_thread(_process_confirmation_queue)
    await asyncio.to_thread(_process_retry_queue)
    await asyncio.to_thread(_send_followups)


# ── 0. Retry de confirmacao ACCEPT ───────────────────────────────────────


def _process_confirmation_queue() -> None:
    """
    Drena ACCEPTs pendentes (que falharam inline no webhook). Para cada,
    chama accept_and_issue de novo (idempotente). Em sucesso, envia
    confirmacao definitiva ao cliente. Em falha persistente (5 tentativas),
    notifica cliente e libera o binding.
    """
    items = pop_pending_confirmations(limit=10)
    if not items:
        return
    logger.info("[heartbeat] processando %d ACCEPT pendente(s)", len(items))

    client = BackendClient()
    sender = TelegramSender()
    try:
        for item in items:
            _confirm_one(client, sender, item)
    finally:
        client.close()
        sender.close()


def _confirm_one(
    client: BackendClient, sender: TelegramSender, item: ConfirmationRetryItem,
) -> None:
    if item.attempts >= 5:
        logger.error(
            "[heartbeat] desistindo de confirmar accept neg=%s apos %d tentativas",
            item.negociacao_id, item.attempts,
        )
        mark_confirmation_success(item.negociacao_id)   # remove sentinel
        if item.chat_id:
            try:
                sender.send_message(
                    item.chat_id,
                    f"Olá {item.counterpart_nome}, tivemos uma instabilidade ao "
                    f"finalizar o título {item.title_id}. Nosso financeiro ja "
                    f"foi notificado e entrará em contato manualmente. "
                    f"Pedimos desculpas pelo inconveniente.",
                )
            except Exception:
                pass
            try:
                release_binding(item.chat_id)
            except Exception:
                pass
        return

    try:
        bundle = client.accept_and_issue(
            negociacao_id=item.negociacao_id,
            idempotency_key=item.idempotency_key,
        )
    except BackendClientError as exc:
        logger.info(
            "[heartbeat] confirm accept neg=%s attempt=%d ainda falha: %s",
            item.negociacao_id, item.attempts + 1, exc,
        )
        mark_confirmation_failed(item, error=str(exc))
        return

    # Sucesso — envia confirmacao definitiva (com boleto se AR)
    evento = bundle.get("evento", {})
    boleto = bundle.get("boleto")
    boleto_error = bundle.get("boleto_error", "")

    msg = _format_acceptance_message_for_retry(
        nome=item.counterpart_nome, title_id=item.title_id,
        tipo_titulo=item.tipo_titulo, boleto=boleto,
    )
    try:
        sender.send_message(item.chat_id, msg)
    except Exception as exc:
        logger.warning("[heartbeat] envio pos-retry accept falhou: %s", exc)

    mark_confirmation_success(item.negociacao_id)
    incr_metric("accept_count")
    if boleto:
        incr_metric("boleto_emitted_count")
    elif boleto_error and item.title_uuid:
        # Boleto ainda nao saiu — enfileira no retry de boleto separado
        from handoff_server.operations import enqueue_boleto_retry
        enqueue_boleto_retry(
            negociacao_id=item.negociacao_id, title_uuid=item.title_uuid,
            chat_id=item.chat_id, counterpart_nome=item.counterpart_nome,
            title_id=item.title_id, error=boleto_error,
        )

    try:
        release_binding(item.chat_id)
    except Exception:
        pass
    logger.info(
        "[heartbeat] confirm accept neg=%s SUCESSO apos %d tentativas evento=%s",
        item.negociacao_id, item.attempts + 1, evento.get("evento_id", ""),
    )


def _format_acceptance_message_for_retry(
    *, nome: str, title_id: str, tipo_titulo: str, boleto: Optional[dict],
) -> str:
    """Igual a webhook, mas sem dependencia circular do modulo."""
    if boleto:
        amount = boleto.get("amount", "")
        due_date = boleto.get("due_date", "")
        nosso_numero = boleto.get("nosso_numero", "")
        linha = boleto.get("linha_digitavel", "")
        return (
            f"Confirmado, {nome}! Aqui esta o boleto do titulo {title_id}:\n\n"
            f"- Nosso Numero: {nosso_numero}\n"
            f"- Valor: R$ {amount}\n"
            f"- Vencimento: {due_date}\n\n"
            f"Linha digitavel:\n{linha}\n\n"
            f"Obrigado pela paciencia e pela negociacao!"
        )
    if tipo_titulo == "AP":
        return (
            f"Confirmado, {nome}! Acordo registrado para o titulo {title_id}. "
            f"Nosso financeiro vai entrar em contato com os dados de pagamento."
        )
    return (
        f"Confirmado, {nome}! Acordo registrado para o titulo {title_id}. "
        f"O boleto sera enviado em instantes. Obrigado pela paciencia!"
    )


# ── 1. Retry queue de boleto ─────────────────────────────────────────────


def _process_retry_queue() -> None:
    items = pop_pending_retries(limit=20)
    if not items:
        return
    logger.info("[heartbeat] processando %d boleto retry(s)", len(items))

    client = BackendClient()
    sender = TelegramSender()
    try:
        for item in items:
            _retry_one(client, sender, item)
    finally:
        client.close()
        sender.close()


def _retry_one(
    client: BackendClient, sender: TelegramSender, item: BoletoRetryItem,
) -> None:
    if item.attempts >= 5:
        # 5 tentativas — escala para humano e drop
        logger.warning(
            "[heartbeat] desistindo de retry boleto neg=%s apos %d tentativas",
            item.negociacao_id, item.attempts,
        )
        # Marca como sucesso para tirar da fila (sentinel sai), mas registra metric
        mark_retry_success(item.negociacao_id)
        incr_metric("boleto_failed_count")  # contagem como falha final
        if item.chat_id:
            try:
                sender.send_message(
                    item.chat_id,
                    f"Olá {item.counterpart_nome}, houve um atraso na geração "
                    f"do boleto do título {item.title_id}. Nosso financeiro "
                    f"vai entrar em contato para concluir manualmente.",
                )
            except Exception:
                pass
        return

    try:
        boleto = client.emit_boleto(title_uuid=item.title_uuid)
    except BackendClientError as exc:
        logger.info(
            "[heartbeat] retry boleto neg=%s attempt=%d ainda falha: %s",
            item.negociacao_id, item.attempts + 1, exc,
        )
        mark_retry_failed(item, error=str(exc))
        return

    # Sucesso: envia linha digitavel
    nosso_numero = boleto.get("nosso_numero", "")
    linha_digitavel = boleto.get("linha_digitavel", "")
    amount = boleto.get("amount", "")
    due_date = boleto.get("due_date", "")
    msg = (
        f"Olá {item.counterpart_nome}, boleto pronto para o título "
        f"{item.title_id}:\n\n"
        f"- Nosso Número: {nosso_numero}\n"
        f"- Valor: R$ {amount}\n"
        f"- Vencimento: {due_date}\n\n"
        f"Linha digitável:\n{linha_digitavel}\n\n"
        f"Agradecemos!"
    )
    try:
        sender.send_message(item.chat_id, msg)
    except Exception as exc:
        logger.warning("[heartbeat] envio do boleto pos-retry falhou: %s", exc)
    mark_retry_success(item.negociacao_id)
    incr_metric("boleto_emitted_count")
    logger.info(
        "[heartbeat] retry boleto neg=%s SUCESSO apos %d tentativas",
        item.negociacao_id, item.attempts + 1,
    )


# ── 2. Follow-up proativo ────────────────────────────────────────────────


def _send_followups() -> None:
    """Reaborda bindings com silencio prolongado (so em janela CDC)."""
    if not is_business_hours_brt():
        logger.debug("[heartbeat] fora da janela CDC — pulando follow-ups")
        return

    min_idle = settings.FOLLOWUP_IDLE_MIN_HOURS * 3600
    max_idle = settings.FOLLOWUP_IDLE_MAX_HOURS * 3600

    sender = TelegramSender()
    client = BackendClient()
    try:
        for binding in scan_stale_bindings(min_idle, max_idle):
            chat_id = binding.get("chat_id", "")
            if not chat_id:
                continue
            if followup_recently_sent(chat_id):
                continue
            _send_one_followup(sender, client, binding)
    finally:
        sender.close()
        client.close()


def _send_one_followup(
    sender: TelegramSender, client: BackendClient, binding: dict,
) -> None:
    chat_id = binding["chat_id"]
    nome = binding.get("counterpart_nome", "")
    title_id = binding.get("title_id", "")
    neg_id = binding.get("negociacao_id", "")
    idle_h = int(binding.get("idle_seconds", 0) // 3600)

    msg = (
        f"Olá {nome}, passando aqui de novo sobre o título {title_id}.\n\n"
        f"Continuamos à disposição para combinar uma forma de regularizar. "
        f"Se preferir conversar mais tarde ou tiver uma proposta diferente, "
        f"é só responder por aqui."
    )

    result = sender.send_message(chat_id, msg)
    if not result.ok:
        logger.warning(
            "[heartbeat] follow-up Telegram falhou chat=%s: %s",
            chat_id, result.error_description,
        )
        return

    mark_followup_sent(chat_id)
    logger.info(
        "[heartbeat] follow-up enviado chat=%s neg=%s idle=%dh",
        chat_id, neg_id, idle_h,
    )

    # Registra rastro no backend como contraproposta (texto livre).
    # Idempotency-key baseada em data + neg_id evita duplicar se o
    # heartbeat rodar 2x no mesmo dia por algum bug.
    from datetime import date as _date
    idem = f"followup:{neg_id}:{_date.today().isoformat()}"
    try:
        client.register_counterproposal(
            negociacao_id=neg_id,
            mensagem_agente=f"[follow-up automatico] reabordagem apos {idle_h}h de silencio",
            idempotency_key=idem,
        )
    except BackendClientError as exc:
        logger.warning("[heartbeat] register follow-up no backend falhou: %s", exc)
