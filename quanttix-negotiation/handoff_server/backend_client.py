"""
backend_client — cliente REST do quanttix_backend (service account
svc_quanttix_claw).

Implementacao espelha o padrao do `quanttix_ai/llm_api/services/
quanttix_api_client.py` (JWT cache em Redis, retry 401, headers de
tenant context). Reduzida ao que o handoff_server precisa:

  - get_policy(title_id, counterpart_doc) -> PolicyDecisionResponse
  - get_counterpart(documento, tipo_titulo) -> CounterpartResponse
  - propose_negotiation(title_id, counterpart_doc, terms, ...) -> NegotiationEventResponse
  - register_counterproposal(negociacao_id, terms) -> NegotiationEventResponse
  - accept_negotiation(negociacao_id, terms?) -> NegotiationEventResponse
  - reject_negotiation(negociacao_id, reason, mensagem) -> NegotiationEventResponse
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Any, Optional

import httpx

from handoff_server.config import settings

logger = logging.getLogger(__name__)


_TOKEN_CACHE_TTL = 3000  # 50 minutos (token Django ttl=1h)


class BackendClientError(Exception):
    """Erro ao falar com o backend (HTTP, auth, validation)."""


class BackendClient:
    """Cliente HTTP autenticado, sincrono (httpx.Client)."""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._http = httpx.Client(
            base_url=settings.BACKEND_BASE_URL,
            timeout=30.0,
        )

    def close(self) -> None:
        self._http.close()

    # ── Auth ─────────────────────────────────────────────────────────

    def _login(self) -> str:
        if not settings.BACKEND_SVC_EMAIL or not settings.BACKEND_SVC_PASSWORD:
            raise BackendClientError(
                "BACKEND_SVC_EMAIL/PASSWORD nao configurados"
            )
        resp = self._http.post(
            "/api/v1/auth/login",
            json={
                "email": settings.BACKEND_SVC_EMAIL,
                "password": settings.BACKEND_SVC_PASSWORD,
            },
        )
        if not resp.is_success:
            raise BackendClientError(
                f"login falhou: HTTP {resp.status_code} body={resp.text[:200]}"
            )
        payload = resp.json()
        inner = payload.get("data", payload)
        token = inner.get("access_token", "")
        if not token:
            raise BackendClientError(f"login sem access_token: {payload}")
        return token

    def _headers(self, idempotency_key: Optional[str] = None) -> dict:
        if not self._token:
            self._token = self._login()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        if settings.BACKEND_TENANT_ID:
            headers["X-Tenant-Id"] = settings.BACKEND_TENANT_ID
        if settings.BACKEND_EMPRESA_ID:
            headers["X-Empresa-Id"] = settings.BACKEND_EMPRESA_ID
        if settings.BACKEND_FILIAL_ID:
            headers["X-Filial-Id"] = settings.BACKEND_FILIAL_ID
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        """GET/POST autenticado com retry em 401."""
        for attempt in (1, 2):
            headers = self._headers(idempotency_key=idempotency_key)
            resp = self._http.request(
                method, path, params=params, json=json_body, headers=headers,
            )
            if resp.status_code == 401 and attempt == 1:
                self._token = None  # forca relogin
                continue
            if not resp.is_success:
                raise BackendClientError(
                    f"{method} {path}: HTTP {resp.status_code} body={resp.text[:300]}"
                )
            try:
                payload = resp.json()
            except ValueError:
                return None
            return payload.get("data", payload) if isinstance(payload, dict) else payload
        raise BackendClientError(f"{method} {path}: 401 apos retry")

    # ── Endpoints — leitura ─────────────────────────────────────────

    def get_policy(self, title_id: str, counterpart_doc: str) -> dict:
        return self._request(
            "GET", "/api/v1/tesouraria/negotiation/policy",
            params={"title_id": title_id, "counterpart_doc": counterpart_doc},
        )

    def get_counterpart(self, documento: str, tipo_titulo: str) -> dict:
        return self._request(
            "GET", f"/api/v1/tesouraria/negotiation/counterparts/{documento}",
            params={"tipo_titulo": tipo_titulo},
        )

    # ── Endpoints — escrita ────────────────────────────────────────

    def propose_negotiation(
        self,
        *,
        title_id: str,
        counterpart_doc: str,
        tipo_titulo: str,
        valor_titulo: Decimal,
        desconto_pct: Optional[Decimal] = None,
        valor_acordado: Optional[Decimal] = None,
        novo_vencimento: Optional[date] = None,
        mensagem_agente: str = "",
        idempotency_key: Optional[str] = None,
    ) -> dict:
        body = {
            "title_id": title_id,
            "counterpart_doc": counterpart_doc,
            "tipo_titulo": tipo_titulo,
            "valor_titulo": str(valor_titulo),
            "terms": {
                "desconto_pct": str(desconto_pct) if desconto_pct is not None else None,
                "valor_acordado": str(valor_acordado) if valor_acordado is not None else None,
                "num_parcelas": 1,
                "novo_vencimento": novo_vencimento.isoformat() if novo_vencimento else None,
                "mensagem_agente": mensagem_agente,
            },
        }
        return self._request(
            "POST", "/api/v1/tesouraria/negotiation/propose",
            json_body=body, idempotency_key=idempotency_key,
        )

    def register_counterproposal(
        self,
        *,
        negociacao_id: str,
        desconto_pct: Optional[Decimal] = None,
        valor_acordado: Optional[Decimal] = None,
        mensagem_agente: str = "",
        idempotency_key: Optional[str] = None,
    ) -> dict:
        body = {
            "terms": {
                "desconto_pct": str(desconto_pct) if desconto_pct is not None else None,
                "valor_acordado": str(valor_acordado) if valor_acordado is not None else None,
                "num_parcelas": 1,
                "mensagem_agente": mensagem_agente,
            },
        }
        return self._request(
            "POST",
            f"/api/v1/tesouraria/negotiation/{negociacao_id}/counterproposal",
            json_body=body, idempotency_key=idempotency_key,
        )

    def accept_negotiation(
        self,
        *,
        negociacao_id: str,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        return self._request(
            "POST",
            f"/api/v1/tesouraria/negotiation/{negociacao_id}/accept",
            json_body={}, idempotency_key=idempotency_key,
        )

    def reject_negotiation(
        self,
        *,
        negociacao_id: str,
        reason: str,
        mensagem_agente: str = "",
        idempotency_key: Optional[str] = None,
    ) -> dict:
        body = {"reason": reason, "mensagem_agente": mensagem_agente}
        return self._request(
            "POST",
            f"/api/v1/tesouraria/negotiation/{negociacao_id}/reject",
            json_body=body, idempotency_key=idempotency_key,
        )
