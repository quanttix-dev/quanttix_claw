"""
session_binding — chat_id <-> negociacao_id no Redis local.

Mantem 2 chaves espelhadas para lookup bidirecional:
  chat:tg:{chat_id}             → JSON {negociacao_id, counterpart_doc,
                                          counterpart_nome, tipo_titulo,
                                          title_id, bound_at, last_touch_at}
  negociacao:{neg_id}:chat      → JSON {channel, chat_id}

TTL: 7 dias, renovado a cada mensagem ("last_touch_at" atualiza).
Encerramento: ao receber status terminal (ACEITA/RECUSADA/EXPIRADA/
BLOQUEADA), `release_binding` apaga as duas chaves.
"""

import json
import logging
from datetime import datetime
from typing import Optional

import redis

from handoff_server.config import settings

logger = logging.getLogger(__name__)


_TTL_SECONDS = 7 * 24 * 60 * 60   # 7 dias
_LOCK_TTL_SECONDS = 5             # lock breve para race condition
_UPDATE_DEDUP_TTL_SECONDS = 24 * 60 * 60   # 24h — Telegram nao reentrega depois


def _client() -> Optional[redis.Redis]:
    """Cliente Redis singleton — falha graciosamente se Redis off."""
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
            logger.warning(f"session_binding: Redis indisponivel: {exc}")
            _client._cached = None  # type: ignore
    return _client._cached  # type: ignore


def _chat_key(chat_id: str) -> str:
    return f"chat:tg:{chat_id}"


def _neg_key(negociacao_id: str) -> str:
    return f"negociacao:{negociacao_id}:chat"


def _lock_key(chat_id: str) -> str:
    return f"lock:chat:{chat_id}"


def _update_seen_key(update_id: int) -> str:
    return f"tg:update:seen:{update_id}"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def create_binding(
    *,
    chat_id: str,
    negociacao_id: str,
    counterpart_doc: str,
    counterpart_nome: str,
    tipo_titulo: str,
    title_id: str,
    title_uuid: Optional[str] = None,
    channel: str = "telegram",
) -> bool:
    """
    Cria as duas chaves espelhadas. Retorna False se Redis off.

    `title_uuid` (opcional): UUID do AccountReceivable, usado para emitir
    boleto pos-ACCEPT em AR. Quando ausente, o ACCEPT registra o evento
    no backend mas a confirmacao ao contraparte fica generica.

    `counter_count` rastreia quantas contrapropostas o devedor ja fez
    nesta sessao (limita repetir negociacao sem fim — escala para humano
    apos MAX_DEBTOR_COUNTERS).
    """
    client = _client()
    if client is None:
        return False
    now = _now_iso()
    chat_payload = {
        "negociacao_id": negociacao_id,
        "counterpart_doc": counterpart_doc,
        "counterpart_nome": counterpart_nome,
        "tipo_titulo": tipo_titulo,
        "title_id": title_id,
        "title_uuid": title_uuid or "",
        "channel": channel,
        "bound_at": now,
        "last_touch_at": now,
        "counter_count": 0,
    }
    neg_payload = {"channel": channel, "chat_id": chat_id}
    try:
        pipe = client.pipeline()
        pipe.setex(_chat_key(chat_id), _TTL_SECONDS, json.dumps(chat_payload))
        pipe.setex(_neg_key(negociacao_id), _TTL_SECONDS, json.dumps(neg_payload))
        pipe.execute()
        return True
    except Exception as exc:
        logger.warning(f"session_binding: create falhou: {exc}")
        return False


def get_binding_by_chat(chat_id: str) -> Optional[dict]:
    """Le binding pela chave chat_id. None se nao existe / Redis off."""
    client = _client()
    if client is None:
        return None
    try:
        raw = client.get(_chat_key(chat_id))
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def get_binding_by_negociacao(negociacao_id: str) -> Optional[dict]:
    """Le binding pela chave negociacao_id (lookup reverso)."""
    client = _client()
    if client is None:
        return None
    try:
        raw = client.get(_neg_key(negociacao_id))
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def touch_binding(chat_id: str) -> None:
    """Atualiza last_touch_at e renova TTL — chamar a cada mensagem nova."""
    client = _client()
    if client is None:
        return
    binding = get_binding_by_chat(chat_id)
    if not binding:
        return
    binding["last_touch_at"] = _now_iso()
    neg_id = binding.get("negociacao_id")
    try:
        pipe = client.pipeline()
        pipe.setex(_chat_key(chat_id), _TTL_SECONDS, json.dumps(binding))
        if neg_id:
            pipe.expire(_neg_key(neg_id), _TTL_SECONDS)
        pipe.execute()
    except Exception as exc:
        logger.warning(f"session_binding: touch falhou: {exc}")


def increment_counter_count(chat_id: str) -> int:
    """
    Incrementa o contador de contrapropostas do devedor neste binding.
    Retorna o novo valor (>=1 apos incremento; 0 se binding nao existe).

    Usado pelo classifier do webhook: apos N contrapropostas o agente
    escala para humano em vez de registrar mais uma rodada.
    """
    client = _client()
    if client is None:
        return 0
    binding = get_binding_by_chat(chat_id)
    if not binding:
        return 0
    current = int(binding.get("counter_count", 0) or 0)
    new_value = current + 1
    binding["counter_count"] = new_value
    binding["last_touch_at"] = _now_iso()
    try:
        client.setex(_chat_key(chat_id), _TTL_SECONDS, json.dumps(binding))
    except Exception as exc:
        logger.warning(f"session_binding: increment_counter falhou: {exc}")
        return current
    return new_value


def release_binding(chat_id: str) -> None:
    """Apaga as duas chaves — usar em status terminal."""
    client = _client()
    if client is None:
        return
    binding = get_binding_by_chat(chat_id)
    try:
        pipe = client.pipeline()
        pipe.delete(_chat_key(chat_id))
        if binding and binding.get("negociacao_id"):
            pipe.delete(_neg_key(binding["negociacao_id"]))
        pipe.execute()
    except Exception as exc:
        logger.warning(f"session_binding: release falhou: {exc}")


def try_acquire_lock(chat_id: str) -> bool:
    """
    Lock distribuido de curta duracao (5s) para evitar processar 2
    mensagens do mesmo chat em paralelo. Retorna True se conseguiu.
    """
    client = _client()
    if client is None:
        return True  # sem Redis, segue best-effort
    try:
        return bool(
            client.set(
                _lock_key(chat_id), "1", nx=True, ex=_LOCK_TTL_SECONDS
            )
        )
    except Exception:
        return True


def release_lock(chat_id: str) -> None:
    client = _client()
    if client is None:
        return
    try:
        client.delete(_lock_key(chat_id))
    except Exception:
        pass


def mark_update_seen(update_id: int) -> bool:
    """
    Marca um update_id do Telegram como visto. Retorna True na primeira vez,
    False se ja existia (duplicata de reentrega).

    Sem Redis: degrada para True (best-effort, sem dedup).
    """
    client = _client()
    if client is None:
        return True
    try:
        ok = client.set(
            _update_seen_key(update_id), "1",
            nx=True, ex=_UPDATE_DEDUP_TTL_SECONDS,
        )
        return bool(ok)
    except Exception:
        return True
