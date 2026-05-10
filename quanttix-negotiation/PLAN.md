# PLAN — Agente Autônomo de Negociação

> Documento vivo. Atualize as caixas `- [ ]` → `- [x]` a cada subtarefa
> concluída e ajuste o **Status** do estágio quando mudar. Não delete
> subtarefas — se uma se tornar irrelevante, marque como `~~obsoleta~~`
> com nota explicando.

---

## Status global

| # | Estágio | Status |
|---|---|---|
| 1 | Política de Negociação (Playbook) | NÃO INICIADO |
| 2 | Skill / Persona do Agente | NÃO INICIADO |
| 3 | MCP Server — Tools de leitura | NÃO INICIADO |
| 4 | Guardrails — validação determinística | NÃO INICIADO |
| 5 | MCP Server — Tools de escrita + audit | NÃO INICIADO |
| 6 | Backend Dispatch Endpoint (SSE) | NÃO INICIADO |
| 7 | Session binding (Redis compartilhado) | NÃO INICIADO |
| 8 | Allowlist dinâmica Telegram + testes E2E | NÃO INICIADO |

---

## Estágio 1: Política de Negociação (Playbook)

**Repo**: `quanttix_claw`
**Bloqueia**: Estágios 2, 3, 4, 5
**Depende de**: nada
**Status**: NÃO INICIADO

### Objetivo
Codificar em YAML as regras de engajamento (faixas de desconto, parcelamento,
prazo, thresholds de escalação, bloqueios) que o agente consulta antes de
propor termos. A política é dado; a engine de lookup é código Python.

### Entregáveis
- `extensions/quanttix-negotiation/policy/policy.example.yaml`
- `extensions/quanttix-negotiation/policy/policy_schema.py` (Pydantic v2)
- `extensions/quanttix-negotiation/policy/policy_engine.py` (lookup)
- `extensions/quanttix-negotiation/policy/README.md` (como editar)
- `extensions/quanttix-negotiation/policy/tests/test_policy_schema.py`
- `extensions/quanttix-negotiation/policy/tests/test_policy_engine.py`

### Subtarefas
- [ ] Decidir granularidade da policy: dimensões = `(tipo_titulo, tier_contraparte, faixa_valor)`
- [ ] Schema Pydantic v2: campos `tipo_titulo (AR|AP)`, `tier (vip|padrao|risco)`,
      `valor_min/max`, `max_desconto_pct`, `max_parcelas`, `max_prazo_dias_extra`,
      `juros_mensal_min_pct`, `escalation_threshold_pct`, `bloqueio_score_min`,
      `bloqueio_em_protesto`
- [ ] Template YAML com 4 cenários: `AR-vip`, `AR-padrao`, `AP-fornecedor-estrategico`,
      `AP-fornecedor-eventual`
- [ ] Função `load_policy(path: Path) -> Policy` (valida no carregamento)
- [ ] Função `lookup(policy, title_meta, counterpart_meta) -> PolicyDecision`
      retornando dataclass com `allowed`, `max_desconto_pct`, `escalation_required`,
      `bloqueio_motivo` (None se OK)
- [ ] Hierarquia de match: regra mais específica vence (tier_vip > tier_padrao)
- [ ] Teste: policy válida carrega
- [ ] Teste: policy inválida (`max_desconto > 100`) falha com mensagem legível
- [ ] Teste: lookup escolhe `AR-vip` quando `counterpart.tier=vip`
- [ ] Teste: `bloqueio_em_protesto=True` retorna `PolicyDecision(allowed=False)`
- [ ] Teste: valor fora da faixa cai pra regra mais ampla
- [ ] Documentar formato em `policy/README.md` com 2 exemplos editáveis

### Contrato — PolicyDecision
```python
@dataclass
class PolicyDecision:
    allowed: bool
    max_desconto_pct: Decimal | None      # None se não aplicável
    max_parcelas: int | None
    max_prazo_dias_extra: int | None
    juros_mensal_min_pct: Decimal | None
    escalation_required: bool              # True se proposta requer humano
    bloqueio_motivo: str | None            # preenchido se allowed=False
    matched_rule_id: str                   # id da regra que casou (audit)
```

### Critério de aceite
- `pytest extensions/quanttix-negotiation/policy/tests/` passa todos os testes
- Pelo menos 1 cenário AR e 1 AP completos e exemplificados no YAML
- Edição manual quebrada acusa erro legível (não stack trace cru)
- `lookup` é determinística: mesma entrada → mesma `matched_rule_id`

