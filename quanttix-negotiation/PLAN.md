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
| 3 | Endpoints REST — Tools de leitura | NÃO INICIADO |
| 4 | Guardrails — validação determinística | NÃO INICIADO |
| 5 | Endpoints REST — Tools de escrita + audit | NÃO INICIADO |
| 6 | Backend Dispatch Endpoint (SSE) | NÃO INICIADO |
| 7 | Handoff (HTTP localhost) + Session binding (Redis) | NÃO INICIADO |
| 8 | Allowlist dinâmica Telegram + testes E2E | NÃO INICIADO |

---

## Estágio 1: Política de Negociação (Playbook)

**Repo**: `quanttix_backend`
**Bloqueia**: Estágios 2, 3, 4, 5
**Depende de**: nada
**Status**: NÃO INICIADO

### Objetivo
Codificar em YAML as regras de engajamento (faixas de desconto, prazo,
thresholds de escalação, bloqueios) que o agente consulta antes de propor
termos. A política é dado; a engine de lookup é código Python. Hospedada
no backend para que, em versões futuras, possa evoluir para um painel de
gestão (CRUD da policy, versionamento, override por caso, aprovação
humana de alçada) sem mudar a fonte de verdade.

### Entregáveis
- `src/treasury/services/negotiation_policy/__init__.py`
- `src/treasury/services/negotiation_policy/policy_schema.py` (Pydantic v2)
- `src/treasury/services/negotiation_policy/policy_engine.py` (load + lookup)
- `src/treasury/services/negotiation_policy/README.md` (como editar)
- `src/treasury/config/negotiation_policy.yaml` (cópia operacional)
- `tests/treasury/test_negotiation_policy_schema.py`
- `tests/treasury/test_negotiation_policy_engine.py`

A versão **editorial** do YAML continua em
`quanttix_claw/quanttix-negotiation/policy.draft.yaml` (humanos editam
e revisam aqui). Quando alinhada, é promovida para a cópia operacional
no backend.

### Subtarefas
- [ ] Schema Pydantic v2 (`policy_schema.py`):
  - `PolicyRule` com: `id`, `descricao`, `tipo_titulo (AR|AP)`,
    `tier (str|None)`, `valor_min`, `valor_max (None=infinito)`,
    `max_desconto_pct`, `max_parcelas`, `max_prazo_dias_extra`,
    `juros_mensal_min_pct`, `escalation_threshold_pct`,
    `bloqueio_score_min`, `bloqueio_em_protesto`
  - `PolicyDefaults` com `expiracao_proposta_horas`,
    `max_propostas_por_dia_por_contraparte`,
    `max_negociacoes_simultaneas_global`, `rodada_maxima`
  - `Policy` (root) com `version`, `tenant_id (None)`, `rules`, `defaults`
  - Validators: `0 <= max_desconto_pct <= 100`,
    `escalation_threshold_pct <= max_desconto_pct`, rule ids únicos
- [ ] Engine (`policy_engine.py`):
  - `load_policy(path: Path) -> Policy` (yaml.safe_load + Pydantic parse)
  - Método `Policy.lookup(title_meta, counterpart_meta) -> PolicyDecision`
  - Hierarquia de match: regra com tier específico vence regra sem tier;
    regra com `valor_max` finito vence regra sem teto (dentro da faixa);
    desempate por ordem de declaração no YAML
- [ ] Promover YAML operacional para `src/treasury/config/negotiation_policy.yaml`
      com os 4 cenários (AR-vip 5%, AR-padrao 10%, AP-estrategico 5%, AP-padrao 10%)
- [ ] Teste: policy válida carrega sem erro
- [ ] Teste: policy inválida (`max_desconto > 100`) falha com mensagem legível
- [ ] Teste: `lookup` escolhe `AR-vip` quando `counterpart.tier=vip`
- [ ] Teste: `lookup` cai em `AR-padrao` quando `counterpart.tier=padrao`
- [ ] Teste: `lookup` escolhe `AP-estrategico` quando `tier=estrategico`
- [ ] Teste: `bloqueio_em_protesto=True` (AR-vip ou AR-padrao) retorna
      `PolicyDecision(allowed=False)`
- [ ] Teste: `policy.defaults.rodada_maxima` acessível e tipado
- [ ] Documentar formato em `negotiation_policy/README.md` com exemplos

### Contrato — PolicyDecision

```python
@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    max_desconto_pct: Decimal | None       # None se allowed=False
    max_parcelas: int | None
    max_prazo_dias_extra: int | None
    juros_mensal_min_pct: Decimal | None
    escalation_threshold_pct: Decimal | None  # acima disso, escalar
    bloqueio_motivo: str | None             # preenchido se allowed=False
    matched_rule_id: str                    # id da regra (audit)
```

