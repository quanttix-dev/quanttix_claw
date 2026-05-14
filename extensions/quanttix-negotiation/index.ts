import { definePluginEntry, type OpenClawPluginApi } from "./api.js";
import { BackendClient } from "./src/backend/client.js";
import { createInboundClaimHandler } from "./src/hooks/on-inbound-claim.js";
import { createStartRouteHandler } from "./src/http/start-route.js";
import { type BindingStoreOptions } from "./src/state/binding-store.js";
import { closeRedis } from "./src/state/redis-client.js";
import { createNegotiationToolFactories } from "./src/tools/index.js";

type PluginMode = "disabled" | "shadow" | "active";

type ResolvedConfig = {
  mode: PluginMode;
  backendBaseUrl: string;
  redisUrl: string;
  agentId: string;
  bindingTtlSeconds: number;
};

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function readNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function resolveConfig(api: OpenClawPluginApi): ResolvedConfig {
  const cfg = (api.pluginConfig as Record<string, unknown> | undefined) ?? {};
  const env = process.env;

  const modeRaw =
    readString(cfg["mode"]) ?? readString(env["QUANTTIX_NEGOTIATION_PLUGIN_MODE"]) ?? "disabled";
  const mode: PluginMode = modeRaw === "shadow" || modeRaw === "active" ? modeRaw : "disabled";

  return {
    mode,
    backendBaseUrl:
      readString(cfg["backendBaseUrl"]) ??
      readString(env["QUANTTIX_BACKEND_BASE_URL"]) ??
      "http://127.0.0.1:8001",
    redisUrl:
      readString(cfg["redisUrl"]) ??
      readString(env["QUANTTIX_REDIS_URL"]) ??
      "redis://127.0.0.1:6379",
    agentId: readString(cfg["agentId"]) ?? "quanttix-negotiation",
    bindingTtlSeconds: readNumber(cfg["bindingTtlSeconds"]) ?? 7 * 24 * 60 * 60,
  };
}

export default definePluginEntry({
  id: "quanttix-negotiation",
  name: "Quanttix Negotiation",
  description: "Autonomous AR/AP negotiation agent over Telegram (replaces legacy handoff_server)",
  register(api: OpenClawPluginApi) {
    const cfg = resolveConfig(api);

    api.logger.info(
      `[quanttix-negotiation] register mode=${cfg.mode} backend=${cfg.backendBaseUrl} redis=${cfg.redisUrl} agentId=${cfg.agentId}`,
    );

    if (cfg.mode === "disabled") {
      return;
    }

    const env = process.env;
    const backend = new BackendClient({
      baseUrl: cfg.backendBaseUrl,
      svcEmail: env["BACKEND_SVC_EMAIL"] ?? "",
      svcPassword: env["BACKEND_SVC_PASSWORD"] ?? "",
      tenantId: env["BACKEND_TENANT_ID"],
      empresaId: env["BACKEND_EMPRESA_ID"],
      filialId: env["BACKEND_FILIAL_ID"],
      logger: api.logger,
    });

    const bindingStore: BindingStoreOptions = {
      redisUrl: cfg.redisUrl,
      logger: api.logger,
      ttlSeconds: cfg.bindingTtlSeconds,
    };

    // HTTP route — entry point called by quanttix_ai to kick off a negotiation.
    api.registerHttpRoute({
      path: "/plugins/quanttix-negotiation/start",
      auth: "gateway",
      handler: createStartRouteHandler({
        mode: cfg.mode,
        backend,
        bindingStore,
        telegramBotToken: env["TELEGRAM_BOT_TOKEN"] ?? "",
        logger: api.logger,
      }),
    });

    // Tools — the quanttix-negotiation skill (LLM) decides which one to call per turn.
    for (const factory of createNegotiationToolFactories({ backend })) {
      api.registerTool(factory);
    }

    // Inbound hook — non-claim for now, only logs.
    // Bloco 2 will flip this to claim+dispatch once the routing pattern is decided.
    api.on(
      "inbound_claim",
      createInboundClaimHandler({
        bindingStore,
        logger: api.logger,
        agentId: cfg.agentId,
      }),
    );

    // Graceful Redis shutdown when the plugin stops (best-effort).
    if (typeof process.on === "function") {
      process.once("beforeExit", () => {
        void closeRedis();
      });
    }
  },
});
