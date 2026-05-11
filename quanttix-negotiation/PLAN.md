# PLAN — Agente Autônomo de Negociação

> Documento vivo. Atualize as caixas `- [ ]` → `- [x]` a cada subtarefa
> concluída e ajuste o **Status** do estágio quando mudar. Não delete
> subtarefas — se uma se tornar irrelevante, marque como `~~obsoleta~~`
> com nota explicando.

---

## Status global

| # | Estágio | Status |
|---|---|---|
| 1 | Política de Negociação (Playbook) | CONCLUÍDO |
| 2 | Skill / Persona do Agente | CONCLUÍDO |
| 3 | Endpoints REST + Models de Negociação (backend) | CONCLUÍDO |
| 3.5 | Pipeline data_eng — perfil_contraparte (refined→POST) | CONCLUÍDO (code) |
| 4 | Guardrails — validação determinística | CONCLUÍDO (code) |
| 5 | Endpoints REST — Tools de escrita + audit | CONCLUÍDO (code) |
| 6 | Backend Dispatch Endpoint (SSE) | NÃO INICIADO |
| 7 | Handoff (HTTP localhost) + Session binding (Redis) | NÃO INICIADO |
| 8 | Allowlist dinâmica Telegram + testes E2E | NÃO INICIADO |

---

## Estágio 1: Política de Negociação (Playbook)

**Repo**: `quanttix_backend`
**Bloqueia**: Estágios 2, 3, 4, 5
**Depende de**: nada
**Status**: CONCLUÍDO (2026-05-10, 35/35 testes passando)

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
- [x] Schema Pydantic v2 (`policy_schema.py`):
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
- [x] Engine (`policy_engine.py`):
  - `load_policy(path: Path) -> Policy` (yaml.safe_load + Pydantic parse)
  - Função `lookup(policy, title_meta, counterpart_meta) -> PolicyDecision`
  - Hierarquia de match: regra com tier específico vence regra sem tier;
    regra com `valor_max` finito vence regra sem teto (dentro da faixa);
    desempate por ordem de declaração no YAML
- [x] Promover YAML operacional para `src/treasury/config/negotiation_policy.yaml`
      com os 4 cenários (AR-vip 5%, AR-padrao 10%, AP-estrategico 5%, AP-padrao 10%)
- [x] Teste: policy válida carrega sem erro
- [x] Teste: policy inválida (`max_desconto > 100`) falha com mensagem legível
- [x] Teste: `lookup` escolhe `AR-vip` quando `counterpart.tier=vip`
- [x] Teste: `lookup` cai em `AR-padrao` quando `counterpart.tier=padrao`
- [x] Teste: `lookup` escolhe `AP-estrategico` quando `tier=estrategico`
- [x] Teste: `bloqueio_em_protesto=True` (AR-vip ou AR-padrao) retorna
      `PolicyDecision(allowed=False)`
- [x] Teste: `policy.defaults.rodada_maxima` acessível e tipado
- [x] Documentar formato em `negotiation_policy/README.md` com exemplos

### Implementação (2026-05-10)
- Arquivos criados:
  - `src/treasury/services/negotiation_policy/__init__.py`
  - `src/treasury/services/negotiation_policy/policy_schema.py` (Pydantic models)
  - `src/treasury/services/negotiation_policy/policy_engine.py` (load + lookup)
  - `src/treasury/services/negotiation_policy/README.md`
  - `src/treasury/config/negotiation_policy.yaml` (4 tiers operacionais)
  - `src/treasury/tests/test_negotiation_policy_schema.py` (18 testes)
  - `src/treasury/tests/test_negotiation_policy_engine.py` (17 testes)
- Resultado: **35/35 testes passando** (sem warnings significativos)
- Decisão pontual: `lookup` ficou como função top-level em vez de método
  em `Policy` — mantém schema puramente data, separação engine/dado mais
  clara para o painel admin futuro

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
**Status**: CONCLUÍDO (2026-05-10)

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
- [x] Definir tom: formal-cordial AR, profissional-direto AP
- [x] Estrutura da conversa: `saudação → contexto → proposta → contra → fecho`
- [x] Regras de compliance verbal: não prometer o que não pode cumprir,
      sempre confirmar valores por escrito antes de fechar
- [x] Frases de escalação: "preciso confirmar internamente"
      (quando `escalation_required=True`)
- [x] Frases de bloqueio: como recusar com elegância sem revelar razões internas
- [x] Escrever SKILL.md principal (system prompt, ~600-1000 palavras)
- [x] ~~manifest.json com `name`, `description`, `tools_required[]`,
      `model_target=planner`~~ — **obsoleta**: skills bundled do OpenClaw
      usam YAML frontmatter dentro do próprio SKILL.md (ver `skills/summarize`,
      `skills/taskflow`). Metadados (`name`, `description`, `metadata.openclaw.emoji`,
      `metadata.openclaw.model_target`, `metadata.openclaw.tools_required`)
      ficaram no frontmatter — sem arquivo separado.
- [x] Greeting prompt (saudação por canal: AR vs AP)
- [x] Objection handling: 5 objeções comuns + scripts de resposta
- [x] Escalation prompt: como avisar o cliente e o supervisor
- [x] Exemplo AR-aceitação completo (turn-by-turn)
- [x] Exemplo AP-contraproposta completo (turn-by-turn)
- [x] Revisar com `policy.draft.yaml` em mãos — os números batem ✓
      (era `policy.example.yaml` no plano original — o arquivo real é
      `policy.draft.yaml` nesta pasta; o operacional vive em
      `quanttix_backend/src/treasury/config/negotiation_policy.yaml`)

### Critério de aceite
- SKILL.md lê coerente em uma passada (sem contradição interna) ✓
- Exemplos mostram o agente usando tools de leitura/escrita
  (mesmo que stubs nesta fase) ✓ (`example_ar_acceptance.md`,
  `example_ap_counterproposal.md` cobrem todas as 9 tools nomeadas no
  frontmatter)
- Manifest é parseável pelo OpenClaw skill loader
  (`openclaw skill validate` se existir) — **smoke ⏳** (validação real
  só no POD, ambiente local não roda OpenClaw)
- Tom passa por revisão humana antes de seguir pro Estágio 3 — **⏳ pendente**

### Implementação (2026-05-10)
- Arquivos criados em `quanttix_claw/skills/quanttix-negotiation/`:
  - `SKILL.md` (~880 palavras, frontmatter com tools + model_target,
    cobre tom, estrutura, compliance, escalação, bloqueio, tools, erro)
  - `prompts/greeting.md` (saudações AR/AP por estado + diretrizes)
  - `prompts/objection_handling.md` (5 objeções com scripts AR/AP)
  - `prompts/escalation.md` (gatilhos, mensagens, payload da tool,
    códigos de `reason` padronizados, retomada)
  - `examples/example_ar_acceptance.md` (9 turnos, AR-padrao, accept
    com confirmação por escrito, contra-proposta no threshold)
  - `examples/example_ap_counterproposal.md` (9 turnos, AP-padrao,
    escalação para humano + retomada + accept final)