O caller usa o decision assim:

```python
policy = load_policy(path)
decision = policy.lookup(title_meta, counterpart_meta)

if not decision.allowed:
    return reject(decision.bloqueio_motivo)
if proposed_desconto > decision.max_desconto_pct:
    return error("POLICY_VIOLATION")
if proposed_desconto > decision.escalation_threshold_pct:
    return escalate_to_human(...)
return proceed(...)
```

Os defaults globais (não dependem do lookup) são acessados via
`policy.defaults.expiracao_proposta_horas`, `policy.defaults.rodada_maxima`, etc.

### Critério de aceite
- `pytest tests/treasury/test_negotiation_policy_*` passa
- 4 cenários AR/AP com tiers exemplificados no YAML operacional
- Edição manual quebrada acusa erro Pydantic legível (não stack trace cru)
- `lookup` é determinístico: mesma entrada → mesma `matched_rule_id`

### Notas de implementação
- Pydantic v2 (já usado no backend)
- Não codar regras no Python — apenas a engine. YAML é a fonte
- `tenant_id=None` por enquanto; preparação pra multi-tenant
- `escalation_threshold_pct <= max_desconto_pct` é invariante validado
- O módulo `negotiation_policy/` é candidato natural a painel admin
  futuro (CRUD, versionamento, alçada). A separação engine/dado já facilita
- O endpoint REST `GET /api/v1/tesouraria/negotiation/policy` do Estágio 3
  é apenas uma thin facade sobre esta engine

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

## Estágio 3: Endpoints REST — Tools de leitura

**Repo**: `quanttix_backend`
**Bloqueia**: Estágios 5, 8
**Depende de**: Estágios 1 e 2
**Status**: NÃO INICIADO

### Objetivo
Expor as capacidades de **consulta** ao agente como endpoints REST
no router existente do backend. Tanto `quanttix_ai` quanto `quanttix_claw`
consomem via HTTPS + JWT (mesmo padrão já usado em
`quanttix_api_client.py`). Sem efeitos colaterais — apenas leitura.

### Por que REST
Topologia atual: backend roda em private network (VPC), `quanttix_ai` e
`quanttix_claw` rodam juntos no Server X (POD com GPUs). A comunicação
cross-zone já passa por HTTPS + JWT service account — duplicar isso com
outro protocolo não traz ganho de segurança. Seguimos REST (Django Ninja,
mesmo padrão dos endpoints `treasury/api_*`).

### Entregáveis
- `src/treasury/api_negotiation.py` (router Ninja, rotas read)
- `src/treasury/services/negotiation_service.py` (queries)
- `src/treasury/schemas/negotiation.py` (Pydantic responses)
- `tests/treasury/test_api_negotiation_read.py`

### Subtarefas
- [ ] Criar router `api_negotiation.py` mountado em
      `/api/v1/tesouraria/negotiation/` (segue convenção de `api_cnab.py`)
- [ ] Endpoint `GET /titles/{title_id}` → `TitleDetails`
- [ ] Endpoint `GET /counterparts/{documento}` → `CounterpartProfile`
- [ ] Endpoint `GET /policy?title_id&counterpart_doc` → `PolicyDecision`
      (chama policy engine do Estágio 1 — importa do pacote
      `extensions/quanttix-negotiation/policy/` ou copia como lib)
- [ ] Endpoint `GET /open-negotiations?counterpart_doc=...` →
      `list[OpenNegotiation]` (lê de `quanttix.simulation.negociacao.evento`)
- [ ] Autenticação — modelo restritivo:
  - JWT service account (já existe `_extract_tenant_context` em `treasury/api.py`)
  - Tokens distintos por cliente: contas `svc_quanttix_ai` e
    `svc_quanttix_claw` com permissões mínimas (leitura nos endpoints
    de negociation + escrita no Estágio 5)
  - Log de toda chamada com `client_id` (audit operacional via Redis,
    Estágio 4) — recurso já existe via `_get_client_ip` + tenant context
  - Rate limit por `client_id` (separado do rate limit por contraparte)
- [ ] Schemas Pydantic alinhados com os contratos abaixo
- [ ] Teste por endpoint com fixture de DB
- [ ] Teste de integração: `httpx.AsyncClient` autenticado via JWT
- [ ] Documentar endpoints em `docs/api/negotiation.md` + OpenAPI auto-gen

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
- `pytest tests/treasury/test_api_negotiation_read.py` passa
- OpenAPI lista os 4 endpoints em `/api/v1/docs`
- Chamar via `quanttix_api_client.py` autenticado funciona (smoke test)
- Latência de cada endpoint < 300ms em ambiente de dev

