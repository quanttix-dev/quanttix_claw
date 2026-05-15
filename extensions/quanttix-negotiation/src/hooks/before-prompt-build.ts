import type { PluginLogger } from "../../api.js";
import {
  getBindingByChat,
  type Binding,
  type BindingStoreOptions,
} from "../state/binding-store.js";

export type BeforePromptBuildOptions = {
  bindingStore: BindingStoreOptions;
  logger: PluginLogger;
  agentId: string;
};

// OpenClaw session keys use a colon-delimited form. For Telegram DMs the shape is
//   agent:<agentId>:telegram:<kind>:<chat_id>[:topic:<topicId>]
// We grab the segment two positions after the "telegram" marker so we don't depend on
// agentId length (skill-routed sessions can re-prefix).
function extractTelegramChatId(sessionKey: string | undefined): string | null {
  if (!sessionKey) return null;
  const parts = sessionKey.split(":");
  const tgIdx = parts.indexOf("telegram");
  if (tgIdx < 0 || tgIdx + 2 >= parts.length) return null;
  const id = parts[tgIdx + 2];
  return id && id.length > 0 ? id : null;
}

function renderNegotiationContext(binding: Binding): string {
  // Single big block prepended to the agent system prompt. Static-ish (changes only when the
  // binding itself rotates) so providers can cache it.
  return [
    "## NEGOCIAÇÃO ATIVA — CONTEXTO PRÉ-CARREGADO",
    "",
    "Esta conversa pertence a uma negociação JÁ EM CURSO. Os dados abaixo vêm do",
    "sistema (binding ativo no Redis). **NÃO peça essas informações ao usuário** — use-as.",
    "",
    `- **negociacao_id**: \`${binding.negociacao_id}\``,
    `- **title_id**: \`${binding.title_id}\``,
    `- **tipo_titulo**: ${binding.tipo_titulo}`,
    `- **counterpart_doc**: \`${binding.counterpart_doc}\``,
    `- **counterpart_nome**: ${binding.counterpart_nome}`,
    `- **canal**: ${binding.channel}`,
    `- **negociação aberta em**: ${binding.bound_at}`,
    "",
    "## PAPÉIS NESTA CONVERSA",
    "",
    "- **VOCÊ** é o agente Quanttix, falando em nome da empresa que ofereceu o título.",
    `- O **USUÁRIO** deste chat é a CONTRAPARTE (${binding.counterpart_nome}, doc ${binding.counterpart_doc}).`,
    "- A 1ª mensagem (com valor do título e proposta inicial de desconto) **já foi enviada**",
    "  à contraparte pelo orquestrador, antes de você assumir o turno. Quando ela responde,",
    "  está respondendo à proposta inicial. **Não pergunte os dados do título de novo.**",
    "",
    "## REGRAS CRÍTICAS DE TOOL-CALLING",
    "",
    "- NÃO chame `propose_negotiation` — a proposta inicial já foi registrada no /start.",
    `- Se a contraparte aceitar → \`accept_negotiation\` com \`negociacao_id="${binding.negociacao_id}"\``,
    `- Se ela fizer contraproposta → \`counterproposal_negotiation\` com \`negociacao_id="${binding.negociacao_id}"\``,
    `- Se ela recusar → \`reject_negotiation\` com \`negociacao_id="${binding.negociacao_id}"\` + motivo curto.`,
    `- Se a contraproposta exceder o limite da policy (consulte \`get_negotiation_policy\`)`,
    `  → \`escalate_negotiation\` com \`negociacao_id="${binding.negociacao_id}"\`.`,
    "- Em qualquer chamada de write tool, reuse o `negociacao_id` acima — não crie outra negociação.",
    "",
  ].join("\n");
}

export function createBeforePromptBuildHandler(opts: BeforePromptBuildOptions) {
  return async function onBeforePromptBuild(
    _event: { prompt: string; messages: unknown[] },
    ctx: { agentId?: string; channelId?: string; sessionKey?: string },
  ): Promise<{ prependSystemContext: string } | void> {
    if (ctx?.agentId !== opts.agentId) return;
    if (ctx?.channelId !== "telegram") return;

    const chatId = extractTelegramChatId(ctx.sessionKey);
    if (!chatId) {
      opts.logger.warn(
        `[quanttix-negotiation][hook] before_prompt_build skipped — could not parse chat_id from sessionKey=${ctx.sessionKey}`,
      );
      return;
    }

    const binding = await getBindingByChat(opts.bindingStore, chatId);
    if (!binding) {
      // Chat without an active binding — let the agent run with its default skill prompt.
      return;
    }

    opts.logger.info(
      `[quanttix-negotiation][hook] before_prompt_build injected context chat=${chatId} neg=${binding.negociacao_id} title=${binding.title_id}`,
    );
    return { prependSystemContext: renderNegotiationContext(binding) };
  };
}
