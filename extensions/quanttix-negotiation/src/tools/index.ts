import { Type, type Static } from "@sinclair/typebox";
import type {
  AnyAgentTool,
  OpenClawPluginToolContext,
  OpenClawPluginToolFactory,
} from "../../api.js";
import { BackendClient, BackendClientError } from "../backend/client.js";

export type ToolDeps = {
  backend: BackendClient;
};

// TypeBox helpers — keep schemas linter-safe (no Union/anyOf/oneOf).
function stringEnum<T extends readonly string[]>(values: T, description: string) {
  return Type.Unsafe<T[number]>({
    type: "string",
    enum: [...values],
    description,
  });
}

const TipoTituloEnum = ["AR", "AP"] as const;

// ── Schemas ─────────────────────────────────────────────────────────

const GetTitleSchema = Type.Object({
  title_id: Type.String({ description: "Identificador do título (AR ou AP)." }),
});

const GetCounterpartSchema = Type.Object({
  documento: Type.String({ description: "CNPJ/CPF do contraparte (somente dígitos)." }),
  tipo_titulo: stringEnum(TipoTituloEnum, "AR (cliente) ou AP (fornecedor)."),
});

const GetPolicySchema = Type.Object({
  title_id: Type.String({ description: "Identificador do título." }),
  counterpart_doc: Type.String({ description: "CNPJ/CPF do contraparte." }),
});

const ListOpenNegotiationsSchema = Type.Object({
  counterpart_doc: Type.String({ description: "CNPJ/CPF do contraparte." }),
});

const ProposeSchema = Type.Object({
  title_id: Type.String({ description: "Identificador do título." }),
  counterpart_doc: Type.String({ description: "CNPJ/CPF do contraparte." }),
  tipo_titulo: stringEnum(TipoTituloEnum, "AR ou AP."),
  valor_titulo: Type.String({ description: "Valor original do título em reais (string decimal)." }),
  desconto_pct: Type.Optional(
    Type.String({
      description: "Desconto proposto em %, dentro da policy (string decimal, ex: '5.00').",
    }),
  ),
  valor_acordado: Type.Optional(
    Type.String({ description: "Valor acordado em reais (string decimal)." }),
  ),
  novo_vencimento: Type.Optional(Type.String({ description: "Novo vencimento (YYYY-MM-DD)." })),
  mensagem_agente: Type.Optional(
    Type.String({ description: "Mensagem livre para auditoria interna." }),
  ),
});

const CounterproposalSchema = Type.Object({
  negociacao_id: Type.String({ description: "ID da negociação aberta." }),
  desconto_pct: Type.Optional(
    Type.String({ description: "Desconto da contraproposta em % (string decimal)." }),
  ),
  valor_acordado: Type.Optional(Type.String({ description: "Valor acordado em reais." })),
  mensagem_agente: Type.Optional(Type.String({ description: "Texto livre — vai para auditoria." })),
});

const AcceptSchema = Type.Object({
  negociacao_id: Type.String({ description: "ID da negociação a ser aceita." }),
});

const RejectSchema = Type.Object({
  negociacao_id: Type.String({ description: "ID da negociação a ser rejeitada." }),
  reason: Type.String({
    description: "Motivo curto e operacional (ex.: 'contraparte_desinteressada').",
  }),
  mensagem_agente: Type.Optional(
    Type.String({ description: "Mensagem completa do contraparte para auditoria." }),
  ),
});

const EscalateSchema = Type.Object({
  negociacao_id: Type.String({ description: "ID da negociação a ser escalada." }),
  reason: Type.String({ description: "Motivo da escalação (ex.: 'desconto_acima_threshold')." }),
  context: Type.Optional(
    Type.String({ description: "Contexto: últimas mensagens + número proposto." }),
  ),
});

// ── Helpers ─────────────────────────────────────────────────────────

function idem(prefix: string, parts: string[]): string {
  return `${prefix}:${parts.join(":")}:${Date.now()}`;
}

function unwrap<T>(promise: Promise<T>): Promise<T | { _error: string; _code: number | null }> {
  return promise.catch((err: unknown) => {
    if (err instanceof BackendClientError) {
      return { _error: err.message, _code: err.status };
    }
    return { _error: err instanceof Error ? err.message : String(err), _code: null };
  });
}

// ── Tool factories ──────────────────────────────────────────────────

function makeGetTitle(deps: ToolDeps): AnyAgentTool {
  return {
    name: "get_title",
    description: "Read-only lookup of a title (AR or AP) with valor atualizado e juros.",
    parameters: GetTitleSchema,
    async execute(_toolCallId: string, params: unknown) {
      const args = params as Static<typeof GetTitleSchema>;
      return await unwrap(deps.backend.getTitle(args.title_id));
    },
  };
}

function makeGetCounterpart(deps: ToolDeps): AnyAgentTool {
  return {
    name: "get_counterpart",
    description:
      "Read-only lookup of counterpart by document — returns tier, score, histórico, protesto.",
    parameters: GetCounterpartSchema,
    async execute(_toolCallId: string, params: unknown) {
      const args = params as Static<typeof GetCounterpartSchema>;
      return await unwrap(deps.backend.getCounterpart(args.documento, args.tipo_titulo));
    },
  };
}

