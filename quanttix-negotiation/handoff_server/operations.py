"""
operations — utilitarios operacionais do handoff_server:

  - metricas (Redis INCR + GET para endpoint /metrics)
  - fila de retry de boleto (quando accept_and_issue falha so no boleto)
  - varredura de bindings idle (para follow-up proativo)
  - janela CDC (so abordar entre 08:00-22:00, dias uteis no fuso BRT)

Tudo orientado a Redis com TTLs sensatos. Sem Redis, funcoes degradam
para no-op (sem mascarar logs).
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Iterable, Optional

import redis

from handoff_server.config import settings

logger = logging.getLogger(__name__)


# ── Conexao Redis (singleton local) ──────────────────────────────────────


def _client() -> Optional[redis.Redis]:
    if not hasattr(_client, "_cached"):
        try:
            r = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD or None,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            r.ping()
            _client._cached = r  # type: ignore
        except Exception as exc:
            logger.warning("operations: Redis indisponivel: %s", exc)
            _client._cached = None  # type: ignore
    return _client._cached  # type: ignore


# ── Metricas ─────────────────────────────────────────────────────────────


# Contadores cumulativos. Reset apenas se a chave for apagada (manualmente
# ou via DEL). Para counters por janela (hora/dia), use bucket prefixado.
_METRIC_KEYS = (
    "accept_count",
    "reject_count",
    "counter_count",
    "escalated_count",
    "boleto_emitted_count",
    "boleto_failed_count",
    "boleto_retried_count",
    "boleto_retry_success_count",
    "followup_sent_count",
    "webhook_duplicate_count",
    "webhook_no_binding_count",
    "accept_inline_retry_count",
    "accept_queued_for_retry_count",
)


def _metric_key(name: str) -> str:
    return f"metrics:negot:{name}"


def incr_metric(name: str, by: int = 1) -> None:
    """Incrementa um counter (idempotente em Redis, INCR atomico)."""
    client = _client()
    if client is None:
        return
    try:
        client.incrby(_metric_key(name), by)
    except Exception as exc:
        logger.debug("incr_metric falhou: %s", exc)


def get_metrics() -> dict[str, int]:
    """Snapshot dos contadores conhecidos."""
    client = _client()
    if client is None:
        return {name: 0 for name in _METRIC_KEYS}
    out: dict[str, int] = {}
    for name in _METRIC_KEYS:
        try:
            raw = client.get(_metric_key(name))
            out[name] = int(raw) if raw else 0
        except Exception:
            out[name] = 0
    return out


# ── Fila de retry de boleto ──────────────────────────────────────────────


_RETRY_QUEUE_KEY = "boleto:retry:queue"   # Redis LIST
_RETRY_TTL = 7 * 24 * 60 * 60             # 7 dias


@dataclass
class BoletoRetryItem:
    negociacao_id: str
    title_uuid: str
    chat_id: str
    counterpart_nome: str
    title_id: str
    enqueued_at: str         # ISO
    attempts: int = 0
    last_attempt_at: str = ""
    last_error: str = ""


def enqueue_boleto_retry(
    *,
    negociacao_id: str,
    title_uuid: str,
    chat_id: str,
    counterpart_nome: str,
    title_id: str,
    error: str = "",
) -> bool:
    """
    Marca um boleto para retry. So enfileira se houver title_uuid
    (sem ele, retry nao tem alvo). Idempotente — uma mesma negociacao
    nao gera duas entries (verifica via SET).
    """
    if not title_uuid:
        logger.info(
            "enqueue_boleto_retry pulado: negociacao=%s sem title_uuid",
            negociacao_id,
        )
        return False

    client = _client()
    if client is None:
        return False

    sentinel_key = f"boleto:retry:has:{negociacao_id}"
    try:
        if not client.set(sentinel_key, "1", nx=True, ex=_RETRY_TTL):
            return False  # ja enfileirado
        item = BoletoRetryItem(
            negociacao_id=negociacao_id,
            title_uuid=title_uuid,
            chat_id=chat_id,
            counterpart_nome=counterpart_nome,
            title_id=title_id,
            enqueued_at=_now_iso(),
            last_error=error,
        )
        client.rpush(_RETRY_QUEUE_KEY, json.dumps(asdict(item)))
        client.expire(_RETRY_QUEUE_KEY, _RETRY_TTL)
        incr_metric("boleto_failed_count")
        logger.info(
            "enqueue_boleto_retry: neg=%s title_uuid=%s error=%s",
            negociacao_id, title_uuid, error[:80],
        )
        return True
    except Exception as exc:
        logger.warning("enqueue_boleto_retry falhou: %s", exc)
        return False


def pop_pending_retries(limit: int = 10) -> list[BoletoRetryItem]:
    """
    Drena ate `limit` items da fila. Os items voltam atrasados se nao
    forem confirmados (chamador chama `mark_retry_success` ou
    `mark_retry_failed`).
    """
    client = _client()
    if client is None:
        return []
    out: list[BoletoRetryItem] = []
    for _ in range(limit):
        try:
            raw = client.lpop(_RETRY_QUEUE_KEY)
        except Exception:
            break
        if not raw:
            break
        try:
            data = json.loads(raw)
            out.append(BoletoRetryItem(**data))
        except Exception as exc:
            logger.warning("retry item malformado: %s", exc)
            continue
    return out


def mark_retry_success(neg_id: str) -> None:
    client = _client()
    if client is None:
        return
    try:
        client.delete(f"boleto:retry:has:{neg_id}")
        incr_metric("boleto_retry_success_count")
    except Exception:
        pass


def mark_retry_failed(item: BoletoRetryItem, error: str) -> None:
    """Devolve o item ao fim da fila com attempts incrementado."""
    client = _client()
    if client is None:
        return
    item.attempts += 1
    item.last_attempt_at = _now_iso()
    item.last_error = error
    try:
        client.rpush(_RETRY_QUEUE_KEY, json.dumps(asdict(item)))
        incr_metric("boleto_retried_count")
    except Exception as exc:
        logger.warning("mark_retry_failed falhou: %s", exc)


# ── Fila de retry de confirmacao ACCEPT ─────────────────────────────────


# Quando o backend esta intermitente e nao conseguimos confirmar o ACCEPT
# inline (mesmo apos retries com backoff), enfileiramos a confirmacao
# para que o heartbeat tente de novo. Cliente recebe mensagem "estamos
# confirmando..." enquanto isso.

_CONFIRMATION_QUEUE_KEY = "accept:retry:queue"
_CONFIRMATION_TTL = 24 * 60 * 60   # 24h — apos isso, gestor humano fecha manualmente


@dataclass
class ConfirmationRetryItem:
    """Pendente de confirmar accept no backend (e emitir boleto se AR)."""
    negociacao_id: str
    chat_id: str
    counterpart_nome: str
    title_id: str
    title_uuid: str            # vazio se AP
    tipo_titulo: str           # "AR" | "AP"
    idempotency_key: str
    enqueued_at: str
    attempts: int = 0
    last_attempt_at: str = ""
    last_error: str = ""


def enqueue_confirmation_retry(
    *,
    negociacao_id: str,
    chat_id: str,
    counterpart_nome: str,
    title_id: str,
    title_uuid: str,
    tipo_titulo: str,
    idempotency_key: str,
    error: str = "",
) -> bool:
    """
    Enfileira ACCEPT que falhou inline. Idempotente — uma negociacao so
    pode estar enfileirada uma vez (sentinel SET NX).
    """
    client = _client()
    if client is None:
        return False
    sentinel = f"accept:retry:has:{negociacao_id}"
    try:
        if not client.set(sentinel, "1", nx=True, ex=_CONFIRMATION_TTL):
            return False
        item = ConfirmationRetryItem(
            negociacao_id=negociacao_id,
            chat_id=chat_id,
            counterpart_nome=counterpart_nome,
            title_id=title_id,
            title_uuid=title_uuid or "",
            tipo_titulo=tipo_titulo,
            idempotency_key=idempotency_key,
            enqueued_at=_now_iso(),
            last_error=error,
        )
        client.rpush(_CONFIRMATION_QUEUE_KEY, json.dumps(asdict(item)))
        client.expire(_CONFIRMATION_QUEUE_KEY, _CONFIRMATION_TTL)
        incr_metric("accept_queued_for_retry_count")
        logger.warning(
            "enqueue_confirmation_retry: neg=%s error=%s",
            negociacao_id, error[:120],
        )
        return True
    except Exception as exc:
        logger.warning("enqueue_confirmation_retry falhou: %s", exc)
        return False


def pop_pending_confirmations(limit: int = 10) -> list[ConfirmationRetryItem]:
    """Drena ate `limit` items da fila de confirmacao."""
    client = _client()
    if client is None:
        return []
    out: list[ConfirmationRetryItem] = []
    for _ in range(limit):
        try:
            raw = client.lpop(_CONFIRMATION_QUEUE_KEY)
        except Exception:
            break
        if not raw:
            break
        try:
            data = json.loads(raw)
            out.append(ConfirmationRetryItem(**data))
        except Exception as exc:
            logger.warning("confirmation item malformado: %s", exc)
            continue
    return out


def mark_confirmation_success(neg_id: str) -> None:
    client = _client()
    if client is None:
        return
    try:
        client.delete(f"accept:retry:has:{neg_id}")
    except Exception:
        pass


def mark_confirmation_failed(item: ConfirmationRetryItem, error: str) -> None:
    client = _client()
    if client is None:
        return
    item.attempts += 1
    item.last_attempt_at = _now_iso()
    item.last_error = error
    try:
        client.rpush(_CONFIRMATION_QUEUE_KEY, json.dumps(asdict(item)))
    except Exception as exc:
        logger.warning("mark_confirmation_failed falhou: %s", exc)


# ── Bindings idle (para follow-up proativo) ──────────────────────────────


def scan_stale_bindings(
    min_idle_seconds: int,
    max_idle_seconds: Optional[int] = None,
) -> Iterable[dict]:
    """
    Yield bindings com `last_touch_at` entre [now - max_idle, now - min_idle].

    Implementacao usa SCAN (nao KEYS — seguro em prod). Filtra por
    `last_touch_at` no payload. Items com last_touch_at fora do range
    sao pulados.
    """
    client = _client()
    if client is None:
        return
    now = datetime.now(timezone.utc)
    try:
        cursor = 0
        while True:
            cursor, batch = client.scan(
                cursor=cursor, match="chat:tg:*", count=100,
            )
            for key in batch:
                try:
                    raw = client.get(key)
                except Exception:
                    continue
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                last_touch = payload.get("last_touch_at", "")
                if not last_touch:
                    continue
                try:
                    lt = datetime.fromisoformat(last_touch.replace("Z", "+00:00"))
                    if lt.tzinfo is None:
                        lt = lt.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                idle = (now - lt).total_seconds()
                if idle < min_idle_seconds:
                    continue
                if max_idle_seconds is not None and idle > max_idle_seconds:
                    continue
                yield {**payload, "chat_id": key.removeprefix("chat:tg:"), "idle_seconds": idle}
            if cursor == 0:
                break
    except Exception as exc:
        logger.warning("scan_stale_bindings falhou: %s", exc)


def mark_followup_sent(chat_id: str) -> None:
    """
    Marca timestamp do ultimo follow-up enviado para esse chat, para
    evitar mandar 2 lembretes no mesmo dia. TTL 24h.
    """
    client = _client()
    if client is None:
        return
    try:
        client.set(f"followup:last:{chat_id}", _now_iso(), ex=24 * 60 * 60)
        incr_metric("followup_sent_count")
    except Exception:
        pass


def followup_recently_sent(chat_id: str) -> bool:
    client = _client()
    if client is None:
        return False
    try:
        return bool(client.get(f"followup:last:{chat_id}"))
    except Exception:
        return False


# ── Janela CDC ───────────────────────────────────────────────────────────


# Brasilia (sem DST desde 2019)
_BRT_OFFSET = timezone(timedelta(hours=-3))
_BUSINESS_START = dtime(8, 0)
_BUSINESS_END = dtime(22, 0)


def is_business_hours_brt(now: Optional[datetime] = None) -> bool:
    """
    True se agora esta entre 08:00 e 22:00, segunda-sexta, no fuso BRT.
    Cobranca automatizada respeita esta janela (CDC art. 42; Lei 14.181).

    Nao trata feriados — implementacao futura pode integrar com calendario
    bancario. Para v1 a janela seg-sex ja cobre a maioria dos casos.
    """
    if now is None:
        now = datetime.now(_BRT_OFFSET)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_BRT_OFFSET)
    else:
        now = now.astimezone(_BRT_OFFSET)

    if now.weekday() >= 5:  # 5=sat, 6=sun
        return False
    return _BUSINESS_START <= now.time() < _BUSINESS_END


# ── Helpers ──────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
