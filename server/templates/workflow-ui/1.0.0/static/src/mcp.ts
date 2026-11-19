// Minimal MCP client over streamable HTTP for the Workflow's own server. The base URL is empty
// (same origin: the host proxies the Workflow server) unless the host sets VITE_WORKFLOW_SERVER_URL.
// Nothing else is ever contacted.
const BASE: string = (import.meta.env.VITE_WORKFLOW_SERVER_URL as string | undefined) ?? "";
const ENDPOINT = `${BASE}/mcp`;
const ACCEPT = "application/json, text/event-stream";

export class ServerError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
  get forbidden(): boolean {
    return this.status === 401 || this.status === 403;
  }
}

let session: Promise<string | null> | null = null;
let next = 1;

function parse(text: string, type: string): any {
  if (!type.includes("text/event-stream")) return JSON.parse(text);
  const data = text.split("\n").filter(l => l.startsWith("data:")).map(l => l.slice(5).trim());
  return JSON.parse(data[data.length - 1] ?? "null");
}

async function post(body: unknown, sid: string | null): Promise<Response> {
  const headers: Record<string, string> = { "content-type": "application/json", accept: ACCEPT };
  if (sid) headers["mcp-session-id"] = sid;
  const res = await fetch(ENDPOINT, { method: "POST", headers, body: JSON.stringify(body) });
  if (!res.ok) throw new ServerError(`The workflow server answered ${res.status}`, res.status);
  return res;
}

function open(): Promise<string | null> {
  if (session === null) {
    session = (async () => {
      const init = {
        jsonrpc: "2.0", id: next++, method: "initialize",
        params: { protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "workflow-ui", version: "1" } },
      };
      const res = await post(init, null);
      const sid = res.headers.get("mcp-session-id");
      await res.text();
      await (await post({ jsonrpc: "2.0", method: "notifications/initialized" }, sid)).text();
      return sid;
    })();
    session.catch(() => { session = null; });
  }
  return session;
}

export async function callTool(name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const sid = await open();
  const res = await post({ jsonrpc: "2.0", id: next++, method: "tools/call", params: { name, arguments: args } }, sid);
  const message = parse(await res.text(), res.headers.get("content-type") ?? "");
  if (message?.error) throw new ServerError(String(message.error.message ?? "The tool failed"), 500);
  const result = message?.result;
  if (result?.isError) throw new ServerError(String(result.content?.[0]?.text ?? "The tool failed"), 500);
  if (result?.structuredContent) return result.structuredContent as Record<string, unknown>;
  const text = result?.content?.[0]?.text;
  try { return JSON.parse(text) as Record<string, unknown>; } catch { return { result: text ?? null }; }
}