### Notas de implementação
- Para `GET /titles/{id}` e `GET /counterparts/{doc}`, fonte primária são
  as tabelas Iceberg via Trino (já existe acesso em `src/treasury/`)
- Cuidado com cache: dados de título mudam (juros calculados na hora)
- `GET /open-negotiations` lê de `quanttix.simulation.negociacao.evento`
  e agrega por `negociacao_id` (último evento vence)
- O cliente do lado `quanttix_ai` estende `quanttix_api_client.py`;
  o cliente do lado `quanttix_claw` é um plugin TS novo
  (`extensions/quanttix-negotiation/src/client.ts`) usando `fetch`+JWT

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
- `src/treasury/services/guardrails/policy_check.py`
- `src/treasury/services/guardrails/rate_limit.py`
- `src/treasury/services/guardrails/audit_ops.py` (Redis transient, TTL 7d)
- `src/treasury/services/guardrails/audit_event.py` (anexa colunas ao evento)
- `src/treasury/services/guardrails/__init__.py` (decorador `@guardrail`)
- `tests/treasury/test_guardrails.py`

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

## Estágio 5: Endpoints REST — Tools de escrita + audit

**Repo**: `quanttix_backend`
**Bloqueia**: Estágio 6 (parcial)
**Depende de**: Estágios 3 e 4
**Status**: NÃO INICIADO

### Objetivo
Expor as **ações** do agente como endpoints REST POST: propor termos,
registrar contraproposta, aceitar, recusar, escalar. Cada um envelopado
pelo guardrail (Estágio 4) e gera evento em
`quanttix.simulation.negociacao.evento`.

### Entregáveis
- Adicionar rotas POST em `src/treasury/api_negotiation.py`
  (mesmo router do Estágio 3, agora com endpoints write)
- `src/treasury/services/negotiation_service.py` ganha métodos de escrita
- `src/treasury/schemas/negotiation.py` ganha request/response schemas
- `tests/treasury/test_api_negotiation_write.py`

### Subtarefas
- [ ] `POST /negotiation/propose` body `{title_id, terms}` →
      `NegotiationEvent` (status `PROPOSTA`)
- [ ] `POST /negotiation/{neg_id}/counterproposal` →
      status `CONTRAPROPOSTA`; valida que existe `PROPOSTA` prévia
- [ ] `POST /negotiation/{neg_id}/accept` → `AcceptResult` (`ACEITA`);
      retorna sinal pro agente saber que dispatch do boleto pode ser
      chamado (Estágio 6 conecta o dispatch)
- [ ] `POST /negotiation/{neg_id}/reject` body `{reason}` →
      `NegotiationEvent` (status `RECUSADA`)
- [ ] `POST /negotiation/{neg_id}/escalate` body `{reason, context}` →
      `EscalationTicket` (cria entrada em fila de aprovação humana)
- [ ] Todos os 5 endpoints envelopados com `@guardrail` (Estágio 4)
- [ ] Idempotência via header `Idempotency-Key`: 2 chamadas com mesma
      key não duplicam evento
- [ ] Teste por endpoint: caminho feliz
- [ ] Teste por endpoint: bloqueado por guardrail (POLICY_VIOLATION etc.)
- [ ] Teste: idempotência
- [ ] Teste: state machine (accept antes de propose retorna 409)

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

## Estágio 7: Handoff (HTTP localhost) + Session binding (Redis)

**Repo**: `quanttix_claw` + `quanttix_ai`
**Bloqueia**: Estágio 8
**Depende de**: Estágios 5 e 6
**Status**: NÃO INICIADO

### Objetivo
Dois sub-problemas conectados, simplificados pela topologia (AI e Claw
no mesmo Server X):

1. **Handoff**: `quanttix_ai` decide iniciar negociação de um título →
   chama o `quanttix_claw` via **HTTP POST localhost** com contexto
   completo + `chat_id` de destino. Substitui o caminho antigo onde
   `quanttix_ai` falava direto via Telegram com o contraparte (versão
   "pobre" referida pelo usuário).

2. **Session binding**: `quanttix_claw` mantém `chat_id ↔ negociacao_id`
   no Redis (local ao Server X) para que cada resposta do contraparte
   seja interpretada no contexto certo (qual título, qual rodada, qual
   última proposta).