function makeGetPolicy(deps: ToolDeps): AnyAgentTool {
  return {
    name: "get_negotiation_policy",
    description:
      "PolicyDecision for a title+counterpart: max_desconto_pct, escalation_threshold_pct, allowed.",
    parameters: GetPolicySchema,
    async execute(_toolCallId: string, params: unknown) {
      const args = params as Static<typeof GetPolicySchema>;
      return await unwrap(deps.backend.getPolicy(args.title_id, args.counterpart_doc));
    },
  };
}

function makeListOpenNegotiations(deps: ToolDeps): AnyAgentTool {
  return {
    name: "list_open_negotiations",
    description: "Lista negociações abertas da mesma contraparte (evita propostas concorrentes).",
    parameters: ListOpenNegotiationsSchema,
    async execute(_toolCallId: string, params: unknown) {
      const args = params as Static<typeof ListOpenNegotiationsSchema>;
      return await unwrap(deps.backend.listOpenNegotiations(args.counterpart_doc));
    },
  };
}

function makePropose(deps: ToolDeps): AnyAgentTool {
  return {
    name: "propose_negotiation",
    description:
      "Cria a proposta inicial no backend (evento PROPOSTA). Sempre dentro de max_desconto_pct da policy. Envelopado pelo guardrail no backend.",
    parameters: ProposeSchema,
    async execute(_toolCallId: string, params: unknown) {
      const args = params as Static<typeof ProposeSchema>;
      return await unwrap(
        deps.backend.proposeNegotiation({
          titleId: args.title_id,
          counterpartDoc: args.counterpart_doc,
          tipoTitulo: args.tipo_titulo,
          valorTitulo: args.valor_titulo,
          descontoPct: args.desconto_pct,
          valorAcordado: args.valor_acordado,
          novoVencimento: args.novo_vencimento,
          mensagemAgente: args.mensagem_agente,
          idempotencyKey: idem("propose", [args.title_id, args.counterpart_doc]),
        }),
      );
    },
  };
}

function makeCounterproposal(deps: ToolDeps): AnyAgentTool {
  return {
    name: "counterproposal_negotiation",
    description:
      "Registra contraproposta recebida da contraparte. Limita a policy.defaults.rodada_maxima (5).",
    parameters: CounterproposalSchema,
    async execute(_toolCallId: string, params: unknown) {
      const args = params as Static<typeof CounterproposalSchema>;
      return await unwrap(
        deps.backend.counterpropose({
          negociacaoId: args.negociacao_id,
          descontoPct: args.desconto_pct,
          valorAcordado: args.valor_acordado,
          mensagemAgente: args.mensagem_agente,
          idempotencyKey: idem("counter", [args.negociacao_id]),
        }),
      );
    },
  };
}

function makeAccept(deps: ToolDeps): AnyAgentTool {
  return {
    name: "accept_negotiation",
    description:
      "Fecha a negociação como ACEITA. Só chamar após confirmação numérica explícita do contraparte. Aciona dispatch do boleto.",
    parameters: AcceptSchema,
    async execute(_toolCallId: string, params: unknown) {
      const args = params as Static<typeof AcceptSchema>;
      return await unwrap(
        deps.backend.acceptNegotiation({
          negociacaoId: args.negociacao_id,
          idempotencyKey: idem("accept", [args.negociacao_id]),
        }),
      );
    },
  };
}

function makeReject(deps: ToolDeps): AnyAgentTool {
  return {
    name: "reject_negotiation",
    description: "Fecha a negociação como RECUSADA com motivo operacional curto.",
    parameters: RejectSchema,
    async execute(_toolCallId: string, params: unknown) {
      const args = params as Static<typeof RejectSchema>;
      return await unwrap(
        deps.backend.rejectNegotiation({
          negociacaoId: args.negociacao_id,
          reason: args.reason,
          mensagemAgente: args.mensagem_agente,
          idempotencyKey: idem("reject", [args.negociacao_id]),
        }),
      );
    },
  };
}

function makeEscalate(deps: ToolDeps): AnyAgentTool {
  return {
    name: "escalate_negotiation",
    description:
      "Escala a negociação para revisão humana (acima do threshold, condição não prevista, etc.).",
    parameters: EscalateSchema,
    async execute(_toolCallId: string, params: unknown) {
      const args = params as Static<typeof EscalateSchema>;
      return await unwrap(
        deps.backend.escalateNegotiation({
          negociacaoId: args.negociacao_id,
          reason: args.reason,
          context: args.context,
          idempotencyKey: idem("escalate", [args.negociacao_id]),
        }),
      );
    },
  };
}

// ── Factory: registers all 9 tools at plugin load ───────────────────

export function createNegotiationToolFactories(deps: ToolDeps): OpenClawPluginToolFactory[] {
  const tools: ((d: ToolDeps) => AnyAgentTool)[] = [
    makeGetTitle,
    makeGetCounterpart,
    makeGetPolicy,
    makeListOpenNegotiations,
    makePropose,
    makeCounterproposal,
    makeAccept,
    makeReject,
    makeEscalate,
  ];
  return tools.map((make) => (_ctx: OpenClawPluginToolContext) => make(deps));
}
