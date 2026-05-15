import { definePluginEntry, type OpenClawPluginApi } from "./api.js";
import { BackendClient } from "./src/backend/client.js";
import { createInboundClaimHandler } from "./src/hooks/on-inbound-claim.js";
import { createStartRouteHandler } from "./src/http/start-route.js";
import { type BindingStoreOptions } from "./src/state/binding-store.js";
import { closeRedis } from "./src/state/redis-client.js";
import { createNegotiationToolFactories } from "./src/tools/index.js";

type PluginMode = "disabled" | "shadow" | "active";

type BackendConfig = {
  baseUrl: string;
  email: string;
  password: string;
  tenantId?: string;
  empresaId?: string;
  filialId?: string;
};

type ResolvedConfig = {
  mode: PluginMode;
  redisUrl: string;
  agentId: string;
  bindingTtlSeconds: number;
  telegramBotToken: string;
  backend: BackendConfig;
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

  const backendCfg = (cfg["backend"] as Record<string, unknown> | undefined) ?? {};

  // pluginConfig wins; env vars are fallback. We accept both BACKEND_SVC_* (plugin-native names)
  // and QUANTTIX_API_* (matches the quanttix_ai .env convention) so a single source of truth
  // can be reused without renaming the AI's env file.
  return {
    mode,
    redisUrl:
      readString(cfg["redisUrl"]) ??
      readString(env["QUANTTIX_REDIS_URL"]) ??
      "redis://127.0.0.1:6379",
    agentId: readString(cfg["agentId"]) ?? "quanttix-negotiation",
    bindingTtlSeconds: readNumber(cfg["bindingTtlSeconds"]) ?? 7 * 24 * 60 * 60,
    telegramBotToken:
      readString(cfg["telegramBotToken"]) ?? readString(env["TELEGRAM_BOT_TOKEN"]) ?? "",
    backend: {
      baseUrl:
        readString(backendCfg["baseUrl"]) ??
        readString(env["QUANTTIX_BACKEND_BASE_URL"]) ??
        readString(env["QUANTTIX_API_URL"]) ??
        "",
      email:
        readString(backendCfg["email"]) ??
        readString(env["BACKEND_SVC_EMAIL"]) ??
        readString(env["QUANTTIX_API_EMAIL"]) ??
        "",
      password:
        readString(backendCfg["password"]) ??
        readString(env["BACKEND_SVC_PASSWORD"]) ??
        readString(env["QUANTTIX_API_PASSWORD"]) ??
        "",
      tenantId:
        readString(backendCfg["tenantId"]) ??
        readString(env["BACKEND_TENANT_ID"]) ??
        readString(env["QUANTTIX_API_TENANT_ID"]),
      empresaId:
        readString(backendCfg["empresaId"]) ??
        readString(env["BACKEND_EMPRESA_ID"]) ??
        readString(env["QUANTTIX_API_EMPRESA_ID"]),
      filialId:
        readString(backendCfg["filialId"]) ??
        readString(env["BACKEND_FILIAL_ID"]) ??
        readString(env["QUANTTIX_API_FILIAL_ID"]),
    },
  };
}

export default definePluginEntry({
  id: "quanttix-negotiation",
  name: "Quanttix Negotiation",
  description: "Autonomous AR/AP negotiation agent over Telegram (replaces legacy handoff_server)",
  register(api: OpenClawPluginApi) {
    const cfg = resolveConfig(api);

    api.logger.info(
      `[quanttix-negotiation] register mode=${cfg.mode} backend=${cfg.backend.baseUrl || "<unset>"} redis=${cfg.redisUrl} agentId=${cfg.agentId} backendEmailSet=${cfg.backend.email ? "yes" : "no"} telegramTokenSet=${cfg.telegramBotToken ? "yes" : "no"}`,
    );

    if (cfg.mode === "disabled") {
      return;
    }

    if (!cfg.backend.baseUrl || !cfg.backend.email || !cfg.backend.password) {
      api.logger.warn(
        "[quanttix-negotiation] backend baseUrl/email/password missing — plugin staying registered but /start and tools will fail at first call. Set plugins.entries.quanttix-negotiation.config.backend.{baseUrl,email,password} in openclaw.json.",
      );
    }

    const backend = new BackendClient({
      baseUrl: cfg.backend.baseUrl,
      svcEmail: cfg.backend.email,
      svcPassword: cfg.backend.password,
      tenantId: cfg.backend.tenantId,
      empresaId: cfg.backend.empresaId,
      filialId: cfg.backend.filialId,
      logger: api.logger,
    });

    const bindingStore: BindingStoreOptions = {
      redisUrl: cfg.redisUrl,
      logger: api.logger,
      ttlSeconds: cfg.bindingTtlSeconds,
    };

    // HTTP route — entry point called by quanttix_ai to kick off a negotiation.
    // Path matches what the legacy quanttix_ai handoff_client.py posts to:
    // it appends `/negotiation/start` to settings.HANDOFF_BASE_URL, so the
    // plugin must expose `/plugins/quanttix-negotiation/negotiation/start`.
    api.registerHttpRoute({
      path: "/plugins/quanttix-negotiation/negotiation/start",
      auth: "gateway",
      handler: createStartRouteHandler({
        mode: cfg.mode,
        backend,
        bindingStore,
        telegramBotToken: cfg.telegramBotToken,
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