### Notas de implementação
- Pydantic v2 (já usado no `quanttix_backend`)
- Não codar regras no Python — apenas a engine. YAML é a fonte
- Deixar `tenant_id` como campo opcional na regra (preparar multi-tenant)
- `escalation_threshold_pct < max_desconto_pct` é regra: acima do threshold
  precisa de humano, mas abaixo do máximo o agente pode autorizar

---

## Estágio 2: Skill / Persona do Agente

**Repo**: `quanttix_claw`
**Bloqueia**: Estágios 3 (parcial), 8
**Depende de**: Estágio 1
**Status**: NÃO INICIADO

### Objetivo
Definir o comportamento conversacional do agente: tom, estrutura da conversa,
quando admitir limitação, compliance verbal. O LLM lê este SKILL.md como
system prompt; o policy.yaml entra como contexto dinâmico via tool.

### Entregáveis
- `skills/quanttix-negotiation/SKILL.md` (system prompt principal)
- `skills/quanttix-negotiation/manifest.json` (metadata para OpenClaw)
- `skills/quanttix-negotiation/prompts/greeting.md`
- `skills/quanttix-negotiation/prompts/objection_handling.md`
- `skills/quanttix-negotiation/prompts/escalation.md`
- `skills/quanttix-negotiation/examples/example_ar_acceptance.md`
- `skills/quanttix-negotiation/examples/example_ap_counterproposal.md`

### Subtarefas
- [ ] Definir tom: formal-cordial AR, profissional-direto AP
- [ ] Estrutura da conversa: `saudação → contexto → proposta → contra → fecho`
- [ ] Regras de compliance verbal: não prometer o que não pode cumprir,
      sempre confirmar valores por escrito antes de fechar
- [ ] Frases de escalação: "preciso confirmar internamente"
      (quando `escalation_required=True`)
- [ ] Frases de bloqueio: como recusar com elegância sem revelar razões internas
- [ ] Escrever SKILL.md principal (system prompt, ~600-1000 palavras)
- [ ] manifest.json com `name`, `description`, `tools_required[]`,
      `model_target=planner` (Gemma 4 E2B via quanttix-planner @ 8091)
- [ ] Greeting prompt (saudação por canal: AR vs AP)
- [ ] Objection handling: 5 objeções comuns + scripts de resposta
- [ ] Escalation prompt: como avisar o cliente e o supervisor
- [ ] Exemplo AR-aceitação completo (turn-by-turn)
- [ ] Exemplo AP-contraproposta completo (turn-by-turn)
- [ ] Revisar com `policy.example.yaml` em mãos — os números batem?

### Critério de aceite
- SKILL.md lê coerente em uma passada (sem contradição interna)
- Exemplos mostram o agente usando tools de leitura/escrita
  (mesmo que stubs nesta fase)
- Manifest é parseável pelo OpenClaw skill loader
  (`openclaw skill validate` se existir)
- Tom passa por revisão humana antes de seguir pro Estágio 3

### Notas de implementação
- OpenClaw skills usam o padrão `.agents/skills/<id>/SKILL.md` para skills
  internas e `skills/<id>/SKILL.md` para skills bundled
- Vamos como bundled (`skills/quanttix-negotiation/`)
- O system prompt deve referenciar tools por nome
  (ex: "use `get_title` antes de propor"), não embutir lógica de negócio
- A política NÃO entra no system prompt — ela é injetada via tool
  `get_negotiation_policy` para que mudar policy não precise redeploy do skill

---

## Estágio 3: MCP Server — Tools de leitura

**Repo**: `quanttix_backend`
**Bloqueia**: Estágios 5, 8
**Depende de**: Estágios 1 e 2
**Status**: NÃO INICIADO

### Objetivo
Expor as capacidades de **consulta** ao agente via MCP (Model Context Protocol).
O agente chama essas tools para montar contexto antes de propor termos.
Sem efeitos colaterais — apenas leitura.

### Entregáveis
- `src/mcp/server.py` (FastMCP ou implementação manual)
- `src/mcp/tools/read/get_title.py`
- `src/mcp/tools/read/get_counterpart_profile.py`
- `src/mcp/tools/read/get_negotiation_policy.py`
- `src/mcp/tools/read/get_open_negotiations.py`
- `src/mcp/tools/read/__init__.py` (registry)
- `tests/mcp/test_read_tools.py`
- `src/main.py` (montar rota `/mcp` na FastAPI app)

### Subtarefas
- [ ] Escolher framework MCP: FastMCP (mais simples) vs implementação manual
      sobre stdio/SSE — recomendação: FastMCP via SSE para integrar com OpenClaw
