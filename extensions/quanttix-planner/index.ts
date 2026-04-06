import {
  definePluginEntry,
  type OpenClawPluginApi,
} from "openclaw/plugin-sdk/plugin-entry";

const PROVIDER_ID = "quanttix-planner";
const LOCAL_API_KEY = "local";

type QuanttixPlannerConfig = {
  baseUrl?: string;
  modelId?: string;
};

export default definePluginEntry({
  id: PROVIDER_ID,
  name: "Quanttix Planner",
  description: "Gemma 4 E2B local planner via llama.cpp (OpenAI-compatible)",
  register(api: OpenClawPluginApi) {
    const pluginConfig = (api.pluginConfig ?? {}) as QuanttixPlannerConfig;

    const baseUrl =
      pluginConfig.baseUrl ??
      process.env["CLAW_PLANNER_URL"] ??
      "http://localhost:8091/v1";

    const modelId =
      pluginConfig.modelId ??
      process.env["CLAW_PLANNER_MODEL_ID"] ??
      "gemma-4-e2b";

    api.registerProvider({
      id: PROVIDER_ID,
      label: "Quanttix Planner (local)",
      envVars: [],
      auth: [],

      catalog: {
        run: async (_ctx) => {
          return {
            provider: {
              baseUrl,
              apiKey: LOCAL_API_KEY,
              api: "openai-completions" as const,
              models: [
                {
                  id: modelId,
                  name: "Gemma 4 E2B (Quanttix Planner)",
                  reasoning: false,
                  input: ["text"] as ["text"],
                  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
                  contextWindow: 8192,
                  maxTokens: 2048,
                },
              ],
            },
          };
        },
      },

      resolveSyntheticAuth: () => ({
        apiKey: LOCAL_API_KEY,
        source: "quanttix-planner (local llama.cpp)",
        mode: "api-key" as const,
      }),

      shouldDeferSyntheticProfileAuth: () => true,
    });
  },
});
