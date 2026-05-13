# Exemplo — Negociação AP com contraproposta e escalação

Cenário: fornecedor padrão (`tier=padrao`) cobrando título em dia.
Quanttix pede desconto por pagamento antecipado. Policy ativa =
`AP-padrao` (max_desconto_pct=10%, escalation_threshold_pct=7%).
Fornecedor contrapropõe acima do limite; agente escala.

Dados (mock, alinhados com `policy.draft.yaml`):

- `title_id`: `N1-042`
- `valor_original`: R$ 24.000,00
- `valor_atualizado`: R$ 24.000,00 (em dia)
- `dt_vencimento`: 2026-06-10
- `counterpart_doc`: 98.765.432/0001-11
- `counterpart_nome`: Fornecedor Padrão Ltda.
- `tier`: padrao
- `score`: 800 (não bloqueia, fornecedor padrão `bloqueio_score_min=0`)

---

### Turno 0 — handoff

```
[handoff] negociacao_id=neg_xyz789 | title_id=N1-042 | tipo_titulo=AP
| chat_id=123456789 | mensagem_inicial_sugerida="pedir 8% por antecipo de 20d"
```

Nota: `instructions.mensagem_inicial_sugerida` vem do orquestrador, mas
**o agente ainda valida contra a policy** antes de propor.

### Turno 1 — agente carrega contexto

```
get_title(title_id="N1-042")
get_counterpart(documento="98.765.432/0001-11")
get_negotiation_policy(title_id="N1-042", counterpart_doc="98.765.432/0001-11")
list_open_negotiations(counterpart_doc="98.765.432/0001-11")
```

Retornos:

- AP, R$ 24.000,00, vencimento 10/06/2026, em dia
- tier=padrao, score=800, em_protesto=false
- `PolicyDecision`: max_desconto_pct=10.0, escalation_threshold_pct=7.0
- Sem negociações em aberto

Validação da sugestão do orquestrador: 8% > 7% threshold → agente decide
abrir com 7% (não 8%) para deixar espaço de negociação dentro do escopo
sem precisar escalar logo.

### Turno 2 — saudação + proposta

Tool call:

```
propose_negotiation(
  title_id="N1-042",
  terms={
    desconto_pct: 7.0,
    valor_acordado: 22320.00,           // 24.000 * 0.93
    num_parcelas: 1,
    novo_vencimento: "2026-05-21",      // ~20 dias antes do original
    juros_pct_mes: 0,
    mensagem_agente: "proposta de antecipação com 7%"
  },
  idempotency_key="neg_xyz789-propose-1"
)
→ NegotiationEvent(status_evento="PROPOSTA", evento_id="evt_010")
```

Agente → Fornecedor:

> "Olá, time financeiro da Fornecedor Padrão Ltda. Aqui é da Quanttix
> tratando do título **N1-042**, vencimento em **10/06/2026**.
> Gostaríamos de antecipar o pagamento em ~20 dias, com desconto de
> **7%** — valor de **R$ 22.320,00** quitado até **21/05/2026**.
> Funciona para vocês?"

### Turno 3 — fornecedor contrapropõe

Fornecedor → Agente:

> "Boa tarde. Conseguimos liberar a antecipação, mas com 4% no máximo,
> ou seja, R$ 23.040,00. Aceitam?"

### Turno 4 — agente avalia e contrapropõe

Análise:

- 4% está dentro do que é interessante (Quanttix queria 7%, mas 4%
  ainda é desconto positivo)
- Decisão: o agente **não pode aceitar** sem confirmar que 4% é
  vantagem suficiente — isso é decisão de tesouraria, não do agente.
  Como a contraproposta fica abaixo do que Quanttix pediu, e a policy
  define o **teto** que o agente pode aceitar **do lado fornecedor**
  (`max_desconto_pct=10`), 4% está dentro do permitido.
- Mas o agente AP pede desconto, não aceita-o; o lado fornecedor está
  oferecendo. Isso é uma contraproposta legítima a registrar.