- [ ] Definir transport: SSE em `/mcp` (compatível com mcporter do OpenClaw)
- [ ] Tool `get_title(title_id: str) -> TitleDetails`
- [ ] Tool `get_counterpart_profile(documento: str) -> CounterpartProfile`
- [ ] Tool `get_negotiation_policy(title_id: str, counterpart_doc: str) -> PolicyDecision`
      (chama policy engine do Estágio 1 — copiar lib pro backend ou importar como pacote)
- [ ] Tool `get_open_negotiations(counterpart_doc: str) -> list[OpenNegotiation]`
      (lê de `quanttix.simulation.negociacao.evento`)
- [ ] Autenticação MCP — modelo de segurança restritivo:
  - Bearer token por cliente: `MCP_TOKEN_QUANTTIX_AI`, `MCP_TOKEN_QUANTTIX_CLAW`
  - Middleware FastAPI valida `Authorization: Bearer <token>` e mapeia para
    `client_id` que vai no log de toda chamada (audit operacional)
  - IP allowlist por env: `MCP_ALLOWED_IPS` (CIDR list); recusar fora dela
  - Tokens rotacionáveis sem deploy (carregar de Redis no startup, fallback env)
  - Requisição sem token → 401 (não 403 — esconder existência da rota)
  - Rate limit por `client_id` independente do rate limit por contraparte
  - Documentar em `docs/mcp/security.md` o threat model e o que NÃO está protegido
- [ ] Schemas Pydantic para responses (todas as tools)
- [ ] Teste de cada tool com mock do storage
- [ ] Teste de integração: chamar tool via cliente MCP real (OpenClaw)
- [ ] Documentar tools em `docs/mcp/tools.md`

### Contratos das tools

```python
class TitleDetails(BaseModel):
    title_id: str
    tipo_titulo: Literal["AR", "AP"]
    valor_original: Decimal
    valor_atualizado: Decimal       # com juros até hoje
    dt_emissao: date
    dt_vencimento: date
    dias_atraso: int                # 0 se em dia
    counterpart_doc: str
    counterpart_nome: str
    status: str

class CounterpartProfile(BaseModel):
    documento: str
    nome: str
    tier: Literal["vip", "padrao", "risco"]
    score: int                      # 0-1000
    em_protesto: bool
    historico_pagamentos: int       # qtd títulos pagos
    historico_atrasos_pct: Decimal
    valor_aberto_total: Decimal
    ultima_negociacao_resultado: str | None

class OpenNegotiation(BaseModel):
    negociacao_id: str
    title_id: str
    status_evento: str
    dt_proposta: datetime
    dt_expiracao: datetime | None
    valor_proposto: Decimal | None
    desconto_proposto_pct: Decimal | None
```

### Critério de aceite
- `pytest tests/mcp/test_read_tools.py` passa
- Iniciar o backend e chamar `curl http://localhost:8000/mcp/tools` lista as 4 tools
- OpenClaw via mcporter consegue listar e invocar as tools
- Latência de cada tool < 300ms em ambiente de dev

### Notas de implementação
- Para `get_title` e `get_counterpart_profile`, a fonte primária são as
  tabelas Iceberg via Trino (já existe `quanttix_backend/src/treasury/`)
- Cuidado com cache: dados de título mudam (juros calculados na hora)
- `get_open_negotiations` lê de `quanttix.simulation.negociacao.evento`
  e agrega por `negociacao_id` (último evento vence)

---

## Estágio 4: Guardrails — validação determinística

**Repo**: `quanttix_backend`
**Bloqueia**: Estágio 5
**Depende de**: Estágios 1 e 3
**Status**: NÃO INICIADO

### Objetivo
Camada de validação que roda **antes** de toda tool de escrita. O LLM pode
"decidir" qualquer coisa; o guardrail valida contra a policy carregada e
recusa com erro estruturado se a decisão violar regra. Audit é parcimonioso:
operacional vai pro Redis (TTL curto), só evento material vai pro Iceberg —
e mesmo assim ANEXADO ao evento de negociação existente, sem tabela nova.

### Entregáveis
- `src/mcp/guardrails/policy_check.py`
- `src/mcp/guardrails/rate_limit.py`
- `src/mcp/guardrails/audit_ops.py` (Redis transient, TTL 7d)
- `src/mcp/guardrails/audit_event.py` (anexa colunas ao evento de negociação)
- `src/mcp/guardrails/__init__.py` (decorador `@guardrail`)
- `tests/mcp/test_guardrails.py`

### Subtarefas
- [ ] Decorador `@guardrail(rules=[...])` para envolver tools de escrita
- [ ] Guardrail `policy_check`: chama `policy_engine.lookup()` e valida
      `desconto_proposto_pct <= max_desconto_pct`, etc.
- [ ] Guardrail `rate_limit`: máx N propostas por contraparte/dia, máx M
      negociações simultâneas (chave Redis `rate:negociacao:{doc}:{date}`)
