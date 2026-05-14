export {
  definePluginEntry,
  type AnyAgentTool,
  type OpenClawPluginApi,
  type OpenClawPluginToolContext,
  type OpenClawPluginToolFactory,
} from "openclaw/plugin-sdk/plugin-entry";

import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";

// PluginLogger is not part of the public SDK type surface, so we derive it from OpenClawPluginApi.
export type PluginLogger = OpenClawPluginApi["logger"];
