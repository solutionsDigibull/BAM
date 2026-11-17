// A fake of the project goal API (#720) for unit tests: GET / PUT /projects/{id}/goal with the server's rules (the ETag
// is the project's row version; PUT needs a current If-Match: 428 without, 412 when stale). Any other request is a 404.
import { vi } from "vitest";

import { API_BASE } from "@/providers/data-provider";

export interface FakeGoalApi {
  goals: Map<string, { goal: string | null; version: number }>;
  calls: { method: string; project: string; ifMatch: string | null; body?: unknown }[];
  /** Answer every request to this project with this status (a server that refuses). */
  failing: Map<string, number>;
}

const json = (status: number, body: unknown, etag?: string) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status < 400 ? "application/json" : "application/problem+json", ...(etag ? { etag } : {}) },
  });

/** What the server sends with a goal (#798): the fixed template, the goal in I. Two rows are enough for the client. */
export const fakeIsatvon = (goal: string | null) =>
  goal
    ? [
        { key: "I", label: "Instructions", text: `You are an FDE Agent operating under ISATVON constraints.\nGoal: ${goal}` },
        { key: "N", label: "Notification", text: "Webhook/alert channels + trigger list" },
      ]
    : [];

export function installGoalApi(): FakeGoalApi {
  const api: FakeGoalApi = { goals: new Map(), calls: [], failing: new Map() };
  const route = new RegExp(`^${API_BASE}/projects/([^/]+)/goal$`);
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const m = route.exec(String(url));
    const method = init.method ?? "GET";
    if (!m) return json(404, { status: 404, code: "NOT_FOUND", title: "Not found" });
    const project = m[1]!;
    const headers = new Headers(init.headers);
    const ifMatch = headers.get("if-match");
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    api.calls.push({ method, project, ifMatch, body });
    const fail = api.failing.get(project);
    if (fail) return json(fail, { status: fail, code: "UNAVAILABLE", title: "The server refused" });
    const row = api.goals.get(project) ?? { goal: null, version: 1 };
    api.goals.set(project, row);
    const etag = `"${row.version}"`;
    if (method === "GET") return json(200, { project_id: project, goal: row.goal, isatvon: fakeIsatvon(row.goal) }, etag);
    if (!ifMatch) return json(428, { status: 428, code: "PRECONDITION_REQUIRED", title: "If-Match required" });
    if (ifMatch !== etag) return json(412, { status: 412, code: "PRECONDITION_FAILED", title: "Stale" });
    const goal = (body as { goal: string | null }).goal;
    if (goal !== row.goal) {
      row.goal = goal;
      row.version += 1;
    }
    return json(200, { project_id: project, goal: row.goal, isatvon: fakeIsatvon(row.goal) }, `"${row.version}"`);
  });
  return api;
}