- [ ] Guardrail `escalation_check`: se proposta > `escalation_threshold_pct`,
      bloqueia accept automático e retorna erro `ESCALATION_REQUIRED`
- [ ] Guardrail `block_check`: se contraparte em protesto/score baixo,
      bloqueia toda escrita com `BLOCKED_BY_POLICY`
- [ ] **Audit operacional (Redis)**: toda chamada de tool grava em
      `audit:tool:{client_id}:{session_id}:{ts}` com payload comprimido
      (gzip+base64). TTL 7 dias. Uso: debug e investigação rápida.
      NÃO vai pro data lake — fica no Redis e expira.
- [ ] **Audit durável (Iceberg)**: NÃO criar tabela separada. Estender o
      schema de `quanttix.simulation.negociacao.evento` com duas colunas:
      `guardrail_motivo STRING` e `regra_aplicada_id STRING`.
      Apenas eventos materiais (PROPOSTA/CONTRAPROPOSTA/ACEITA/RECUSADA/
      EXPIRADA/ESCALADA/BLOQUEADA) vão pro lake — uma linha por evento.
- [ ] Métrica agregada (Prometheus, não evento por evento):
      `negotiation_guardrail_blocks_total{rule="..."}`,
      `negotiation_proposals_total{outcome="..."}`. Permite alarmar
      tendências sem consultar a tabela.
- [ ] Erro estruturado: `GuardrailError(code, message, hint)` parseável
      pelo LLM para responder ao usuário/contraparte com clareza
- [ ] Teste: tool de escrita com desconto acima do máximo é bloqueada
- [ ] Teste: rate limit atinge limite e bloqueia próximo
- [ ] Teste: audit operacional grava no Redis com TTL e desaparece após
- [ ] Teste: audit durável NÃO duplica registro quando evento já existe
- [ ] Teste: alterar policy reflete no próximo lookup sem restart

### Códigos de erro

```
POLICY_VIOLATION       — proposta excede limite da policy
ESCALATION_REQUIRED    — precisa aprovação humana antes de seguir
BLOCKED_BY_POLICY      — contraparte/título bloqueados (protesto, score)
RATE_LIMITED           — limite de chamadas atingido
INVALID_STATE          — operação não cabe no estado atual da negociação
                         (ex: accept em negociação já fechada)
```

### Critério de aceite
- 100% das tools de escrita do Estágio 5 passam pelo decorador
- Bypass do guardrail é impossível (testes garantem)
- Tabela audit tem schema definido e pelo menos um teste de gravação

### Notas de implementação
- Não confie no LLM para respeitar limites — o guardrail é o NORTE
- Audit log é write-only; nunca update/delete
- Rate limit usa chave Redis `rate:negociacao:{contraparte_doc}:{date}`

---

## Estágio 5: MCP Server — Tools de escrita + audit

**Repo**: `quanttix_backend`
**Bloqueia**: Estágio 6 (parcial)
**Depende de**: Estágios 3 e 4
**Status**: NÃO INICIADO

### Objetivo
Expor as **ações** do agente: propor termos, registrar contraproposta,
aceitar, recusar, escalar. Cada uma envelopada pelo guardrail e gera
evento em `quanttix.simulation.negociacao.evento`.

### Entregáveis
- `src/mcp/tools/write/propose_negotiation.py`
- `src/mcp/tools/write/record_counterproposal.py`
- `src/mcp/tools/write/accept_negotiation.py`
- `src/mcp/tools/write/reject_negotiation.py`
- `src/mcp/tools/write/escalate_to_human.py`
- `src/mcp/tools/write/__init__.py`
- `tests/mcp/test_write_tools.py`

### Subtarefas
- [ ] Tool `propose_negotiation(title_id, terms) -> NegotiationEvent`
      grava com status `PROPOSTA`
- [ ] Tool `record_counterproposal(negociacao_id, terms) -> NegotiationEvent`
      grava status `CONTRAPROPOSTA`, valida que existe `PROPOSTA` prévia
- [ ] Tool `accept_negotiation(negociacao_id) -> AcceptResult`
      grava `ACEITA` e retorna sinal para o agente saber que dispatch
      do boleto pode ser chamado (Estágio 6 conecta o dispatch)
- [ ] Tool `reject_negotiation(negociacao_id, reason) -> NegotiationEvent`
      grava `RECUSADA` com motivo
- [ ] Tool `escalate_to_human(negociacao_id, reason, context) -> EscalationTicket`
      cria entrada em fila de aprovação humana (canal interno de supervisão)
- [ ] Todas envelopadas com `@guardrail` (Estágio 4)
- [ ] Escrita idempotente: chamar `propose` 2x com mesmo `idempotency_key`
      não duplica evento
