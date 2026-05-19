"""
telegram_sender — envio de mensagens via Bot API do Telegram.

Sincrono (httpx.Client). Reutilizado pelos handoff_endpoint (1a msg) e
telegram_webhook (replies do agente). Mensagens sao texto puro; formatacao
em Markdown/HTML pode ser adicionada no v2.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from handoff_server.config import settings
from handoff_server.session_binding import touch_binding

logger = logging.getLogger(__name__)


@dataclass
class TelegramResult:
    ok: bool
    message_id: Optional[int] = None
    error_description: str = ""
    file_id: Optional[str] = None


class TelegramSender:
    """Cliente sincrono Telegram Bot API."""

    def __init__(self) -> None:
        self._http = httpx.Client(timeout=15.0)

    def close(self) -> None:
        self._http.close()

    def _api_url(self, method: str) -> str:
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN nao configurado")
        return f"https://api.telegram.org/bot{token}/{method}"

    def send_message(self, chat_id: str, text: str) -> TelegramResult:
        """Envia texto. Retorna TelegramResult (nunca levanta — log em erro)."""
        try:
            resp = self._http.post(
                self._api_url("sendMessage"),
                json={"chat_id": chat_id, "text": text},
            )
        except httpx.HTTPError as exc:
            logger.error(f"telegram send falhou: {exc}")
            return TelegramResult(ok=False, error_description=str(exc))

        try:
            payload = resp.json()
        except ValueError:
            return TelegramResult(
                ok=False, error_description=f"resposta nao-JSON: {resp.text[:200]}"
            )

        if payload.get("ok"):
            result = payload.get("result", {})
            # Renova TTL do binding tambem quando NOS enviamos —
            # senao bindings de conversas longas (so envio, sem reply)
            # poderiam expirar antes do contraparte responder.
            try:
                touch_binding(chat_id)
            except Exception as exc:  # pragma: no cover — best effort
                logger.debug(f"touch_binding falhou em send: {exc}")
            return TelegramResult(ok=True, message_id=result.get("message_id"))
        return TelegramResult(
            ok=False,
            error_description=payload.get("description", "erro desconhecido"),
        )

    def send_document(
        self,
        chat_id: str,
        *,
        document: bytes,
        filename: str,
        caption: Optional[str] = None,
        mime_type: str = "application/pdf",
    ) -> TelegramResult:
        """Envia arquivo via multipart/form-data (Telegram sendDocument).

        Usado para entregar o boleto PDF ficticio quando o backend devolve
        boleto=None. Limite documentado do Telegram: 50 MB por documento;
        o boleto gerado fica em torno de 15 KB.
        """
        data: dict[str, str] = {"chat_id": chat_id}
        if caption is not None:
            data["caption"] = caption

        files = {"document": (filename, document, mime_type)}

        try:
            resp = self._http.post(
                self._api_url("sendDocument"),
                data=data,
                files=files,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            logger.error(f"telegram send_document falhou: {exc}")
            return TelegramResult(ok=False, error_description=str(exc))

        try:
            payload = resp.json()
        except ValueError:
            return TelegramResult(
                ok=False, error_description=f"resposta nao-JSON: {resp.text[:200]}"
            )

        if payload.get("ok"):
            result = payload.get("result", {})
            doc = result.get("document", {}) or {}
            try:
                touch_binding(chat_id)
            except Exception as exc:  # pragma: no cover — best effort
                logger.debug(f"touch_binding falhou em send_document: {exc}")
            return TelegramResult(
                ok=True,
                message_id=result.get("message_id"),
                file_id=doc.get("file_id"),
            )
        return TelegramResult(
            ok=False,
            error_description=payload.get("description", "erro desconhecido"),
        )
