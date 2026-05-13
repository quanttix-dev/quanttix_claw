# Exemplo — Negociação AR com aceitação

Cenário: cliente padrão (`tier=padrao`) com título vencido há 12 dias.
Policy ativa = `AR-padrao` (max_desconto_pct=10%, escalation_threshold_pct=7%).
Negociação fecha em 3 rodadas com desconto de 5%.

Dados (mock, alinhados com `policy.draft.yaml`):

- `title_id`: `SE1-001`
- `valor_original`: R$ 10.000,00
- `valor_atualizado`: R$ 10.450,00 (com juros até hoje)
- `dt_vencimento`: 2026-04-28
- `dias_atraso`: 12
- `counterpart_doc`: 12.345.678/0001-99
- `counterpart_nome`: Cliente Exemplo S.A.
- `tier`: padrao
- `score`: 720

---

### Turno 0 — sistema entrega o handoff

Mensagem injetada pelo session binding (não vai pro chat):

```
[handoff] negociacao_id=neg_abc123 | title_id=SE1-001 | tipo_titulo=AR
| chat_id=987654321 | mensagem_inicial_sugerida=null
```

### Turno 1 — agente carrega contexto

Tool calls (paralelos, sem texto ao usuário ainda):

```
get_title(title_id="SE1-001")
get_counterpart(documento="12.345.678/0001-99")
get_negotiation_policy(title_id="SE1-001", counterpart_doc="12.345.678/0001-99")
list_open_negotiations(counterpart_doc="12.345.678/0001-99")
```

Retornos resumidos:

- `TitleDetails`: AR, R$ 10.450,00, vencido há 12 dias, status `EM_ATRASO`
- `CounterpartProfile`: tier=padrao, score=720, em_protesto=false, histórico OK
- `PolicyDecision`: allowed=true, matched_rule_id=`AR-padrao`,
  max_desconto_pct=10.0, escalation_threshold_pct=7.0, max_parcelas=1,
  max_prazo_dias_extra=30
- `list_open_negotiations`: vazia

### Turno 2 — saudação

Agente → Cliente (Telegram):

> "Bom dia, senhor responsável pela Cliente Exemplo S.A. Aqui é da
> Quanttix em nome da [Empresa Credora]. Estou entrando em contato
> sobre o título **SE1-001**, no valor atualizado de **R$ 10.450,00**,
> com vencimento em **28/04/2026** (12 dias em atraso). Podemos
> conversar sobre uma forma de regularizar?"

### Turno 3 — cliente responde

Cliente → Agente:

> "Bom dia, podemos sim. O caixa esse mês ficou apertado, conseguem
> dar 8% de desconto se eu pagar essa semana?"

### Turno 4 — agente avalia e contrapropõe

Análise interna do agente:

- 8% está dentro de `max_desconto_pct=10` ✓
- 8% está **acima** de `escalation_threshold_pct=7` — accept direto
  seria bloqueado pelo guardrail
- Pode contrapropor 7% (no threshold) sem precisar escalar

Tool call:

```
propose_negotiation(
  title_id="SE1-001",
  terms={
    desconto_pct: 7.0,
    valor_acordado: 9718.50,            // 10.450 * 0.93
    num_parcelas: 1,
    novo_vencimento: "2026-05-15",      // ~5 dias úteis
    juros_pct_mes: 0,
    mensagem_agente: "contraproposta inicial a 7%"
  },
  idempotency_key="neg_abc123-propose-1"
)
→ NegotiationEvent(status_evento="PROPOSTA", evento_id="evt_001")
```

Agente → Cliente:

> "Senhor, consigo aprovar internamente um desconto de **7%**, o que
> deixa o valor em **R$ 9.718,50** para pagamento até **15/05/2026**.
> É o limite que posso oferecer diretamente. Podemos fechar nessa
> condição?"

### Turno 5 — cliente aceita verbalmente, com confirmação

Cliente → Agente:

> "Fechado, 9.718,50 até dia 15/05. Manda o boleto."

### Turno 6 — agente confirma por escrito antes do accept

Agente → Cliente:

> "Confirmando para registro: **R$ 9.718,50** com vencimento em
> **15/05/2026**, referente ao título **SE1-001**. Confirma o
> fechamento?"

### Turno 7 — cliente reafirma

Cliente → Agente:

> "Confirmo."

### Turno 8 — agente fecha

Tool call:

```
accept_negotiation(
  neg_id="neg_abc123",
  idempotency_key="neg_abc123-accept-1"
)
→ AcceptResult(status_evento="ACEITA", dt_evento="2026-05-10T14:23:11Z")
```

Agente → Cliente:

> "🤝 Acordo registrado, senhor. Você receberá o boleto atualizado em
> instantes. Obrigado pela atenção e bom dia."

### Turno 9 — dispatch do boleto

O `accept_negotiation` **não** dispara o boleto sozinho. O orquestrador
(quanttix_ai) detecta o evento `ACEITA` e chama
`POST /api/v1/simulation/dispatch` (Estágio 6). O claw recebe os eventos
SSE do DAG e envia a linha digitável quando ela for emitida pelo banco
simulado — isso é tratado em fluxo paralelo, não no system prompt aqui.

---

## Sinais que validam o fluxo

- Saudação chamou os 3 reads em paralelo antes do primeiro texto ✓
- Contraproposta respeitou `escalation_threshold_pct` (7%, não 8%) ✓
- Confirmação por escrito antes de `accept_negotiation` ✓
- `idempotency_key` em todas as escritas ✓
- Nenhuma referência a score, alçada, ou threshold na conversa ✓
- Tom formal-cordial mantido ✓