- [ ] Teste por tool: caminho feliz
- [ ] Teste por tool: bloqueado por guardrail
- [ ] Teste: idempotência
- [ ] Teste: state machine consistency (accept antes de propose falha)

### Contratos

```python
class NegotiationTerms(BaseModel):
    desconto_pct: Decimal | None = None
    valor_acordado: Decimal | None = None
    num_parcelas: int = 1
    novo_vencimento: date | None = None
    juros_pct_mes: Decimal | None = None
    mensagem_agente: str

class NegotiationEvent(BaseModel):
    negociacao_id: str        # gerado no primeiro propose; reaproveitado
    evento_id: str             # UUID por evento
    status_evento: str
    terms: NegotiationTerms
    dt_evento: datetime
    expira_em: datetime | None
```

### Critério de aceite
- Pytest cobre os 5 caminhos felizes + 5 caminhos bloqueados
- Idempotência verificada
- Eventos aparecem em `quanttix.simulation.negociacao.evento` após chamada
  (teste de integração com Iceberg local ou stub)

### Notas de implementação
- `negociacao_id` é gerado no primeiro `propose_negotiation` e retornado
  ao agente; ele DEVE passar de volta em chamadas subsequentes
- `idempotency_key` é hash de `(negociacao_id, status_evento, terms_canonical)`
- `accept_negotiation` NÃO dispara boleto diretamente — apenas grava o
  evento. O dispatch fica no Estágio 6 e é orquestrado pelo agente
  (próxima tool chamada)

---

## Estágio 6: Backend Dispatch Endpoint (SSE)

**Repo**: `quanttix_backend`
**Bloqueia**: ciclo completo de fechamento
**Depende de**: Estágio 5
**Status**: NÃO INICIADO

### Objetivo
Endpoint REST que recebe pedido de simulação (boleto, retorno, baixa) do
agente ou do frontend, dispara o DAG Airflow correspondente, monitora
status e publica eventos via SSE. **Guardrail entre o agente e o Airflow.**
(Design já discutido na sessão — Opção B com SSE.)

### Entregáveis
- `src/dispatch/router.py` (FastAPI routes)
- `src/dispatch/airflow_client.py` (HTTP client para Airflow REST API)
- `src/dispatch/job_store.py` (Redis-backed job state)
- `src/dispatch/poller.py` (background task que poll do Airflow)
- `src/dispatch/sse_events.py` (publish/subscribe via Redis pubsub)
- `tests/dispatch/test_router.py`
- `tests/dispatch/test_poller.py`

### Subtarefas
- [ ] `POST /api/v1/simulation/dispatch` — valida payload, gera `job_id`,
      grava em Redis com TTL 1h, chama Airflow `POST /dags/{dag}/dagRuns`,
      retorna `{job_id, status: "QUEUED"}`
- [ ] Schema do dispatch: `{type: "boleto"|"retorno"|"baixa", conf: {...},
      tenant_id, idempotency_key?}`
- [ ] Mapeamento `type → dag_id`:
      `boleto → sim_cobranca_boleto`,
      `retorno → sim_retorno_bancario`,
      `baixa → sim_baixa_erp`
- [ ] Airflow client com auth Basic (user/pass do env)
- [ ] Background poller: para cada job ativo, poll do Airflow a cada 5s,
      publica eventos no canal Redis `sim:job:{job_id}`
- [ ] Eventos SSE: `queued`, `running`, `step_done`, `done`, `error`
- [ ] `GET /api/v1/simulation/jobs/{job_id}` — retorna estado atual
- [ ] `GET /api/v1/simulation/jobs/{job_id}/events` — SSE stream
- [ ] Idempotência: mesmo `idempotency_key` retorna o mesmo `job_id`
- [ ] Teste: dispatch dispara Airflow (mock do client)
- [ ] Teste: poller publica eventos corretos
- [ ] Teste: SSE drena os eventos do Redis
- [ ] Teste: idempotência

### Contrato do endpoint

```http
POST /api/v1/simulation/dispatch
Authorization: Bearer <service_token>
X-Tenant-Id: <tenant>

{
  "type": "boleto",
  "conf": {
    "title_ids": ["SE1-001"],
    "tipo_titulo": "AR",
    "desconto_pct": 5.0,
    "banco_portador": "341",
    "novo_vencimento": "2026-07-31"
  },
  "tenant_id": "tnt_xxx",
  "idempotency_key": "neg-abc123-boleto"
}
→ 202 Accepted
{ "job_id": "sim_boleto_abc123", "status": "QUEUED" }
```