### Por que HTTP localhost (e não Redis pub/sub)
AI e Claw rodam no mesmo servidor. Chamada direta `localhost:18789`
é trivial (latência microsegundos), síncrona, simples de depurar e
não exige listener no Claw nem broker pra fanout. Redis fica só pra
state compartilhado.

### Entregáveis
- `quanttix_ai/llm_api/services/handoff_client.py` (httpx client localhost)
- `extensions/quanttix-negotiation/src/handoff_endpoint.ts`
  (HTTP handler exposto pelo OpenClaw gateway)
- `extensions/quanttix-negotiation/src/session_binding.ts` (lookup pre-LLM)
- `extensions/quanttix-negotiation/src/redis_client.ts` (compartilhado)
- `tests/integration/test_handoff_flow.py`

### Subtarefas
- [ ] Definir contrato do handoff (ver "Contrato — Handoff" abaixo)
- [ ] Implementar `handoff_endpoint.ts` no extension —
      `POST http://localhost:18789/negotiation/start` recebe payload e:
      1. valida bearer token (compartilhado localhost only, low-risk)
      2. cria binding `chat_id ↔ negociacao_id` no Redis
      3. busca contexto via REST tools (Estágio 3) para enriquecer prompt
      4. envia primeira mensagem ao contraparte via canal Telegram OpenClaw
- [ ] Implementar `handoff_client.py` no `quanttix_ai` —
      httpx POST contra `http://localhost:18789/negotiation/start`,
      retry com backoff em caso de 5xx
- [ ] Layout de chaves Redis (local ao Server X):
  - `chat:tg:{chat_id} → {negociacao_id, counterpart_doc, bound_at}`
  - `negociacao:{negociacao_id}:chat → {channel, chat_id}`
  - `lock:chat:{chat_id}` (TTL 5s, evita race de mensagens em paralelo)
- [ ] Hook OpenClaw pre-LLM em `session_binding.ts` — antes de invocar o
      LLM em mensagem nova, lê `chat:tg:{chat_id}` do Redis e injeta
      contexto no system prompt (negociacao_id, título, policy, etc.)
- [ ] TTL do binding: 7 dias com renovação a cada mensagem
- [ ] Encerramento: ao receber status terminal (ACEITA/RECUSADA/EXPIRADA),
      limpar `chat:tg:{chat_id}` e `negociacao:{neg_id}:chat`
- [ ] **Migração do `quanttix_ai`**: `start_negotiation()` do
      `NegotiationOrchestrator` muda — em vez de chamar `TelegramService`
      direto, chama `handoff_client.request_start()`. Manter o caminho
      antigo atrás de flag `USE_LEGACY_NEGOTIATION=true` por uma versão
      pra rollback rápido
- [ ] Teste: binding cria as duas chaves Redis consistentes
- [ ] Teste: hook retorna `None` para chat não vinculado
- [ ] Teste E2E handoff: `quanttix_ai` dispara → claw recebe → manda 1ª msg
- [ ] Teste: TTL renova ao chegar nova mensagem do contraparte
- [ ] Teste: encerramento limpa as chaves
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
- `quanttix_ai` **não** chama mais a Telegram API direto após esta etapa
  — todo tráfego com contraparte passa pelo claw
- `NegotiationOrchestrator` antigo do `quanttix_ai` vira fallback,
  não primário
- O endpoint `handoff_endpoint.ts` é exposto apenas em `127.0.0.1` no
  Server X — não precisa de TLS nem allowlist IP, só bearer token
  compartilhado por env (`QUANTTIX_HANDOFF_TOKEN`)
- Comunicação entre AI e Claw NÃO passa pelo backend; o backend só é
  envolvido nas tools (REST + JWT) e no dispatch (Estágio 6)

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
     → handoff via HTTP localhost → `quanttix_claw` (conduz a negociação).
   - Detalhe do handoff: Estágio 7.

2. **Transporte — REST + JWT.**
   - Decisão baseada na topologia: backend roda em private network,
     `quanttix_ai` e `quanttix_claw` rodam no mesmo Server X.
   - Tools de leitura e escrita do agente são endpoints REST no router
     `src/treasury/api_negotiation.py` (mesma convenção dos demais
     `api_*` do treasury), autenticados por JWT service account.
   - Handoff AI → Claw é **HTTP POST localhost** (Estágio 7), sem
     intermediário, sem broker. Latência mínima, debug trivial.
   - A barreira de segurança é JWT + TLS pro backend (cross-zone) e
     bearer token loopback pro handoff (mesmo servidor).

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
- **Borderô**: lote de boletos enviado ao banco para registro
- **Tier**: classificação do contraparte (vip/padrão/risco)
- **Tenant**: empresa cliente do SaaS Quanttix
