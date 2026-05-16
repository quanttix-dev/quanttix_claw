import type { PluginLogger } from "../../api.js";
import {
  getBindingByChat,
  type Binding,
  type BindingStoreOptions,
} from "../state/binding-store.js";
import { getRedis } from "../state/redis-client.js";
import { hookCtxRedisKey } from "./on-inbound-claim.js";

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

// Best-effort chat_id extraction. The shape of session messages and ctx isn't part of the
// stable plugin SDK; some sessions collapse to "main" dmScope so we have to look in multiple
// places. We try, in order:
//   1) ctx.sessionKey "agent:<id>:telegram:<kind>:<chat_id>" (precise, dmScope!=main)
//   2) Any object in event.messages whose metadata or top-level fields mention a Telegram chat_id
//   3) Sole active binding in Redis (only when there's exactly one — single-tenant smoke)
function chatIdFromMessages(messages: unknown[]): string | null {
  for (const m of messages) {
    if (!m || typeof m !== "object") continue;
    const obj = m as Record<string, unknown>;
    // Scan candidate shapes without committing to a single one.
    const candidates: unknown[] = [
      obj["chat_id"],
      obj["chatId"],
      obj["peer"],
      (obj["metadata"] as Record<string, unknown> | undefined)?.["chat_id"],
      (obj["metadata"] as Record<string, unknown> | undefined)?.["chatId"],
      (obj["metadata"] as Record<string, unknown> | undefined)?.["peerId"],
      (obj["context"] as Record<string, unknown> | undefined)?.["chat_id"],
    ];
    for (const c of candidates) {
      if (typeof c === "string" && c.length > 0) return c;
      if (typeof c === "number") return String(c);
      if (c && typeof c === "object" && "id" in (c as Record<string, unknown>)) {
        const id = (c as Record<string, unknown>)["id"];
        if (typeof id === "string" && id.length > 0) return id;
        if (typeof id === "number") return String(id);
      }
    }
  }
  return null;
}

async function chatIdFromHookCtxBridge(
  opts: BeforePromptBuildOptions,
  agentId: string,
  channelId: string,
): Promise<string | null> {
  const redis = getRedis({ url: opts.bindingStore.redisUrl, logger: opts.logger });
  if (!redis) return null;
  try {
    const v = await redis.get(hookCtxRedisKey(agentId, channelId));
    return v && v.length > 0 ? v : null;
  } catch (err) {
    opts.logger.warn(`[quanttix-negotiation][hook] hookctx read failed: ${String(err)}`);
    return null;
  }
}

export function createBeforePromptBuildHandler(opts: BeforePromptBuildOptions) {
  return async function onBeforePromptBuild(
    event: { prompt: string; messages: unknown[] },
    ctx: { agentId?: string; channelId?: string; sessionKey?: string },
  ): Promise<{ prependSystemContext: string } | void> {
    if (ctx?.agentId !== opts.agentId) return;
    if (ctx?.channelId !== "telegram") return;

    let chatId = extractTelegramChatId(ctx.sessionKey);
    let source = "sessionKey";

    if (!chatId) {
      chatId = chatIdFromMessages(event.messages);
      if (chatId) source = "messages";
    }

    if (!chatId) {
      // Third fallback: chat_id published by on-inbound-claim into Redis under
      // a key derived from (agentId, channelId) — the only two fields shared
      // between PluginHookMessageContext (inbound) and PluginHookAgentContext
      // (this hook). TTL is short, so it only matches if inbound_claim ran in
      // the same turn.
      chatId = await chatIdFromHookCtxBridge(opts, ctx.agentId, ctx.channelId);
      if (chatId) source = "hookctx-bridge";
    }

    if (!chatId) {
      const firstMsg = event.messages[0];
      const sample =
        firstMsg && typeof firstMsg === "object"
          ? Object.keys(firstMsg as Record<string, unknown>).join(",")
          : typeof firstMsg;
      opts.logger.warn(
        `[quanttix-negotiation][hook] could not extract chat_id (sessionKey=${ctx.sessionKey}, msgCount=${event.messages.length}, firstMsgKeys=${sample}) — context injection skipped`,
      );
      return;
    }

    const binding = await getBindingByChat(opts.bindingStore, chatId);
    if (!binding) {
      // Chat without an active binding — let the agent run with its default skill prompt.
      return;
    }

    opts.logger.info(
      `[quanttix-negotiation][hook] injected context (source=${source}) chat=${chatId} neg=${binding.negociacao_id} title=${binding.title_id}`,
    );
    return { prependSystemContext: renderNegotiationContext(binding) };
  };
}