### Critério de aceite
- Dispatch dispara o DAG correto no Airflow (verificável via Airflow UI)
- SSE stream chega ao consumidor com eventos em ordem
- Idempotência testada
- Erros do Airflow (DAG não existe, conf inválida) viram evento `error`
  no SSE com mensagem legível

### Notas de implementação
- Não bloquear o request do dispatch esperando o Airflow — sempre async
- TTL do job no Redis: 1h em estado ativo, 24h após `done/error`
- Poller usa `asyncio.Task` por job — limitar concorrência se necessário
- Airflow REST API: `POST /api/v1/dags/{dag_id}/dagRuns` com auth Basic

---

## Estágio 7: Handoff API + Session binding (Redis compartilhado)

**Repo**: `quanttix_claw` + `quanttix_backend` + `quanttix_ai`
**Bloqueia**: Estágio 8
**Depende de**: Estágios 5 e 6
**Status**: NÃO INICIADO

### Objetivo
Dois sub-problemas conectados:

1. **Handoff**: o `quanttix_ai` (orquestrador interno) decide iniciar
   negociação de um título → entrega ao `quanttix_claw` (agente externo)
   com contexto completo + `chat_id` de destino. Substitui o caminho
   antigo onde o `quanttix_ai` falava direto com o contraparte via
   Telegram (a "versão pobre" referida pelo usuário).

2. **Session binding**: o `quanttix_claw` mantém `chat_id ↔ negociacao_id`
   no Redis para que cada resposta do contraparte seja interpretada no
   contexto certo (qual título, qual rodada, qual última proposta).

### Entregáveis
- `quanttix_ai/llm_api/services/handoff_client.py` (cliente que chama claw)
- `extensions/quanttix-negotiation/src/handoff_listener.ts` (escuta pub/sub)
- `extensions/quanttix-negotiation/src/session_binding.ts` (lookup pre-LLM)
- `src/mcp/tools/write/request_negotiation_start.py` (backend, ponte)
- `src/mcp/tools/read/get_active_negotiation_for_chat.py` (backend)
- `src/mcp/tools/write/bind_chat_to_negotiation.py` (backend)
- `src/mcp/tools/write/unbind_chat.py` (backend)
- `tests/integration/test_handoff_flow.py`

### Subtarefas
- [ ] Definir contrato do handoff (ver "Contrato — Handoff" abaixo)
- [ ] Decidir mecanismo de transporte do handoff:
  - opção A: HTTP POST direto do `quanttix_ai` → endpoint REST no claw
    (precisa de gateway HTTP custom dentro do OpenClaw)
  - opção B: `quanttix_ai` grava em canal Redis pub/sub
    `negotiation:start`; extensão claw escuta e age
  - opção C: tool MCP `request_negotiation_start` chamada pelo
    `quanttix_ai` contra o backend; o backend publica no Redis;
    claw escuta o mesmo canal
  - **Recomendação**: opção C — passa pelo MCP, mantém o backend como
    hub central, centraliza auth + audit, evita acoplamento direto
- [ ] Implementar tool MCP `request_negotiation_start(title_id,
      counterpart_doc, channel, chat_id, instructions?)` no backend
      (valida com guardrail antes de publicar)
- [ ] Implementar `handoff_listener.ts` no extension — subscreve canal
      Redis `negotiation:start` e dispara a primeira mensagem ao contraparte
- [ ] Ao receber evento: criar binding (`bind_chat_to_negotiation`),
      buscar contexto via MCP (title + counterpart + policy), enviar
      primeira mensagem via canal Telegram do OpenClaw
- [ ] Layout de chaves Redis:
  - `chat:tg:{chat_id} → {negociacao_id, counterpart_doc, bound_at}`
  - `negociacao:{negociacao_id}:chat → {channel, chat_id}`
  - canal pub/sub `negotiation:start` (handoff de entrada)
  - canal pub/sub `negotiation:end:{negociacao_id}` (encerramento)
- [ ] Tool MCP `bind_chat_to_negotiation(chat_id, negociacao_id, counterpart_doc)`
- [ ] Tool MCP `get_active_negotiation_for_chat(chat_id) -> NegotiationContext | None`
- [ ] Tool MCP `unbind_chat(chat_id, reason)` (no fechamento da negociação)
- [ ] Hook OpenClaw pre-LLM — em toda mensagem nova, busca contexto via
      `get_active_negotiation_for_chat` e injeta no system prompt
- [ ] TTL: binding expira em 7 dias se sem atividade
- [ ] **Migração do `quanttix_ai`**: a função `start_negotiation()` do
      `NegotiationOrchestrator` muda — em vez de chamar `TelegramService`
      direto, chama `handoff_client.request_start()` que internamente
      invoca a tool MCP `request_negotiation_start`. Manter o caminho
      antigo atrás de flag `USE_LEGACY_NEGOTIATION=true` por uma versão
      para rollback rápido
