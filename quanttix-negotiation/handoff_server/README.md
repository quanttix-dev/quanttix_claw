# handoff_server — Quanttix Negotiation (lado claw)

Serviço Python FastAPI loopback que recebe handoffs do `quanttix_ai` e
conduz a conversa Telegram com a contraparte na v1.

## Por que Python em vez de plugin OpenClaw

OpenClaw plugin SDK (v0) registra **providers**, **channels** e
**agents** — mas não rotas HTTP arbitrárias. Criar o handoff endpoint
como plugin exigiria fork invasivo do core OpenClaw.

Pra v1, este serviço Python autossuficiente:
- roda como processo separado no Server X (POD)
- porta loopback `18790` (gateway OpenClaw fica em `18789`, sem conflito)
- compartilha o Redis local do POD com `quanttix_ai`
- chama backend REST (mesmo `quanttix_api_client.py` style)
- envia Telegram via Bot API direto

**v2** absorve isto num plugin OpenClaw quando o agente conversacional
(Gemma 4 E2B via `quanttix-planner`) entrar em produção.

## Endpoints

```
POST /negotiation/start          (loopback, bearer token)
POST /telegram/webhook           (publico — Telegram Bot API webhook)
GET  /health                     (smoke)
```

## Variáveis de ambiente

```
QUANTTIX_HANDOFF_TOKEN   bearer compartilhado com quanttix_ai
TELEGRAM_BOT_TOKEN       bot externo @Quanttix_Negotiator_bot
TELEGRAM_WEBHOOK_SECRET  segredo do webhook (opcional, header X-Telegram-Bot-Api-Secret-Token)
BACKEND_BASE_URL         https://test.quanttix.com.br
BACKEND_SVC_EMAIL        svc_quanttix_claw@quanttix.com
BACKEND_SVC_PASSWORD     <senha do service account>
REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD
HANDOFF_PORT             default 18790
HANDOFF_HOST             default 127.0.0.1 (loopback)
```

## Layout

```
handoff_server/
├── main.py              FastAPI app + uvicorn entry
├── config.py            settings via env
├── handoff_endpoint.py  POST /negotiation/start
├── telegram_webhook.py  POST /telegram/webhook
├── session_binding.py   Redis chat_id↔neg_id (TTL 7d, renovacao)
├── backend_client.py    httpx cliente REST do backend
├── telegram_sender.py   Telegram Bot API
└── tests/
```

## Smoke local (POD)

```bash
cd quanttix_claw/quanttix-negotiation/handoff_server
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 18790
```

Ver `STAGE_7A.md` para visão completa do estágio.
