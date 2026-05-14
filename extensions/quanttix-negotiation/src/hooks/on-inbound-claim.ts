import type { PluginLogger } from "../../api.js";
import {
  getBindingByChat,
  touchBinding,
  type BindingStoreOptions,
} from "../state/binding-store.js";

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

// Bloco 1 deliverable: hook is wired but DOES NOT claim yet.
// We log when a chat with an active binding receives a message, so we can verify
// the binding store and routing path before flipping to claim+dispatch in Bloco 2.
//
// Future Bloco 2 work (one of):
//   a) return { handled: true } and invoke api.runtime.agent.runEmbeddedPiAgent
//      with the quanttix-negotiation skill (heavy, fully controlled);
//   b) override agentId via openclaw.config.json sessionAgent map (lighter,
//      depends on whether the gateway exposes per-session agent override);
//   c) use before_dispatch + reply text directly (skip the whole agent loop).
export function createInboundClaimHandler(opts: InboundClaimOptions) {
  return async function onInboundClaim(event: InboundEvent): Promise<InboundResult | void> {
    if (event.channel !== "telegram") {
      return;
    }
    const chatId = event.conversationId ?? event.senderId;
    if (!chatId) {
      return;
    }

    const binding = await getBindingByChat(opts.bindingStore, chatId);
    if (!binding) {
      return;
    }

    // Found an active negotiation binding — refresh TTL and log routing intent.
    await touchBinding(opts.bindingStore, chatId);
    opts.logger.info(
      `[quanttix-negotiation][hook] chat=${chatId} has active binding neg=${binding.negociacao_id} tipo=${binding.tipo_titulo} title=${binding.title_id} — would route to agent=${opts.agentId} (Bloco 2 will flip to claim+dispatch)`,
    );

    // Stay non-claiming for now so legacy flow keeps working during shadow rollout.
    return;
  };
}
