import { Redis } from "ioredis";
import type { PluginLogger } from "../../api.js";

let _client: Redis | null = null;
let _attempted = false;

export type RedisClientOptions = {
  url: string;
  logger: PluginLogger;
};

// Lazy singleton. Connection failures degrade gracefully (binding-store falls back to no-op),
// matching the legacy session_binding.py behaviour where the negotiation flow must survive Redis hiccups.
export function getRedis(opts: RedisClientOptions): Redis | null {
  if (_client !== null) {
    return _client;
  }
  if (_attempted) {
    return null;
  }
  _attempted = true;
  try {
    const client = new Redis(opts.url, {
      connectTimeout: 2000,
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
      lazyConnect: false,
    });
    client.on("error", (err) => {
      opts.logger.warn(`[quanttix-negotiation] redis error: ${String(err)}`);
    });
    _client = client;
    return _client;
  } catch (err) {
    opts.logger.warn(`[quanttix-negotiation] redis init failed: ${String(err)}`);
    return null;
  }
}

export async function closeRedis(): Promise<void> {
  if (_client) {
    try {
      await _client.quit();
    } catch {
      // ignore — best-effort shutdown
    }
    _client = null;
  }
  _attempted = false;
}