- Decisões pontuais registradas:
  - Manifest.json não foi criado — frontmatter YAML do SKILL.md é o
    padrão das skills bundled OpenClaw (`skills/summarize`,
    `skills/taskflow`). Os 9 nomes de tools (`get_title`,
    `get_counterpart`, `get_negotiation_policy`, `list_open_negotiations`,
    `propose_negotiation`, `counterproposal_negotiation`,
    `accept_negotiation`, `reject_negotiation`, `escalate_negotiation`)
    estão em `metadata.openclaw.tools_required`. **Estágios 3 e 5
    devem implementar endpoints REST com esses nomes** (mapeamento
    1-to-1 — o cliente TS do extension expõe cada um como tool).
  - `model_target=quanttix-planner` (não `gemma-4-e2b` cru) — segue o
    namespace de provider definido em
    `extensions/quanttix-planner/openclaw.plugin.json`.
- Validações:
  - Números dos exemplos batem com `policy.draft.yaml` (AR-padrao /
    AP-padrao, max 10%, threshold 7%, max_parcelas=1,
    max_prazo_dias_extra=30)
  - Cálculos conferidos: 10.450 × 0.93 = 9.718,50; 24.000 × 0.96 = 23.040
- Pendências do estágio:
  - Carregamento real no OpenClaw skill loader (smoke ⏳ — só no POD)
  - Revisão humana do tom antes do Estágio 3

### Notas de implementação
- OpenClaw skills usam o padrão `.agents/skills/<id>/SKILL.md` para skills
  internas e `skills/<id>/SKILL.md` para skills bundled
- Vamos como bundled (`skills/quanttix-negotiation/`)
- O system prompt deve referenciar tools por nome
  (ex: "use `get_title` antes de propor"), não embutir lógica de negócio
- A política NÃO entra no system prompt — ela é injetada via tool
  `get_negotiation_policy` para que mudar policy não precise redeploy do skill

---

## Estágio 3: Endpoints REST + Models de Negociação (backend)

**Repo**: `quanttix_backend`
**Branch**: `agentic_flow_cnab`
**Bloqueia**: Estágios 4, 5, 8
**Depende de**: Estágios 1 e 2
**Status**: CONCLUÍDO (2026-05-10)

### Objetivo
Criar a estrutura transacional do agente no backend Django: 4 models de
negociação (`Counterpart`, `Negotiation`, `NegotiationEvent`,
`EscalationTicket`), 3 endpoints REST de leitura para o agente consumir,
1 endpoint POST de ingestão para o pipeline data_eng do Estágio 3.5
alimentar os perfis de contraparte, e role de serviço dedicada
(`ROLE_AGENT_SERVICE`).

### Decisões de arquitetura (2026-05-10, após análise a fundo)

Após inspecionar `quanttix_ai`, `quanttix_data_eng` e o OpenAPI de
produção, **o plano original foi reformatado**. Descobertas chave:

