import type { Redis } from "ioredis";
import type { PluginLogger } from "../../api.js";
import { getRedis } from "./redis-client.js";

// Keys mirror the legacy Python handoff_server (quanttix-negotiation/handoff_server/session_binding.py)
// so quanttix_ai can keep reading bindings while we run shadow mode.
const _chatKey = (chatId: string) => `chat:tg:${chatId}`;
const _negKey = (negociacaoId: string) => `negociacao:${negociacaoId}:chat`;
const _lockKey = (chatId: string) => `lock:chat:${chatId}`;

const DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60; // 7 days
const LOCK_TTL_SECONDS = 5;

export type Binding = {
  negociacao_id: string;
  counterpart_doc: string;
  counterpart_nome: string;
  tipo_titulo: "AR" | "AP";
  title_id: string;
  channel: "telegram";
  bound_at: string;
  last_touch_at: string;
};

export type BindingStoreOptions = {
  redisUrl: string;
  logger: PluginLogger;
  ttlSeconds?: number;
};

function nowIso(): string {
  return new Date().toISOString();
}

function client(opts: BindingStoreOptions): Redis | null {
  return getRedis({ url: opts.redisUrl, logger: opts.logger });
}

export async function createBinding(
  opts: BindingStoreOptions,
  input: {
    chatId: string;
    negociacaoId: string;
    counterpartDoc: string;
    counterpartNome: string;
    tipoTitulo: "AR" | "AP";
    titleId: string;
  },
): Promise<boolean> {
  const r = client(opts);
  if (!r) return false;

  const ttl = opts.ttlSeconds ?? DEFAULT_TTL_SECONDS;
  const now = nowIso();
  const chatPayload: Binding = {
    negociacao_id: input.negociacaoId,
    counterpart_doc: input.counterpartDoc,
    counterpart_nome: input.counterpartNome,
    tipo_titulo: input.tipoTitulo,
    title_id: input.titleId,
    channel: "telegram",
    bound_at: now,
    last_touch_at: now,
  };
  const negPayload = { channel: "telegram", chat_id: input.chatId };
  try {
    await r
      .multi()
      .set(_chatKey(input.chatId), JSON.stringify(chatPayload), "EX", ttl)
      .set(_negKey(input.negociacaoId), JSON.stringify(negPayload), "EX", ttl)
      .exec();
    return true;
  } catch (err) {
    opts.logger.warn(`[quanttix-negotiation] createBinding failed: ${String(err)}`);
    return false;
  }
}

export async function getBindingByChat(
  opts: BindingStoreOptions,
  chatId: string,
): Promise<Binding | null> {
  const r = client(opts);
  if (!r) return null;
  try {
    const raw = await r.get(_chatKey(chatId));
    if (!raw) return null;
    return JSON.parse(raw) as Binding;
  } catch (err) {
    opts.logger.warn(`[quanttix-negotiation] getBindingByChat failed: ${String(err)}`);
    return null;
  }
}

export async function touchBinding(opts: BindingStoreOptions, chatId: string): Promise<void> {
  const r = client(opts);
  if (!r) return;
  const binding = await getBindingByChat(opts, chatId);
  if (!binding) return;
  binding.last_touch_at = nowIso();
  const ttl = opts.ttlSeconds ?? DEFAULT_TTL_SECONDS;
  try {
    await r
      .multi()
      .set(_chatKey(chatId), JSON.stringify(binding), "EX", ttl)
      .expire(_negKey(binding.negociacao_id), ttl)
      .exec();
  } catch (err) {
    opts.logger.warn(`[quanttix-negotiation] touchBinding failed: ${String(err)}`);
  }
}

export async function releaseBinding(opts: BindingStoreOptions, chatId: string): Promise<void> {
  const r = client(opts);
  if (!r) return;
  const binding = await getBindingByChat(opts, chatId);
  try {
    const tx = r.multi().del(_chatKey(chatId));
    if (binding?.negociacao_id) {
      tx.del(_negKey(binding.negociacao_id));
    }
    await tx.exec();
  } catch (err) {
    opts.logger.warn(`[quanttix-negotiation] releaseBinding failed: ${String(err)}`);
  }
}

export async function tryAcquireLock(opts: BindingStoreOptions, chatId: string): Promise<boolean> {
  const r = client(opts);
  if (!r) return true; // No Redis → best effort, let processing continue.
  try {
    const reply = await r.set(_lockKey(chatId), "1", "EX", LOCK_TTL_SECONDS, "NX");
    return reply === "OK";
  } catch {
    return true;
  }
}

export async function releaseLock(opts: BindingStoreOptions, chatId: string): Promise<void> {
  const r = client(opts);
  if (!r) return;
  try {
    await r.del(_lockKey(chatId));
  } catch {
    // ignore — lock auto-expires via TTL
  }
}
