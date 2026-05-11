# Agente Autônomo de Negociação — Quanttix

Plano de implementação do agente conversacional autônomo de negociação que
roda dentro do `quanttix_claw` (fork OpenClaw), integrado com
`quanttix_backend` (REST tools + dispatch + guardrails) e o pipeline de simulação
CNAB em `quanttix_data_eng`.

## Arquivos nesta pasta

- `PLAN.md` — Plano dividido em 8 estágios com subtarefas marcáveis.
  Atualizar checkbox a cada subtarefa concluída. **Fonte de verdade.**
- `README.md` — este arquivo. Navegação e instruções de retomada.

## Visão de uma frase

Transformar o OpenClaw num agente conversacional autônomo capaz de negociar
títulos AR/AP via Telegram, dentro de guardrails determinísticos, gravando
eventos no data lake e disparando o pipeline de cobrança simulada.

## Arquitetura

```
+========================================+      +========================================+
|  Server X (POD com GPUs)               |      |  Private network (VPC do backend)      |
|                                        |      |                                        |
|  +-------------------+                 |      |  +-----------------+                   |
|  | quanttix_ai       |                 |      |  | Frontend        |                   |
|  | (Qwen3-32B)       |---+ HTTP        |      |  | (gestor)        |                   |
|  | @Quanttix_bot     |   | localhost   |      |  +--------+--------+                   |
|  +---------+---------+   |             |      |           |                            |
|            ^             v             |      |           v                            |
|            |  +-------------------+    |      |  +-----------------+                   |
|            |  | quanttix_claw     |    |      |  | quanttix_backend|                   |
|            |  | (Gemma 4 E2B)     |    |      |  | (Django + Ninja)|                   |
|            |  | @Negotiator_bot   |----+----- REST + JWT ----------+                   |
|            |  +---------+---------+    |      |  +--------+--------+                   |
|            |            |              |      |           |                            |
|            |            v Telegram     |      |           v                            |
|            |  +-------------------+    |      |  +-----------------+                   |
|            |  | Contraparte       |    |      |  | Postgres +      |                   |
|            +--| (cliente/fornec.) |    |      |  | Trino/Iceberg   |                   |
|               +-------------------+    |      |  +-----------------+                   |
|                                        |      |           |                            |
|  +-------------------+                 |      |           v                            |
|  | Redis (state)     |                 |      |  +-----------------+                   |
|  +-------------------+                 |      |  | Airflow DAGs    |                   |
+========================================+      |  | (sim_* dispatch)|                   |
                                                |  +-----------------+                   |
                                                +========================================+

Comunicacao:
  - quanttix_ai <-> quanttix_claw  : HTTP localhost (handoff direto)
  - claw -> contraparte            : Telegram API (canal externo)
  - (ai | claw) <-> backend        : HTTPS + JWT service account (cross-zone)
  - claw <-> Redis                 : local ao Server X (state, binding)

Bots Telegram (dois, funcoes distintas):
  @Quanttix_bot            -> canal INTERNO  (quanttix_ai <-> gestor)
  @Quanttix_Negotiator_bot -> canal EXTERNO  (quanttix_claw <-> contraparte)
```

## Repositórios envolvidos

| Repo | Branch | O que muda |
|---|---|---|
| `quanttix_claw` | `developer` | Extension `quanttix-negotiation`, skill, policy, allowlist hooks |
| `quanttix_backend` | `agentic_flow_cnab` | REST negotiation endpoints, policy engine, dispatch, guardrails, audit |
| `quanttix_ai` | `developer_cpp` | `NegotiationOrchestrator` vira fallback (não é mais primário) |
| `quanttix_data_eng` | `developer_flow` | Já contém DAGs `sim_*` — sem mudanças nesta fase |

## Como retomar este plano

Se a sessão atual for interrompida (créditos, troca de conta, novo dia):

1. Abra `PLAN.md` neste diretório.
2. Procure pela primeira subtarefa não marcada (`- [ ]`).
3. Verifique o "Critério de aceite" do estágio dela.
4. Implemente **apenas essa subtarefa**.
5. Marque `- [x]` e atualize o **Status** do estágio se mudou
   (NÃO INICIADO → EM ANDAMENTO → CONCLUÍDO).
6. Commit com mensagem: `negotiation-agent: stage-N <nome-curto-da-subtarefa>`.

**Regras importantes ao retomar:**

- Não pule estágios. A ordem é dependência real, não preferência.
- Estágio 1 (Policy) bloqueia 2, 3, 4, 5. Não tente codar a skill antes da policy estar pronta.
- Estágio 6 (Dispatch endpoint) bloqueia o fechamento real do ciclo de boleto.
- Se uma subtarefa estiver mal definida, prefira atualizar o PLAN.md primeiro (com nota explicando) antes de codar.
- Mudanças de contrato (assinatura de endpoint REST, payload do dispatch) precisam atualizar a seção "Contratos" do estágio antes de implementar.

## Convenções de status

No PLAN.md cada estágio tem um campo **Status** com três valores:

- `NÃO INICIADO` — nenhuma subtarefa concluída
- `EM ANDAMENTO` — pelo menos uma subtarefa concluída, mas há subtarefas pendentes
- `CONCLUÍDO` — todas as subtarefas marcadas E critério de aceite verificado

## Pré-requisitos antes de começar a codar

- [x] Os 4 repos commitados e empurrados (2026-05-10).
- [x] Bots Telegram: dois separados (`@Quanttix_bot` interno do
      `quanttix_ai`, `@Quanttix_Negotiator_bot` externo do `quanttix_claw`).
- [x] Transporte de tools: REST + JWT no backend, HTTP localhost no
      handoff AI ↔ Claw (chamada direta, sem broker).

---

Versão inicial do plano: 2026-05-10.