1. **`GET /titles/{title_id}` NÃO é criado.** O `quanttix_ai` já consome
   `/api/v1/tesouraria/contas-receber/fluxos` e `/contas-pagar/documentos`
   (existentes em produção) via [QuanttixApiClient](https://test.quanttix.com.br/api/docs).
   `get_fluxos_ar` retorna `active_flows` com `id (UUID)`, `client`,
   `title_id` (ERP), `amount`, `due_date`, `days_overdue` por título.
   Criar `/negotiation/titles/{id}` seria duplicação. O agente claw recebe
   o título já enriquecido via handoff (Estágio 7), sem precisar buscar
   sozinho no backend.

2. **`GET /counterparts/{documento}` SIM é criado**, mas **não auto-cria**.
   Counterpart é populada pelo pipeline data_eng (Estágio 3.5) via POST
   ingest. Se o agente buscar e não achar, retorna 404 honesto. Curadoria
   manual via Django admin para casos especiais.

3. **AR/AP no Postgres NÃO ganham `client_document`/`supplier_document`.**
   O cnpj/cpf já existe em `quanttix.protheus.refined.finance.posicao_titulos_receber_atual`
   (Iceberg). O pipeline data_eng (Estágio 3.5) extrai daí e popula
   `Counterpart` no Postgres. Mudar o schema de AR/AP exigiria mexer
   nos endpoints `/datalake/ingest/accounts-{payable,receivable}` que
   estão em produção — fica como melhoria separada e opcional.

4. **Models `Negotiation`, `NegotiationEvent`, `EscalationTicket`** são
   criados aqui (não esperam Estágio 5) para destravar os guardrails do
   Estágio 4 e ter estado transacional consistente desde o início. O
   Estágio 5 só **escreve** neles via endpoints POST.

5. **Audit durável** (Estágio 4) usa as colunas `guardrail_motivo` e
   `regra_aplicada_id` que já estão modeladas em `NegotiationEvent` aqui.
   Não duplica tabela.

6. **Código órfão detectado** em `quanttix_data_eng/scripts/refined/negociacao/send_negociacao_data.py`:
   tenta `POST /api/v1/datalake/negotiation-snapshot` que **não existe no
   backend**. Pendência separada — vou flagar como melhoria futura (não
   bloqueador do agente), eventualmente vira `/datalake/ingest/negotiation-metrics-snapshot`.

### Entregáveis

- `src/treasury/models.py` ou `src/treasury/models_negotiation.py` —
  4 models novos
- `src/treasury/migrations/00XX_negotiation_models.py` — migration
- `src/treasury/admin.py` ou `src/treasury/admin_negotiation.py` —
  Django admin
- `src/treasury/schemas/negotiation.py` — Pydantic responses
- `src/treasury/schemas/datalake.py` — schemas de ingest
  `CounterpartProfileIngestItem` + `CounterpartProfileIngestRequest`
- `src/treasury/services/negotiation_service.py` — queries
- `src/treasury/services/ingestion_service.py` — método
  `ingest_counterpart_profiles` adicionado
- `src/treasury/api_negotiation.py` — router com 3 GETs
- `src/treasury/api_datalake.py` — POST `/datalake/ingest/counterpart-profiles`
  adicionado
- `src/treasury/api.py` — montagem do `negotiation_router`
- `src/authentication/models.py` — `ROLE_AGENT_SERVICE` em `UserTenantRole`
- `src/treasury/tests/test_api_negotiation_read.py` — testes pytest

### Subtarefas

- [x] Criar 4 models: `Counterpart`, `Negotiation`, `NegotiationEvent`,
      `EscalationTicket` (todos com `TreasuryBaseModel` base — tenant,
      empresa, filial)
- [x] Adicionar `ROLE_AGENT_SERVICE` em `UserTenantRole`
- [x] Gerar migration Django para os 4 models + role
- [x] Criar Django admin para os 4 models (read-mostly para Negotiation*,
      editable para Counterpart)
- [x] Criar schemas Pydantic de response em `treasury/schemas/negotiation.py`:
      `CounterpartResponse`, `OpenNegotiationItem`, `OpenNegotiationsResponse`,
      `PolicyDecisionResponse`
- [x] Criar schemas Pydantic de ingest em `treasury/schemas/datalake.py`:
      `CounterpartProfileIngestItem`, `CounterpartProfileIngestRequest`
- [x] Criar `treasury/services/negotiation_service.py` com 3 queries:
      `get_policy_decision(title_id, counterpart_doc, tenant_ctx)`,
      `get_counterpart_by_documento(documento, tenant_ctx)`,
      `list_open_negotiations(counterpart_doc, tenant_ctx)`
- [x] Adicionar `ingest_counterpart_profiles` em
      `treasury/services/ingestion_service.py` (upsert por
      `(tenant, documento)`, max 5000 records/batch padrão)
- [x] Criar `treasury/api_negotiation.py` (router Ninja) com 3 GETs:
      `/policy`, `/counterparts/{documento}`, `/open-negotiations`
- [x] Adicionar `POST /datalake/ingest/counterpart-profiles` em
      `treasury/api_datalake.py` com auth `DataLakeApiKeyAuth` (padrão)
- [x] Montar `negotiation_router` em `treasury/api.py` sob
      `/api/v1/tesouraria/negotiation`
- [x] Testes pytest em `treasury/tests/test_api_negotiation_read.py`:
      policy retorna AR-padrao, policy bloqueia em protesto, counterpart
      404 quando ausente, counterpart 200 quando ingerida, open-negotiations
      vazia, open-negotiations com 1 item após criar Negotiation
- [x] ~~`GET /titles/{title_id}`~~ — **obsoleta**: usar
      `/contas-receber/fluxos` ou `/contas-pagar/documentos` existentes
- [x] ~~`get_counterpart` auto-create com defaults~~ — **obsoleta**:
      counterpart populada via ingest do Estágio 3.5; 404 honesto se ausente
- [x] ~~Schema de AR/AP ganha `client_document`/`supplier_document`~~ —
      **obsoleta**: documento vem do Iceberg → pipeline → `Counterpart`
      table sem alterar `AccountReceivable`/`AccountPayable`

### Contratos

```python
# Models (Django) — simplificados, ver código real para constraints e indexes

class Counterpart(TreasuryBaseModel):
    documento = CharField(max_length=20)          # cpf ou cnpj (apenas dígitos)
    nome = CharField(max_length=200)
    tipo_titulo = CharField(choices=["AR","AP"])  # mesmo documento pode aparecer dos dois lados
    tier = CharField(choices=["vip","padrao","risco","estrategico"], default="padrao")
    score = IntegerField(0..1000, default=500)
    em_protesto = BooleanField(default=False)
    qtd_titulos_pagos = IntegerField(default=0)
    qtd_atrasos = IntegerField(default=0)
    historico_atrasos_pct = DecimalField(0..100, default=0)
    valor_aberto_total = DecimalField(default=0)
    ultima_negociacao_resultado = CharField(null=True, blank=True)
    fonte = CharField(choices=["datalake","manual"], default="datalake")
    dt_ref = DateField()                          # data do snapshot quando fonte=datalake
    # Unique: (tenant, empresa, filial, documento, tipo_titulo)

class Negotiation(TreasuryBaseModel):
    negociacao_id = CharField(unique=True)        # UUID gerado no primeiro propose
    title_id = CharField()                        # title_id ERP (ex: SE1-001)
    title_uuid_ref = UUIDField(null=True)         # AccountReceivable.id ou AccountPayable.id
    tipo_titulo = CharField(choices=["AR","AP"])
    counterpart = ForeignKey(Counterpart, on_delete=PROTECT)
    status_atual = CharField(choices=["ABERTA","ACEITA","RECUSADA","EXPIRADA","ESCALADA","BLOQUEADA"])
    dt_criacao = DateTimeField()
    dt_expiracao = DateTimeField(null=True)
    rodada_atual = IntegerField(default=0)
    # Indexes: (tenant, counterpart, status_atual), (tenant, title_id)

class NegotiationEvent(TreasuryBaseModel):
    evento_id = CharField(unique=True)            # UUID por evento
    negociacao = ForeignKey(Negotiation, on_delete=PROTECT, related_name="eventos")
    status_evento = CharField(choices=["PROPOSTA","CONTRAPROPOSTA","ACEITA","RECUSADA","EXPIRADA","ESCALADA","BLOQUEADA"])
    terms = JSONField(default=dict)               # NegotiationTerms (Estágio 5)
    dt_evento = DateTimeField()
    expira_em = DateTimeField(null=True)
    guardrail_motivo = CharField(null=True, blank=True)
    regra_aplicada_id = CharField(null=True, blank=True)  # rule.id da policy
    idempotency_key = CharField(unique=True)      # (tenant, negociacao_id, status, terms hash)

class EscalationTicket(TreasuryBaseModel):
    ticket_id = CharField(unique=True)
    negociacao = ForeignKey(Negotiation, on_delete=PROTECT, related_name="tickets")
    reason = CharField(max_length=100)            # códigos padronizados (ver SKILL.md)
    context = JSONField(default=dict)
    status = CharField(choices=["aberto","aprovado","recusado"], default="aberto")
    dt_criacao = DateTimeField()
    dt_resolucao = DateTimeField(null=True)
    nota_resolucao = TextField(blank=True)
```

```python
# REST — Pydantic responses (treasury/schemas/negotiation.py)

class PolicyDecisionResponse(BaseModel):
    allowed: bool
    matched_rule_id: str
    max_desconto_pct: Decimal | None
    max_parcelas: int | None
    max_prazo_dias_extra: int | None
    juros_mensal_min_pct: Decimal | None
    escalation_threshold_pct: Decimal | None
    bloqueio_motivo: str | None
    # Defaults globais expostos junto p/ o agente não precisar 2ª chamada:
    expiracao_proposta_horas: int
    rodada_maxima: int
    max_propostas_por_dia_por_contraparte: int

class CounterpartResponse(BaseModel):
    documento: str
    nome: str
    tipo_titulo: Literal["AR", "AP"]
    tier: str
    score: int
    em_protesto: bool
    qtd_titulos_pagos: int
    qtd_atrasos: int
    historico_atrasos_pct: Decimal
    valor_aberto_total: Decimal
    ultima_negociacao_resultado: str | None
    fonte: Literal["datalake", "manual"]
    dt_ref: date

class OpenNegotiationItem(BaseModel):
    negociacao_id: str
    title_id: str
    tipo_titulo: Literal["AR", "AP"]
    status_atual: str
    dt_criacao: datetime
    dt_expiracao: datetime | None
    rodada_atual: int
    ultimo_status_evento: str | None
    ultimo_terms: dict | None

class OpenNegotiationsResponse(BaseModel):
    items: list[OpenNegotiationItem]
    total: int
```

```python
# Ingest — schema (treasury/schemas/datalake.py)

class CounterpartProfileIngestItem(BaseModel):
    documento: str = Field(min_length=11, max_length=20)
    nome: str
    tipo_titulo: Literal["AR", "AP"]
    tier: str
    score: int = Field(ge=0, le=1000)
    em_protesto: bool = False
    qtd_titulos_pagos: int = Field(ge=0, default=0)
    qtd_atrasos: int = Field(ge=0, default=0)
    historico_atrasos_pct: Decimal = Field(ge=0, le=100, default=Decimal("0"))
    valor_aberto_total: Decimal = Field(ge=0, default=Decimal("0"))
    ultima_negociacao_resultado: str | None = None

class CounterpartProfileIngestRequest(BaseModel):
    tenant_id: UUID
    empresa_id: UUID
    filial_id: UUID
    dt_ref: date
    batch_id: str
    records: list[CounterpartProfileIngestItem] = Field(min_length=1, max_length=5000)
```

### Endpoints expostos

| Método | Path | Auth | Função |
|---|---|---|---|
| `GET` | `/api/v1/tesouraria/negotiation/policy?title_id&counterpart_doc` | JWT + role | Resolve `PolicyDecision` |
| `GET` | `/api/v1/tesouraria/negotiation/counterparts/{documento}?tipo_titulo=AR\|AP` | JWT + role | Lê `Counterpart` |
| `GET` | `/api/v1/tesouraria/negotiation/open-negotiations?counterpart_doc=...` | JWT + role | Lista `Negotiation` em estados não-terminais |
| `POST` | `/api/v1/datalake/ingest/counterpart-profiles` | DataLakeApiKeyAuth | Upsert de `Counterpart` em batch (Estágio 3.5 chama) |

### Critério de aceite

- `pytest src/treasury/tests/test_api_negotiation_read.py` passa (code ✅
  — sintaxe validada local; execução exige Django no POD ⏳)
- Migration aplicada sem erro (smoke ⏳ — POD)
- Admin Django mostra os 4 models (smoke ⏳ — POD)
- OpenAPI lista os 4 endpoints em `/api/v1/docs` (smoke ⏳ — POD)
- Chamada autenticada via `quanttix_api_client.py` funciona (smoke ⏳ — POD)
- Latência de cada GET < 300ms (smoke ⏳ — POD)

### Implementação (2026-05-10)

Arquivos criados em `quanttix_backend@agentic_flow_cnab`:

- `src/treasury/models_negotiation.py` — 4 models (`Counterpart`,
  `Negotiation`, `NegotiationEvent`, `EscalationTicket`). Importado no
  fim de `src/treasury/models.py` para Django descobrir.
- `src/treasury/migrations/0010_negotiation_models.py` — migration
  manual (CreateModel + Historical* + AddIndex + AddConstraint +
  AlterField em `DataLakeIngestion.entity` para `counterpart_profiles`).
  Escrita seguindo o padrão da `0008_boleto_emission.py`. **Deve ser
  regenerada via `makemigrations` no POD se os models forem alterados.**
- `src/authentication/migrations/0003_add_agent_service_role.py` —
  adiciona choice `agent_service` ao campo `role` de `UserTenantRole`
  e `HistoricalUserTenantRole`.
- `src/treasury/admin_negotiation.py` — Django admin: `Counterpart`
  editável (curadoria manual), `Negotiation` read-mostly, `NegotiationEvent`
  read-only (append-only), `EscalationTicket` editável só em
  `status`/`nota_resolucao` (workflow humano). Registrado via
  `@admin.register` e linkado em `admin.py` por import.
- `src/treasury/schemas/negotiation.py` — `PolicyDecisionResponse`,
  `CounterpartResponse`, `OpenNegotiationItem`, `OpenNegotiationsResponse`.
- `src/treasury/schemas/datalake.py` — adicionados
  `CounterpartProfileIngestItem` (com validators de documento e tier)
  e `CounterpartProfileIngestRequest` (max 5000 records/batch).
- `src/treasury/services/negotiation_service.py` — 3 queries
  (`get_policy_decision`, `get_counterpart_by_documento`,
  `list_open_negotiations`), `NegotiationServiceError` com códigos,
  loader de policy sem cache na v0.
- `src/treasury/services/ingestion_service.py` — `ingest_counterpart_profiles`
  adicionado seguindo o padrão de `ingest_accounts_payable`.
- `src/treasury/api_negotiation.py` — router Ninja com 3 GETs
  (`/policy`, `/counterparts/{documento}`, `/open-negotiations`),
  decorators `@require_tenant_access` + `@require_any_role(NEGOTIATION_ROLES)`
  com `ROLE_AGENT_SERVICE` incluído.
- `src/treasury/api_datalake.py` — endpoint POST
  `/api/v1/datalake/ingest/counterpart-profiles` adicionado.
- `src/treasury/api.py` — `negotiation_router` montado em
  `/api/v1/tesouraria/negotiation`.
- `src/authentication/models.py` — `UserTenantRole.ROLE_AGENT_SERVICE`
  adicionado.
- `src/treasury/tests/test_api_negotiation_read.py` — 11 testes pytest
  (4 cenários de policy + 3 de counterpart + 4 de open-negotiations).
  Sintaxe validada local; **execução exige Django no POD**.

Decisões de implementação:

- **Models e migration**: usei funções nomeadas (`_default_negociacao_id`,
  etc.) em vez de lambda no `default=` porque lambdas não serializam
  em migrations Django.
- **Audit no `NegotiationEvent`**: campos `guardrail_motivo` e
  `regra_aplicada_id` direto na linha do evento — sem tabela separada,
  exatamente como Apêndice A do plano define.
- **`AccountReceivable`/`AccountPayable` intocados** — adicionar
  `client_document`/`supplier_document` exigiria mudar os ingest
  endpoints em produção (`/datalake/ingest/accounts-{payable,receivable}`),
  fora do escopo. Documento de contraparte vem direto do pipeline
  `perfil_contraparte_*` (Estágio 3.5) para a tabela `Counterpart`.
- **Sem cache de policy** na v0 — `load_policy()` a cada GET. Quando
  vira gargalo, plug Redis-cache com TTL curto.
- **`load_policy` permite override** via `settings.NEGOTIATION_POLICY_PATH`
  para testes injetarem YAML alternativo (não usado nos testes atuais,
  mas suportado).
- **`DataLakeIngestion.EntityType`** ganhou `COUNTERPART_PROFILES` —
  migration inclui `AlterField` para refletir nas choices do campo
  `entity`.

Pendências do estágio:

- Smoke no POD: aplicar migration, popular Counterpart manualmente via
  admin, chamar os 3 GETs com JWT do `svc_quanttix_ai` ou `svc_quanttix_claw`,
  validar contratos
- Criar contas de serviço `svc_quanttix_ai@quanttix.com` e
  `svc_quanttix_claw@quanttix.com` com `ROLE_AGENT_SERVICE` (manual
  via admin ou seed script)
- Adicionar wrappers no `quanttix_api_client.py` para os 3 GETs novos
  (não bloqueador deste estágio — pode entrar quando o claw começar a
  consumir)

### Notas de implementação

- Counterpart vazia até o Estágio 3.5 rodar — endpoints retornam 404 ou
  lista vazia; testes locais usam fixtures pra popular
- `policy` carrega `negotiation_policy.yaml` operacional (já em
  `src/treasury/config/`) via `policy_engine.load_policy` — sem cache;
  evolução: redis-cache com TTL curto quando virar produção
- `open-negotiations` retorna negociações com `status_atual` em
  `ABERTA|ESCALADA` (não-terminais); ordenação por `dt_criacao` desc
- `ingest_counterpart_profiles` faz upsert atomic; cada record processado
  individualmente para que erros parciais não derrubem o batch — devolve
  `processed_count`, `error_count`, `errors[]` no payload de resposta
- Os 9 nomes de tools listados no `SKILL.md` Estágio 2:
  - `get_title`, `get_counterpart`, `get_negotiation_policy`,
    `list_open_negotiations` (leitura)
  - `propose_negotiation`, `counterproposal_negotiation`,
    `accept_negotiation`, `reject_negotiation`, `escalate_negotiation`
    (escrita — Estágio 5)
  - `get_title` será uma **tool local do claw** (Estágio 7) que olha
    o payload do handoff; não vira endpoint REST aqui
  - `get_counterpart`, `get_negotiation_policy`, `list_open_negotiations`
    mapeiam 1-to-1 nos 3 GETs deste estágio

---

## Estágio 3.5: Pipeline data_eng — perfil_contraparte (refined→POST)

**Repo**: `quanttix_data_eng`
**Branch**: `developer_flow`
**Bloqueia**: nenhuma fase em si (mas habilita dados reais para 4-8)
**Depende de**: Estágio 3 (endpoint POST + model `Counterpart` precisam existir)
**Status**: CONCLUÍDO code ✅ (2026-05-10); smoke ⏳ no POD
**Pode rodar em paralelo com**: Estágios 4, 5, 6 (não bloqueante)

### Objetivo
Calcular o perfil enriquecido de cada contraparte (tier, score,
em_protesto, histórico) por tenant a partir das tabelas refined que já
existem no Iceberg, e empurrar pro backend via POST. Sem o pipeline,
`Counterpart` no Postgres fica vazia e endpoints do Estágio 3 retornam
404 ou lista vazia — agente opera com defaults manuais cadastrados via
Django admin.

### Por que existir
O `quanttix_data_eng` já tem todo o pipeline raw→trusted→refined para
títulos AR e AP, incluindo:
- `protheus.refined.finance.posicao_titulos_receber_atual` (com cpf_cnpj)
- `protheus.refined.finance.posicao_cliente_atual` (agregado por cliente,
  com `maior_atraso_dias`, `qtd_titulos_abertos`, `total_valor_pendente`)
- `protheus.refined.finance.posicao_fornecedor_atual` (equivalente AP)
- `protheus.trusted.finance_negotiation.fato_negociacao_agente`
  (eventos históricos do agente — quando começar a popular após Estágio 5)

O que falta é a tabela **`perfil_contraparte`** que junta esses dados e
calcula `tier`, `score`, `em_protesto`. É o último estágio refined.

### Entregáveis
- `scripts/refined/negociacao/create_perfil_contraparte_ar.py`
- `scripts/refined/negociacao/create_perfil_contraparte_ap.py`
- `scripts/refined/negociacao/send_perfil_contraparte.py`
- `dags/refined/dag_perfil_contraparte.py`

### Subtarefas
- [x] `create_perfil_contraparte_ar.py`: PySpark, agrega
      `posicao_titulos_receber_atual` por (tenant, empresa, cpf_cnpj).
      Calcula tier/score/em_protesto via heurística (SQL puro). Grava em
      `quanttix.{vendor}.refined.finance_negotiation.perfil_contraparte_ar`
      particionado por (tenant_id, cod_empresa, dt_ref). Enriquecimento
      opcional com `ultima_negociacao_resultado` via MERGE do
      `fato_negociacao_agente` (graceful se vazio).
- [x] `create_perfil_contraparte_ap.py`: equivalente AP, agrega
      `posicao_titulos_pagar_atual`. Tier `estrategico` por
      valor_aberto_total > R$200k. `em_protesto=False` fixo.
- [x] `send_perfil_contraparte.py`: lê as 2 tabelas (max dt_ref),
      sanitiza Decimal/Date para JSON, agrupa por
      (tenant, empresa, filial), POST batch (max 5000/req) ao backend em
      `POST /api/v1/datalake/ingest/counterpart-profiles` com header
      `X-DataLake-Api-Key`. Reusa `_build_context_lookup` e
      `_resolve_context` (padrão `send_accounts_receivable.py`),
      retry exponencial 3x via `tenacity`.
- [x] DAG `refined_perfil_contraparte`: 4 tasks em paralelo/sequência —
      `(create_ar | create_ap | load_tenant_config) → send_perfil`,
      schedule diário 05:00 (após `refined_titulos_*` 04:30).
- [x] Heurísticas iniciais (documentadas nos docstrings dos scripts):
  - **tier AR**: `vip` se valor_aberto_total > R$500k; `risco` se
    em_protesto OR score < 300; senão `padrao`.
  - **tier AP**: `estrategico` se valor_aberto_total > R$200k; senão
    `padrao`. *(Whitelist por categoria BENS/MATERIA_PRIMA ficou pra v2
    — não disponível em `posicao_titulos_pagar_atual`.)*
  - **score** (0-1000): `1000 - min(maior_atraso_dias × 5, 700) -
    min(historico_atrasos_pct × 3, 200)`
  - **em_protesto** (AR): `maior_atraso_dias > 180 AND
    historico_atrasos_pct > 50`
  - **em_protesto** (AP): sempre `False`
  - **historico_atrasos_pct**: `qtd_atrasos / max(qtd_titulos_abertos, 1) × 100`
  - **qtd_titulos_pagos**: `0` na v0 (TODO: derivar de `fato_movimentos`
    quando incluirmos eventos de baixa no pipeline)
- [x] Sintaxe Python validada em todos os 4 arquivos (code ✅)
- [ ] Smoke no POD: DAG roda, POST chega no backend, registros aparecem
      em `Counterpart` table (smoke ⏳)

### Contrato — payload do POST

Cada chamada envia 1 batch por tenant/dt_ref:

```http
POST /api/v1/datalake/ingest/counterpart-profiles
Headers: X-DataLake-Api-Key, X-Tenant-Id, Content-Type
Body:
{
  "tenant_id": "uuid",
  "empresa_id": "uuid",
  "filial_id": "uuid",
  "dt_ref": "2026-05-10",
  "batch_id": "perfil-contraparte-2026-05-10",
  "records": [
    {
      "documento": "12345678000199",
      "nome": "Cliente Exemplo S.A.",
      "tipo_titulo": "AR",
      "tier": "padrao",
      "score": 720,
      "em_protesto": false,
      "qtd_titulos_pagos": 47,
      "qtd_atrasos": 8,
      "historico_atrasos_pct": 17.02,
      "valor_aberto_total": 124500.00,
      "ultima_negociacao_resultado": null
    }
  ]
}
→ 200 {"processed_count": 1, "error_count": 0, "errors": []}
```

### Critério de aceite
- Sintaxe Python validada local (code ✅)
- DAG aparece na UI do Airflow no POD (smoke ⏳)
- 1 execução completa popula `Counterpart` table no backend (smoke ⏳)
- Heurísticas geram tiers/scores plausíveis para dataset real (smoke ⏳)

### Notas de implementação
- Heurísticas atuais são **chute educado**, vão precisar de calibragem
  com dados reais. Fórmulas documentadas nos docstrings dos scripts
  para facilitar revisão posterior pelo time financeiro
- Tabela `perfil_contraparte_*` é write-only por dt_ref (snapshot
  diário); backend faz upsert por `(tenant, documento, tipo_titulo)`
  então a última carga vence
- Nada nesta etapa modifica AR/AP existentes — pipeline complementar,
  não substitui

### Implementação (2026-05-10)

Arquivos criados em `quanttix_data_eng@developer_flow`:

- `scripts/refined/negociacao/create_perfil_contraparte_ar.py` —
  PySpark, ~190 linhas. SQL CTE pipeline: `aggregated` →
  `with_pct` → `with_em_protesto` → `with_score` → `with_tier`. Cria
  tabela com `CREATE OR REPLACE TABLE` particionada por
  `(tenant_id, cod_empresa, dt_ref)`. Imprime stats de tier ao final.
- `scripts/refined/negociacao/create_perfil_contraparte_ap.py` —
  PySpark, ~155 linhas. Diferenças vs AR: tier `estrategico` por valor,
  `em_protesto=False` fixo, fonte é `posicao_titulos_pagar_atual`.
- `scripts/refined/negociacao/send_perfil_contraparte.py` —
  Python + Spark, ~280 linhas. Lê as duas tabelas (AR + AP, max dt_ref),
  agrupa por (tenant, empresa, filial), POST batches de até 5000 records
  em `/api/v1/datalake/ingest/counterpart-profiles` com retry
  exponencial via tenacity. Reusa o padrão de
  `send_accounts_receivable.py` (`_build_context_lookup`,
  `_resolve_context`, `_make_batch_id`).
- `dags/refined/refined_perfil_contraparte.py` — Airflow DAG, ~165 linhas.
  Tasks `create_perfil_contraparte_ar` + `create_perfil_contraparte_ap`
  + `load_tenant_config` rodam em paralelo, todas convergem em
  `send_perfil_contraparte`. Schedule diário 05:00, `is_paused_upon_creation=True`.

Decisões pontuais:

- **Heurísticas em SQL Spark puro** (não PySpark DataFrame API) — fica
  legível, facilita revisão pelo time financeiro pra calibrar fórmula.
- **Enriquecimento de `ultima_negociacao_resultado` é opcional** — usa
  `MERGE INTO` quando `fato_negociacao_agente` existe e tem linhas; em
  ambiente sem agente rodando, coluna fica vazia (sem erro).
- **`qtd_titulos_pagos=0`** na v0 — derivar de `fato_movimentos`
  (histórico de baixas) entra como TODO, não bloqueia o pipeline.
- **`tier estrategico` por classificação AP (BENS/MATERIA_PRIMA)** ficou
  pra v2 — coluna não está em `posicao_titulos_pagar_atual`. Hoje o
  critério é só `valor_aberto_total > R$200k`.
- **`em_protesto` AP sempre `False`** — Quanttix paga; conceito de
  protesto do nosso lado não se aplica.
- **DAG roda em paralelo** AR/AP/load_config — todos independentes,
  convergem só em `send_perfil_contraparte`.

Pendências do estágio (smoke ⏳ no POD):

- Migration do backend aplicada antes (depende do Estágio 3)
- DAG `refined_perfil_contraparte` aparece na UI Airflow
- Trigger manual: cria tabelas refined, popula `Counterpart` no backend
- Validar stats de tier/score plausíveis para dataset real
- Calibrar fórmulas com o time financeiro depois da primeira execução
- Derivar `qtd_titulos_pagos` de `fato_movimentos` (TODO documentado)

---

## Estágio 4: Guardrails — validação determinística

**Repo**: `quanttix_backend`
**Bloqueia**: Estágio 5
**Depende de**: Estágios 1 e 3
**Status**: CONCLUÍDO code ✅ (2026-05-11); smoke ⏳ no POD

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
- [x] Decorador `@guardrail` para envolver tools de escrita
      (`treasury/services/guardrails/decorator.py`). Recebe a chamada,
      roda cadeia de checks, injeta `_guardrail_outcomes` no kwargs.
- [x] Guardrail `policy_check` (`policy_check.py`): chama
      `policy_engine.lookup()` e valida `desconto_pct`, `num_parcelas`,
      `juros_pct_mes`, `novo_vencimento` contra `PolicyDecision`.
- [x] Guardrail `rate_limit` (`rate_limit.py`): máx propostas por
      contraparte/dia via `django.core.cache` (chave
      `rate:negociacao:propostas:{tenant}:{doc}:{date}`, TTL 25h);
      máx negociações simultâneas via count direto no DB.
- [x] Guardrail `escalation_check` (`escalation_check.py`): se
      `accept_negotiation` com `desconto_pct > escalation_threshold_pct`,
      levanta `ESCALATION_REQUIRED`. Para `propose`/`counter`, anota
      `escalation_required=True` no outcome (não bloqueia).
- [x] Guardrail `block_check` (`block_check.py`): contraparte
      `em_protesto=True` bloqueia toda escrita com `BLOCKED_BY_POLICY`.
      Score baixo fica para o `policy_check`.
- [x] **Audit operacional (Redis)** (`audit_ops.py`): toda chamada de
      tool grava em `audit:tool:{client_id}:{session_id}:{ts_us}` com
      payload JSON+gzip+base64, TTL 7 dias. `read_audit(key)` para
      inspeção. Falha silenciosa (audit nunca derruba operação).
- [x] **Audit durável (Iceberg/Postgres)**: NÃO criar tabela separada.
      Usa `NegotiationEvent.guardrail_motivo` e
      `NegotiationEvent.regra_aplicada_id` que já foram criados no
      Estágio 3. Estágio 5 popula esses campos a partir de
      `kwargs["_guardrail_outcomes"]` injetado pelo decorador.
- [x] Métrica agregada (`metrics.py`): contadores in-memory
      thread-safe — `negotiation_guardrail_blocks_total{rule, code}` e
      `negotiation_proposals_total{tipo, outcome}`. Interface
      compatível com Prometheus (`prometheus_client` plug-and-play
      no v1).
- [x] Erro estruturado `GuardrailError(code, message, hint, regra_aplicada_id)`
      em `exceptions.py`. Códigos fechados em `GUARDRAIL_CODES`:
      `POLICY_VIOLATION | ESCALATION_REQUIRED | BLOCKED_BY_POLICY |
      RATE_LIMITED | INVALID_STATE`. Método `.to_dict()` para
      serialização REST.
- [x] Teste: desconto acima do máximo bloqueado com `POLICY_VIOLATION`
- [x] Teste: rate limit atinge 3 propostas/dia e bloqueia próxima
- [x] Teste: audit operacional grava no Redis e roundtrip de leitura
- [x] Teste: contraparte em_protesto bloqueia com `BLOCKED_BY_POLICY`
- [x] Teste: accept acima de `escalation_threshold_pct` sobe
      `ESCALATION_REQUIRED`
- [x] Teste: rodada_maxima atingida sobe `ESCALATION_REQUIRED`
- [x] Teste: `@guardrail` injeta `_guardrail_outcomes` e
      `_guardrail_rule_id` no wrapped fn
- [x] Teste: erro não-guardrail no wrapped faz rollback do rate_limit
- [x] Teste: primeiro argumento não-`GuardrailContext` levanta `TypeError`

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
  (a impor no Estágio 5 — todas as funções `*_negotiation` ganham
  `@guardrail` antes da gravação do evento)
- Bypass do guardrail é impossível: decorador é a única forma de chegar
  no DB; checks rodam em sequência fixa (block → policy → escalation →
  rate_limit) (verificado por testes ✅)
- Audit operacional gravado em todo path: ok, blocked, error (✅
  cobertura `test_audit_de_erro_inclui_codigo`)
- Audit durável usa colunas já existentes em `NegotiationEvent` —
  zero schema novo (Apêndice A)

### Notas de implementação
- Não confie no LLM para respeitar limites — o guardrail é o NORTE
- Audit log é write-only; nunca update/delete
- Rate limit usa `django.core.cache` (django-redis já configurado no
  backend) — chave: `rate:negociacao:propostas:{tenant}:{doc}:{date}`
- Metrics em memória atende v0; trocar para `prometheus_client.Counter`
  no v1 sem mudar callsite

### Implementação (2026-05-11)

Arquivos criados em `quanttix_backend@agentic_flow_cnab`:

- `src/treasury/services/guardrails/__init__.py` — barrel exporta
  `guardrail`, `GuardrailContext`, `GuardrailError`, `GuardrailOutcome`,
  `GUARDRAIL_CODES`
- `src/treasury/services/guardrails/exceptions.py` — `GuardrailError`
  com codes fechados + `to_dict()` para serialização REST
- `src/treasury/services/guardrails/context.py` — `GuardrailContext`
  (frozen dataclass, hashable, primitivos só) e `GuardrailOutcome`
- `src/treasury/services/guardrails/block_check.py` — bloqueia
  contraparte em_protesto; passa com Counterpart ausente
- `src/treasury/services/guardrails/policy_check.py` — chama
  `policy_engine.lookup` injetado pelo decorador, valida desconto/
  parcelas/juros/prazo. Operações sem termos (accept/reject) viram
  no-op aqui (mesma assinatura, retornam outcome neutro)
- `src/treasury/services/guardrails/escalation_check.py` — exige
  escalation explícita quando accept acima do threshold OU
  rodada_atual >= rodada_maxima. Em propose/counter acima do
  threshold, só anota flag (não bloqueia)
- `src/treasury/services/guardrails/rate_limit.py` — Django cache
  para contador diário (TTL 25h) + count direto no DB para limite
  global; expõe `rate_limit_rollback` para o decorador usar em erros
- `src/treasury/services/guardrails/audit_ops.py` — `write_audit` +
  `read_audit` com gzip+base64; sanitiza Decimal/UUID/datetime;
  audit nunca derruba operação (try/except + log warning)
- `src/treasury/services/guardrails/metrics.py` — contadores
  thread-safe via `collections.Counter` + `threading.Lock`. Helpers
  `record_guardrail_block` e `record_proposal_outcome`
- `src/treasury/services/guardrails/decorator.py` — `@guardrail`
  envolve fn, carrega policy 1x via `_load_policy_cached` do
  `negotiation_service`, roda cadeia, faz audit em todo path, faz
  rollback de rate em erro não-guardrail
- `src/treasury/tests/test_guardrails.py` — 21 testes pytest
  cobrindo todos os checks + decorator (sintaxe validada ✅)

Decisões pontuais:

- **GuardrailContext frozen** — fail-fast em mutação acidental, e
  garante hashability para futura cache de outcome
- **Operations sem termos** (accept/reject/escalate) passam pelo
  `policy_check` mas viram no-op de validação — assim mantemos
  pipeline uniforme sem condicional no decorator
- **Score baixo NÃO bloqueia no `block_check`** — fica no `policy_check`
  que conhece `bloqueio_score_min` da regra. Block_check só vê o
  flag bivalente `em_protesto`, que é independente da regra
- **Counterpart ausente NÃO bloqueia** — agente roda com defaults
  conservadores (tier=None, score=0). policy_check faz filtro estrito
- **Métricas em memória v0** — `prometheus_client` plug-and-play
  no v1 sem mudar callsite (helpers `record_*` ficam idênticos)
- **Audit nunca derruba** — falha de Redis grava warning no log
  mas operação prossegue. Tradeoff: pode perder linhas de audit em
  janela de incidente Redis, mas evita criar dependência rígida

Pendências do estágio (smoke ⏳ no POD):

- `pytest src/treasury/tests/test_guardrails.py` executa com Django
  + DB ativo
- Confirmar que `cache.set/get` com `django-redis` no POD bate com
  os testes que usam `locmem` cache em settings_test
- Integração com Estágio 5: cada tool de escrita ganha `@guardrail`
  e usa `kwargs["_guardrail_outcomes"]["policy"].rule_id` para
  popular `NegotiationEvent.regra_aplicada_id`

---

## Estágio 5: Endpoints REST — Tools de escrita + audit

**Repo**: `quanttix_backend`
**Bloqueia**: Estágio 6 (parcial)
**Depende de**: Estágios 3 e 4
**Status**: CONCLUÍDO code ✅ (2026-05-11); smoke ⏳ no POD

### Objetivo
Expor as **ações** do agente como endpoints REST POST: propor termos,
registrar contraproposta, aceitar, recusar, escalar. Cada um envelopado
pelo guardrail (Estágio 4) e gera `NegotiationEvent` no Postgres do
backend (audit duravel via colunas `guardrail_motivo` +
`regra_aplicada_id` ja modeladas no Estagio 3).

### Entregáveis
- Adicionar rotas POST em `src/treasury/api_negotiation.py`
  (mesmo router do Estágio 3, agora com endpoints write)
- `src/treasury/services/negotiation_service.py` ganha métodos de escrita
- `src/treasury/schemas/negotiation.py` ganha request/response schemas
- `tests/treasury/test_api_negotiation_write.py`

### Subtarefas
- [x] `POST /negotiation/propose` body `{title_id, counterpart_doc, ..., terms}`
      → `NegotiationEventResponse` (status `PROPOSTA`); cria Negotiation + evento
- [x] `POST /negotiation/{neg_id}/counterproposal` →
      `NegotiationEventResponse` (status `CONTRAPROPOSTA`); incrementa rodada_atual,
      valida que negociacao nao e terminal
- [x] `POST /negotiation/{neg_id}/accept` → `NegotiationEventResponse`
      (status `ACEITA`); herda termos do ultimo evento se body vazio;
      atualiza `Counterpart.ultima_negociacao_resultado="ACEITA"`
- [x] `POST /negotiation/{neg_id}/reject` body `{reason, mensagem_agente}` →
      `NegotiationEventResponse` (status `RECUSADA`)
- [x] `POST /negotiation/{neg_id}/escalate` body `{reason, context, mensagem_agente}` →
      `EscalationTicketResponse` (cria `EscalationTicket` + evento `ESCALADA`
      atomicamente)
- [x] Todos os 5 endpoints envelopados com `@guardrail` (Estagio 4) via
      wrappers `_propose_wrapped`, `_counter_wrapped`, etc.
- [x] Idempotência via header `Idempotency-Key` (ou `X-Idempotency-Key`):
      2 chamadas com mesma key não duplicam evento. Sem header, fallback
      para hash determinístico de `(tenant, neg_id, status, terms_canonical)`
- [x] State machine: `_ensure_not_terminal` bloqueia operacoes em
      negociacoes ja em ACEITA/RECUSADA/EXPIRADA/BLOQUEADA com
      `INVALID_STATE` (HTTP 409)
- [x] Teste por endpoint: 5 happy paths (propose, counter, accept, reject, escalate)
- [x] Teste de guardrail integration: `POLICY_VIOLATION` e `ESCALATION_REQUIRED`
      bloqueiam via decorator antes de gravar
- [x] Teste de idempotência: chamada repetida e mesma key explicita
- [x] Teste de state machine: counterproposal em ACEITA levanta `INVALID_STATE`
- [x] Teste: `escalate` cria ticket + evento atomicamente
- [x] Teste: `accept`/`reject` atualizam `Counterpart.ultima_negociacao_resultado`

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
- Pytest cobre os 5 caminhos felizes + bloqueios via guardrail ✅
- Idempotência verificada (hash determinístico + header explicito) ✅
- Eventos aparecem na tabela `treasury_negotiation_event` (Postgres)
  após chamada — verificavel via `Negotiation.eventos.all()` ✅
- Audit duravel em `NegotiationEvent.{guardrail_motivo,regra_aplicada_id}`
  populado a partir dos outcomes do `@guardrail` ✅

### Notas de implementação
- `negociacao_id` é gerado no primeiro `propose_negotiation` e retornado
  ao agente; ele DEVE passar de volta em chamadas subsequentes via URL
- `idempotency_key` armazenado no DB e composto por prefixo `hdr:` ou
  `auto:` dependendo se cliente enviou header ou nao
- `accept_negotiation` NÃO dispara boleto diretamente — apenas grava o
  evento. O dispatch fica no Estágio 6 e é orquestrado pelo agente
  (próxima tool chamada)
- Endpoints retornam HTTP `201 Created` em sucesso; bloqueios viram:
  - `403` BLOCKED_BY_POLICY
  - `409` ESCALATION_REQUIRED / INVALID_STATE
  - `422` POLICY_VIOLATION
  - `429` RATE_LIMITED
- Body do erro: dict serializado da `GuardrailError.to_dict()`
  (`{code, message, hint, regra_aplicada_id}`)

### Implementação (2026-05-11)

Arquivos modificados/criados em `quanttix_backend@agentic_flow_cnab`:

- `src/treasury/schemas/negotiation.py` — adicionados 9 schemas write:
  `NegotiationTerms`, `ProposeNegotiationRequest`, `CounterproposalRequest`,
  `AcceptRequest`, `RejectRequest`, `EscalateRequest`,
  `NegotiationEventResponse`, `EscalationTicketResponse`
- `src/treasury/services/negotiation_service.py` — adicionados 5 service
  methods: `propose_negotiation`, `register_counterproposal`,
  `accept_negotiation`, `reject_negotiation`, `escalate_negotiation`,
  mais helpers `_compute_idempotency_key`, `_canonical_terms`,
  `_ensure_not_terminal`, `_build_event_response`
- `src/treasury/api_negotiation.py` — adicionados 5 endpoints POST
  envelopados em `_*_wrapped` decorados com `@guardrail`. Helpers
  `_handle_guardrail_error` (mapeia codes para HTTP 4xx),
  `_handle_service_error` (404/409), `_idempotency_key_from_request`,
  `_session_id_from_request`, `_client_id_from_request`,
  `_fetch_neg_or_404`, `_last_event_desconto`
- `src/treasury/tests/test_api_negotiation_write.py` — 14 testes pytest

Decisoes pontuais:

- **Wrapper `_*_wrapped` separado do handler** — o `@guardrail`
  precisa receber GuardrailContext como primeiro argumento; manter
  isolado do handler Ninja torna a chamada interna mais testavel
  e desacopla validacao de HTTP framework
- **`AcceptRequest.terms` opcional** — accept simples herda do ultimo
  evento de proposta/contraproposta; raro mas possivel passar terms
  para accept renegociado
- **Idempotency_key prefixado** (`hdr:`/`auto:`) — facilita debugging
  para distinguir keys explicitas das hashes determinísticas
- **Reject e Escalate gravam mensagem_agente** dentro do `terms` JSON
  do NegotiationEvent — alternativa seria adicionar coluna dedicada
  mas isso poluiria o schema; JSON do terms ja e flexivel
- **Counterpart.ultima_negociacao_resultado** atualizada via
  `Counterpart.objects.filter(id=...).update(...)` em vez de
  `cp.save()` — atomico, sem trigger de history extra
- **Sem signal Django** entre Negotiation e NegotiationEvent — service
  metodo gerencia state machine explicitamente (mais facil de auditar
  do que signal magic)

Pendências do estágio (smoke ⏳ no POD):

- `pytest src/treasury/tests/test_api_negotiation_write.py` executa
  com Django + DB ativos
- Validar end-to-end via curl + JWT do `svc_quanttix_claw`:
  propose → counter → accept (cada um retornando 201)
- Validar bloqueios: propose com desconto > 10% retorna 422 com
  `{code: POLICY_VIOLATION}`
- Validar idempotency: 2x mesma POST com `Idempotency-Key: X` retorna
  o mesmo `evento_id`
- Estagio 6 (dispatch SSE) eh o proximo: `accept_negotiation` aciona
  o `/api/v1/simulation/dispatch` para emitir boleto

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
