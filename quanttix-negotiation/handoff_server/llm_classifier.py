"""
llm_classifier — classifica resposta do contraparte e extrai termos.

Substitui keyword classifier do v1. Tarefas:
  1. outcome: ACCEPT | REJECT | COUNTER
  2. extracted_terms (so se COUNTER):
       - num_parcelas: int | None
       - valor_acordado: Decimal-str | None
       - novo_vencimento: ISO date | None
       - desconto_pct: Decimal-str | None

Backend: chat LLM via HTTP (default Qwen3-32B em http://127.0.0.1:8083).
Endpoint OpenAI-compat (`/v1/chat/completions`). Sem auth — loopback.

Em qualquer falha (rede, timeout, JSON invalido, LLM off), retorna
None e o chamador faz fallback para keyword classifier. NUNCA levanta.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from handoff_server.config import settings

logger = logging.getLogger(__name__)


_TIMEOUT_SECONDS = 8.0
_MAX_TOKENS = 256


@dataclass
class ClassificationResult:
    outcome: str                      # "ACCEPT" | "REJECT" | "COUNTER"
    confidence: float = 0.0           # 0..1
    extracted_terms: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.extracted_terms is None:
            self.extracted_terms = {}


_SYSTEM_PROMPT = """Voce e um classificador de respostas de cobranca em portugues do Brasil.

Recebe a ULTIMA proposta enviada ao devedor e a RESPOSTA do devedor.
Classifica a intencao da resposta e, se for contraproposta, extrai os termos.

REGRAS:
- ACCEPT: devedor aceita a proposta sem condicoes ("sim", "aceito", "ok pode fazer", "tudo bem fechado").
- REJECT: devedor recusa sem alternativa ("nao", "nao quero", "sem condicoes", "deixa pra la").
- COUNTER: devedor pede algo diferente — parcelar, prazo maior, desconto maior, vencimento diferente,
  ou faz qualquer condicao ("sim mas em 3x", "pode ser semana que vem", "consegue 20%?",
  "so se for ate sexta", "sim, com desconto").
- Em duvida entre ACCEPT e COUNTER, escolha COUNTER (sempre que houver "mas", "se", "porem",
  ou condicional, e COUNTER).

EXTRACAO (so quando COUNTER):
- num_parcelas: inteiro 1..24 mencionado (ex: "em 3 vezes" -> 3; "12x" -> 12). Default null.
- valor_acordado: valor numerico que o devedor propoe pagar, sem simbolo (ex: "1500 reais" -> "1500.00"). Default null.
- desconto_pct: percentual de desconto pedido entre 0 e 100 (ex: "20%" -> "20.00"). Default null.
- novo_vencimento: data ISO yyyy-mm-dd se mencionada (ex: "sexta-feira" -> calcule baseado em hoje
  se possivel; "dia 30" -> use o proximo dia 30). Em duvida, null.

RESPONDA APENAS COM JSON VALIDO, sem markdown, sem ``` , sem texto extra:
{"outcome":"ACCEPT|REJECT|COUNTER","confidence":0.0-1.0,"extracted_terms":{...}}
"""


def classify_reply_llm(
    text: str,
    *,
    last_proposal_summary: str = "",
) -> Optional[ClassificationResult]:
    """
    Classifica resposta. Retorna None se LLM indisponivel/falhou —
    chamador faz fallback para keyword.
    """
    if not text or not text.strip():
        return None

    base_url = settings.LLM_CLASSIFIER_URL
    if not base_url:
        return None

    user_msg = (
        f"PROPOSTA ENVIADA: {last_proposal_summary or '(nao informado)'}\n"
        f"RESPOSTA DO DEVEDOR: {text.strip()}"
    )

    body = {
        "model": settings.LLM_CLASSIFIER_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.1,
        "max_tokens": _MAX_TOKENS,
        # llama.cpp ignora response_format mas openai-compat servers aceitam.
        # Mantemos para forward-compat sem quebrar quando ausente.
        "response_format": {"type": "json_object"},
    }

    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as http:
            resp = http.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                json=body,
            )
        if not resp.is_success:
            logger.warning(
                "llm_classifier: HTTP %s body=%s",
                resp.status_code, resp.text[:200],
            )
            return None
        payload = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("llm_classifier: rede falhou: %s", exc)
        return None
    except ValueError as exc:
        logger.warning("llm_classifier: resposta nao-JSON: %s", exc)
        return None

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        logger.warning("llm_classifier: payload inesperado: %s", exc)
        return None

    return _parse_classification(content)


def _parse_classification(raw: str) -> Optional[ClassificationResult]:
    """
    Extrai outcome + extracted_terms do JSON do LLM. Tolerante a:
      - texto antes/depois do JSON (alguns modelos vazam preambulo)
      - <think>...</think> tags do Qwen
      - confidence ausente
    """
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # Procura primeiro objeto JSON balanceado
    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    if not obj_match:
        return None
    try:
        data = json.loads(obj_match.group())
    except json.JSONDecodeError:
        return None

    outcome = str(data.get("outcome", "")).upper().strip()
    if outcome not in ("ACCEPT", "REJECT", "COUNTER"):
        return None

    confidence_raw = data.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    extracted = data.get("extracted_terms") or {}
    if not isinstance(extracted, dict):
        extracted = {}

    # Sanitiza extracted_terms — passa adiante apenas o que o backend aceita.
    # Limites alinhados com schema NegotiationTerms do quanttix_backend
    # (treasury/schemas/negotiation.py): num_parcelas 1..24, desconto_pct 0..100.
    clean: dict[str, Any] = {}
    if outcome == "COUNTER":
        num_parc = extracted.get("num_parcelas")
        if isinstance(num_parc, int) and 1 <= num_parc <= 24:
            clean["num_parcelas"] = num_parc

        # desconto_pct: 0..100. Fora do range, descarta (backend retornaria 422).
        raw_desconto = extracted.get("desconto_pct")
        if raw_desconto not in (None, "", "null"):
            try:
                v = float(str(raw_desconto).replace(",", "."))
                if 0 <= v <= 100:
                    clean["desconto_pct"] = f"{v}"
            except (TypeError, ValueError):
                pass

        # valor_acordado: >= 0. Sem upper limit (backend confia no proprio range).
        raw_valor = extracted.get("valor_acordado")
        if raw_valor not in (None, "", "null"):
            try:
                v = float(str(raw_valor).replace(",", "."))
                if v >= 0:
                    clean["valor_acordado"] = f"{v}"
            except (TypeError, ValueError):
                pass

        vencimento = extracted.get("novo_vencimento")
        if isinstance(vencimento, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", vencimento):
            from datetime import date as _date
            try:
                clean["novo_vencimento"] = _date.fromisoformat(vencimento)
            except ValueError:
                pass

    return ClassificationResult(
        outcome=outcome, confidence=confidence, extracted_terms=clean,
    )
