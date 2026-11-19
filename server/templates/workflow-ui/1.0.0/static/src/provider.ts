import type { DataProvider } from "@refinedev/core";
import { WORKFLOW } from "./workflow.generated";
import { callTool } from "./mcp";

// The Refine data provider of this Workflow. Reads come from the generated workflow description;
// every tool call is a `create` on the "tools" resource and goes to the Workflow's own MCP server.
export const workflowProvider: DataProvider = {
  getApiUrl: () => `mcp://${WORKFLOW.serverName}`,
  getList: async ({ resource }) => {
    if (resource === "skills") return { data: WORKFLOW.skills as any, total: WORKFLOW.skills.length };
    return { data: [], total: 0 };
  },
  getOne: async ({ resource, id }) => {
    if (resource === "skills") return { data: WORKFLOW.skills.find(s => String(s.n) === String(id)) as any };
    return { data: null as any };
  },
  create: async ({ resource, variables }) => {
    if (resource !== "tools") throw new Error(`Unknown resource ${resource}`);
    const { name, arguments: args } = variables as { name: string; arguments: Record<string, unknown> };
    return { data: (await callTool(name, args)) as any };
  },
  update: async () => { throw new Error("Not supported: a Workflow UI only runs tools"); },
  deleteOne: async () => { throw new Error("Not supported: a Workflow UI only runs tools"); },
};