- [ ] Teste: bind cria as duas chaves Redis consistentes
- [ ] Teste: get retorna None para chat não vinculado
- [ ] Teste E2E handoff: `quanttix_ai` dispara → claw recebe → manda 1ª msg
- [ ] Teste: TTL renova ao chegar nova mensagem do contraparte
- [ ] Teste: unbind quando negociação fecha (status terminal)
- [ ] Teste: flag legacy desliga handoff novo e usa caminho antigo

### Contrato — Handoff payload

```json
{
  "negociacao_id": "neg_abc123",
  "title_id": "SE1-001",
  "tipo_titulo": "AR",
  "counterpart_doc": "12.345.678/0001-99",
  "counterpart_nome": "Cliente Exemplo S.A.",
  "channel": "telegram",
  "chat_id": "987654321",
  "instructions": {
    "tone": "cordial",
    "max_desconto_pct_authorized": 10.0,
    "expiracao_em_horas": 48,
    "mensagem_inicial_sugerida": "..."
  }
}
```

### Critério de aceite
- Frontend pede negociação → `quanttix_ai` escolhe título → claw envia
  primeira mensagem ao contraparte automaticamente
- Mensagem nova no Telegram chega ao LLM com contexto enriquecido
  (título, histórico, policy)
- Trocar de chat não vaza contexto entre conversas
- Encerrar negociação remove o binding automaticamente
- Flag `USE_LEGACY_NEGOTIATION=true` retorna ao caminho antigo

### Notas de implementação
- OpenClaw mantém histórico de mensagens nativo; o session binding é
  COMPLEMENTAR — "memória de negócio" ≠ "memória de conversa"
- Race condition: 2 mensagens do mesmo chat em <1s — usar lock distribuído
  no Redis (chave `lock:chat:{chat_id}`, TTL 5s) ou aceitar best-effort
  com idempotency_key
- O `quanttix_ai` **NÃO** chama mais a Telegram API direto após esta etapa
  — todo tráfego com contraparte passa pelo claw
- O `NegotiationOrchestrator` antigo do `quanttix_ai` vira fallback,
  não primário

---

## Estágio 8: Allowlist dinâmica Telegram + testes E2E

**Repo**: `quanttix_claw`
**Bloqueia**: ir pra produção (mesmo simulada)
**Depende de**: Estágios 1-7
**Status**: NÃO INICIADO

### Objetivo
Hoje OpenClaw aceita Telegram só de `allowFrom: [chat_id_fixo]`. Para o
agente atender múltiplos contrapartes, a allowlist precisa ser dinâmica:
quando uma negociação é criada, o `chat_id` do contraparte entra na lista;
ao encerrar, sai.

### Entregáveis
- `extensions/quanttix-negotiation/src/allowlist_manager.ts`
- `extensions/quanttix-negotiation/src/telegram_hooks.ts`
- `tests/quanttix-negotiation/test_allowlist.ts`
- `tests/e2e/test_full_negotiation_flow.py` (orquestra todos os repos)

### Subtarefas
- [ ] Decidir mecanismo: (a) atualizar `channels.telegram.accounts.default.allowFrom`
      via `openclaw config set` em runtime; (b) substituir allowlist nativo por
      hook que consulta Redis. Recomendação: (b) — mais responsivo
- [ ] Implementar hook `beforeMessageRouting` que consulta
      `allowlist:negotiation:{chat_id}` no Redis
- [ ] Ao criar negociação no backend, publica `chat_id` na allowlist Redis
- [ ] Ao fechar negociação (aceita/recusada/expirada), remove
- [ ] Teste unit: mensagem de chat não vinculado é rejeitada
- [ ] Teste E2E: ciclo completo
  - Iniciar negociação no backend
  - Enviar mensagem do "cliente" (script de teste) via Telegram
  - Verificar que agente respondeu com proposta dentro da policy
  - Cliente responde "aceito"
  - Verificar que evento `ACEITA` foi gravado
  - Verificar que dispatch do boleto foi chamado
  - Verificar que SSE emitiu eventos do DAG
  - Verificar que agente mandou linha digitável ao cliente
- [ ] Documentar runbook de teste manual em `tests/e2e/RUNBOOK.md`

### Critério de aceite
- Teste E2E roda do início ao fim sem intervenção manual em CI/local
- Allowlist dinâmica não impacta performance (latência adicional < 50ms)
- Documentação clara de como adicionar/remover contraparte manualmente
  em caso de incidente

