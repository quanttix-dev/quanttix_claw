import {
  definePluginEntry,
  type OpenClawPluginApi,
} from "openclaw/plugin-sdk/plugin-entry";

const PROVIDER_ID = "quanttix-planner";
const LOCAL_API_KEY = "local";

export default definePluginEntry({
  id: PROVIDER_ID,
  name: "Quanttix Planner",
  description: "Gemma 4 E2B local planner via llama.cpp (OpenAI-compatible)",
  register(api: OpenClawPluginApi) {
    const baseUrl =
      process.env["CLAW_PLANNER_URL"] ?? "http://localhost:8091/v1";
    const modelId =
      process.env["CLAW_PLANNER_MODEL_ID"] ?? "gemma-4-e2b";

    api.registerProvider({
      id: PROVIDER_ID,
      label: "Quanttix Planner (local)",
      docsPath: "/providers/quanttix-planner",
      auth: [],

      discovery: {
        order: "late",
        run: async () => ({
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
                contextWindow: 32768,
                maxTokens: 4096,
              },
            ],
          },
        }),
      },

      resolveSyntheticAuth: () => ({
        apiKey: LOCAL_API_KEY,
        source: "quanttix-planner (local llama.cpp)",
        mode: "api-key" as const,
      }),
    });
  },
});
