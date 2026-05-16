import type { PluginLogger } from "../../api.js";
import {
  getBindingByChat,
  touchBinding,
  type BindingStoreOptions,
} from "../state/binding-store.js";
import { getRedis } from "../state/redis-client.js";

export type InboundClaimOptions = {
  bindingStore: BindingStoreOptions;
  logger: PluginLogger;
  agentId: string;
};

type InboundEvent = {
  conversationId?: string;
  channel: string;
  senderId?: string;
  content: string;
};

type InboundResult = { handled: boolean };

// Bridge key used by before_prompt_build to recover the inbound chat_id.
// Same shape used in both hooks — agent+channel is stable across the SDK's
// two hook contexts (PluginHookMessageContext vs PluginHookAgentContext).
export const hookCtxRedisKey = (agentId: string, channelId: string) =>
  `qx:negotiation:hookctx:${agentId}:${channelId}`;
const HOOK_CTX_TTL_SECONDS = 60;

// Bloco 1 deliverable: hook is wired but DOES NOT claim yet.
// We log when a chat with an active binding receives a message and bridge the
// chat_id to before_prompt_build via Redis so that hook can inject the active
// negotiation context (the PluginHookAgentContext does not carry conversationId).
export function createInboundClaimHandler(opts: InboundClaimOptions) {
  return async function onInboundClaim(event: InboundEvent): Promise<InboundResult | void> {
    if (event.channel !== "telegram") {
      return;
    }
    const chatId = event.conversationId ?? event.senderId;
    if (!chatId) {
      return;
    }

    // Always publish the chat_id to the hookctx bridge — even if there's no
    // binding yet — so before_prompt_build can match. TTL is short so a stale
    // value doesn't outlive the turn.
    const redis = getRedis({ url: opts.bindingStore.redisUrl, logger: opts.logger });
    if (redis) {
      try {
        await redis.set(
          hookCtxRedisKey(opts.agentId, event.channel),
          chatId,
          "EX",
          HOOK_CTX_TTL_SECONDS,
        );
      } catch (err) {
        opts.logger.warn(`[quanttix-negotiation][hook] hookctx publish failed: ${String(err)}`);
      }
    }

    const binding = await getBindingByChat(opts.bindingStore, chatId);
    if (!binding) {
      return;
    }

    // Found an active negotiation binding — refresh TTL and log routing intent.
    await touchBinding(opts.bindingStore, chatId);
    opts.logger.info(
      `[quanttix-negotiation][hook] chat=${chatId} has active binding neg=${binding.negociacao_id} tipo=${binding.tipo_titulo} title=${binding.title_id} — context published for before_prompt_build`,
    );

    // Stay non-claiming for now so legacy flow keeps working during shadow rollout.
    return;
  };
}