Tool call:

```
counterproposal_negotiation(
  neg_id="neg_xyz789",
  terms={
    desconto_pct: 5.5,                  // meio termo
    valor_acordado: 22680.00,
    num_parcelas: 1,
    novo_vencimento: "2026-05-21",
    mensagem_agente: "contraproposta meio termo 5,5%"
  },
  idempotency_key="neg_xyz789-counter-1"
)
→ NegotiationEvent(status_evento="CONTRAPROPOSTA", evento_id="evt_011")
```

Agente → Fornecedor:

> "Obrigado pela resposta. Conseguimos fechar em **5,5%** de desconto,
> ou seja, **R$ 22.680,00** quitados até **21/05/2026**. É o meio
> termo entre nossa proposta inicial e a de vocês. Fecha nessa?"

### Turno 5 — fornecedor insiste fora do limite

Fornecedor → Agente:

> "Nosso teto interno é 4%. Não conseguimos ir além disso. Aceitam
> ou prefere manter no vencimento original?"

### Turno 6 — agente escala

Análise: o fornecedor cravou 4%. Decisão de aceitar ou manter no
vencimento original é estratégica (caixa, custo de oportunidade) —
não é decisão do agente.

Agente → Fornecedor (mensagem antes da tool):

> "Time financeiro da Fornecedor, essa condição precisa passar por uma
> validação interna aqui. Retomamos o contato em até **48h** com a
> posição final."

Tool call:

```
escalate_negotiation(
  neg_id="neg_xyz789",
  reason="contraparte_alegou_fato_externo",
  context={
    ultimo_pedido_contraparte: "fornecedor cravou 4% como teto interno",
    desconto_pct_pedido: 4.0,
    desconto_pct_que_pediamos: 5.5,
    valor_proposto_fornecedor: 23040.00,
    valor_original: 24000.00,
    economia_em_4pct: 960.00,
    rodada_atual: 2,
    decisao_pendente: "aceitar 4% ou manter vencimento original"
  }
)
→ EscalationTicket(ticket_id="esc_022", criado_em="2026-05-10T15:11:02Z")
```

### Turno 7 — fornecedor pressiona

Fornecedor → Agente:

> "E aí, tem retorno?"

Agente → Fornecedor:

> "Time financeiro, a análise ainda está em andamento. Retorno assim
> que tiver a posição."

### Turno 8 — retomada (mensagem do sistema)

Algumas horas depois, o painel de aprovação humana envia retomada via
session binding:

```
[retomada] ticket=esc_022 | decisao=aceitar | nota="aceitar 4%, ok"
```

### Turno 9 — agente fecha

Tool calls:

```
counterproposal_negotiation(
  neg_id="neg_xyz789",
  terms={
    desconto_pct: 4.0,
    valor_acordado: 23040.00,
    num_parcelas: 1,
    novo_vencimento: "2026-05-21",
    mensagem_agente: "aceitando contraproposta original do fornecedor"
  },
  idempotency_key="neg_xyz789-counter-2"
)
→ status=CONTRAPROPOSTA

accept_negotiation(
  neg_id="neg_xyz789",
  idempotency_key="neg_xyz789-accept-1"
)
→ AcceptResult(status_evento="ACEITA")
```

Agente → Fornecedor:

> "Time financeiro, retomamos com aprovação interna. Fechamos em
> **4% de desconto**, **R$ 23.040,00** quitados até **21/05/2026**.
> Acordo registrado, vamos providenciar o pagamento."

---

## Sinais que validam o fluxo

- Agente respeitou o `escalation_threshold_pct=7%` na proposta inicial ✓
- Contraproposta de 5,5% ficou dentro do limite ✓
- Decisão de aceitar fora do range "ótimo" foi escalada, não tomada ✓
- Nenhuma resposta enquanto o ticket estava em análise ✓
- Confirmação numérica explícita no fecho ✓
- Tom profissional-direto mantido (sem emoji, sem "senhor/senhora",
  empresa-a-empresa) ✓
