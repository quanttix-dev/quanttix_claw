# Escalação para humano

Quando a negociação sai do que você pode resolver autonomamente, você
escala para validação humana via `escalate_negotiation`. A escalação
gera um ticket na fila de aprovação interna; **a partir desse momento
você não responde mais a contraparte até receber sinal externo de
retomada**.

## Gatilhos de escalação

| Situação                                                                                                      | Detecção                                               |
| ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| Proposta da contraparte excede `max_desconto_pct`                                                             | Você comparou e o valor está acima                     |
| Proposta dentro do limite mas acima de `escalation_threshold_pct`                                             | Cálculo trivial; accept seria bloqueado pelo guardrail |
| Pedido de parcelamento quando `max_parcelas=1`                                                                | Policy não permite                                     |
| Pedido de prazo acima de `max_prazo_dias_extra`                                                               | Policy não permite                                     |
| Atingiu `rodada_maxima` (5) sem acordo                                                                        | Contador interno da sessão                             |
| Tool de escrita retornou `ESCALATION_REQUIRED`                                                                | Erro estruturado                                       |
| Contraparte alega fato externo verificável que muda o caso (ação judicial, mudança de razão social, falência) | Julgamento humano necessário                           |

## Mensagem ao contraparte (antes de chamar a tool)

**AR (formal-cordial)**:

> "Senhor(a) [Nome], essa condição precisa de uma confirmação interna
> do nosso time. Retorno com uma resposta firme em até
> **[expiracao_proposta_horas]h** (até **[dt_limite_retorno]**).
> Agradeço a paciência."

**AP (profissional-direto)**:

> "Time financeiro da [Fornecedor], essa condição precisa passar por
> uma validação interna aqui. Retomamos o contato em até
> **[expiracao_proposta_horas]h** com a posição final."

Após enviar, **não responda mais nesta sessão** até receber retomada.
Se a contraparte mandar mensagem antes da retomada, responda só com:

> "Senhor(a) [Nome], a análise ainda está em andamento. Retorno assim
> que tiver a posição."

Não revele para quem foi escalado, qual o tempo restante, ou o motivo
exato — apenas que está em análise.

## Chamada da tool

```jsonc
escalate_negotiation({
  "neg_id": "neg_abc123",
  "reason": "desconto_acima_do_limite",        // código curto, kebab/snake
  "context": {
    "ultimo_pedido_contraparte": "...",       // texto literal
    "valor_pedido": "R$ 12.000,00",
    "desconto_pct_pedido": 15.0,
    "max_desconto_pct_policy": 10.0,
    "rodada_atual": 3,
    "historico_mensagens_n": 6                // ou inline as últimas N
  }
})
```

Códigos de `reason` padronizados (use exatamente um destes — o painel
de aprovação humana filtra por eles):

- `desconto_acima_do_limite`
- `parcelamento_fora_policy`
- `prazo_acima_do_limite`
- `rodada_maxima_atingida`
- `contraparte_alegou_fato_externo`
- `inconsistencia_dados_titulo`
- `bloqueio_solicitou_revisao` (contraparte pediu revisão depois de
  receber bloqueio inicial)

## Aviso ao supervisor interno

O `escalate_negotiation` é responsável por criar o ticket; o
agente **não envia mensagem direta ao supervisor**. O painel de
aprovação humana lê a fila e o supervisor responde por lá. Você
recebe o desbloqueio via retomada externa (binding do chat
Redis ganha sinal de "retomar com proposta autorizada X").

## Retomada após aprovação

Quando o supervisor humano aprova/recusa, a retomada chega como
mensagem do sistema injetada no contexto (mecanismo do Estágio 7 —
session binding). Você reage assim:

- **Aprovado** com proposta nova → envie a proposta autorizada ao
  contraparte, registre via `propose_negotiation` ou
  `counterproposal_negotiation` conforme estado.
- **Recusado** → siga para `reject_negotiation` com a frase de bloqueio
  elegante do SKILL.md.
- **Sem resposta até expiração** → a negociação expira naturalmente
  por `defaults.expiracao_proposta_horas`; você não precisa fazer nada
  ativo, mas se a contraparte voltar a falar, responda:
  > "Senhor(a) [Nome], a proposta expirou. Caso ainda haja interesse,
  > posso abrir uma nova análise — confirma?"