### Notas de implementação
- Bot de teste do Telegram pode usar segunda conta para simular cliente
- Para CI, mockar o Telegram Bot API (não chamar de verdade)
- Considerar logging detalhado neste estágio — vai ser onde a maioria
  dos bugs aparece em integração

---

## Apêndice A — Decisões de arquitetura (CONFIRMADAS em 2026-05-10)

1. **Bot Telegram — dois bots separados, funções complementares.**
   - `@Quanttix_bot` (`quanttix_ai`): canal **interno**, conversa com o
     gestor financeiro do SaaS para receber instruções e mostrar status.
     **Não** fala com o contraparte.
   - `@Quanttix_Negotiator_bot` (`quanttix_claw`): canal **externo**,
     fala diretamente com o contraparte (cliente/fornecedor). É o agente
     autônomo deste plano.
   - **Fluxo**: Frontend → `quanttix_ai` (orquestrador, seleção do título)
     → handoff via MCP → `quanttix_claw` (conduz a negociação real).
   - Detalhe do handoff: Estágio 7.

2. **MCP server — embarcado no FastAPI do backend, rota `/mcp`.**
   - Auth obrigatória: bearer token por cliente + IP allowlist.
   - Clientes autorizados: apenas `quanttix_ai` e `quanttix_claw`.
   - Subtarefa específica de implementação de auth está no Estágio 3.

3. **Policy YAML — global, com `tenant_id` reservado.**
   - Draft inicial criado em `policy.draft.yaml` (nesta pasta):
     - AR: desconto máx 10%, sem parcelamento (`max_parcelas=1`)
     - AP: desconto máx 10%, sem parcelamento (`max_parcelas=1`)
     - Threshold de escalação humana: 7% (acima → aprovação manual)
   - Draft serve de seed para o Estágio 1. Estrutura final do schema pode
     evoluir, mas valores iniciais são esses.

4. **LLM do agente — Gemma 4 E2B (`quanttix-planner` @ 8091).**
   - GPU constrained → Gemma é leve e roda local em llama.cpp porta 8091.
   - O `quanttix_ai` continua com **Qwen3-32B (porta 8083)** como LLM
     principal do ciclo orquestrador — é o "cérebro interno".
   - O agente externo (claw) usa o modelo mais leve por design e contexto
     de uso (turnos curtos de Telegram, baixa latência preferida).

5. **Audit log — parcimonioso, dois níveis (sem tabela nova).**
   - **Operacional (Redis, TTL curto)**: toda chamada de tool e decisão
     de guardrail. Chave `audit:tool:{client_id}:{session}:{ts}`, TTL 7d.
     Para debug e investigação rápida; **não vai pro data lake**.
   - **Durável (Iceberg, anexado)**: NÃO criar `quanttix.audit.*`. Anexar
     `guardrail_motivo` e `regra_aplicada_id` na coluna do evento que já
     existe em `quanttix.simulation.negociacao.evento`. Uma linha por
     evento material (PROPOSTA/CONTRAPROPOSTA/ACEITA/RECUSADA/EXPIRADA/
     ESCALADA/BLOQUEADA).
   - **Métricas (Prometheus)**: contadores agregados para alarmar sem
     consultar a tabela. Detalhe no Estágio 4.

## Apêndice B — Arquivos da sessão anterior já criados (pré-requisitos)

Estes arquivos foram criados em sessões anteriores e SÃO pré-requisitos
para o ciclo completo funcionar. Não precisa recriar; apenas verificar
que estão commitados:

- `quanttix_data_eng/dags/simulation/sim_cobranca_boleto.py`
- `quanttix_data_eng/dags/simulation/sim_retorno_bancario.py`
- `quanttix_data_eng/dags/simulation/sim_baixa_erp.py`
- `quanttix_data_eng/scripts/simulation/sim_*.py` (6 scripts)
- `quanttix_data_eng/config/schemas/simulation/*.py` (5 schemas)
- `quanttix_data_eng/scripts/trusted/negociacao/*.py`
- `quanttix_data_eng/scripts/refined/negociacao/*.py`

Branch: `developer_flow` (untracked). **Commitar antes de iniciar Estágio 6.**

## Apêndice C — Glossário rápido

- **AR**: Accounts Receivable (contas a receber, títulos de cliente)
- **AP**: Accounts Payable (contas a pagar, títulos de fornecedor)
- **CNAB**: padrão Febraban de troca de arquivos com bancos
- **DDA**: Débito Direto Autorizado
- **MCP**: Model Context Protocol (Anthropic)
- **Borderô**: lote de boletos enviado ao banco para registro
- **Tier**: classificação do contraparte (vip/padrão/risco)
- **Tenant**: empresa cliente do SaaS Quanttix
